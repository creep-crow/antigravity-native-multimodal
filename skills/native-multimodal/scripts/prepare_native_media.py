#!/usr/bin/env python3
"""Prepare media files for Antigravity's native multimodal read_file tool.

Subcommands:
    inspect <path>
        Inspect container, streams, codecs, duration, and byte size, and report
        whether read_file supports the file directly or needs normalize/slice.
    normalize <path> [--out OUT] [--max-mb N]
        Losslessly remux (-c copy) when codecs are already native-compatible,
        or transcode into native .mp4 (H.264 yuv420p + AAC), .mp3/.wav, or .png
        strictly under --max-mb while preserving both visual motion and audio.
    slice <path> [--chunk-sec 180] [--overlap-sec 3] [--start T] [--end T]
                 [--burn-timecode] [--max-mb N] [--out-dir DIR]
        Split long or oversized video/audio into native .mp4/.mp3 segments
        (with optional burned-in global HH:MM:SS timecode) for parallel read_file.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

# Ensure UTF-8 I/O on Windows regardless of console codepage.
for _stream in (sys.stdout, sys.stderr):
    if _stream is not None and hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

# ---------------------------------------------------------------------------
# Capability constants (empirically verified on Antigravity + Gemini 4)
# ---------------------------------------------------------------------------
NATIVE_VIDEO_EXTS = {".mp4", ".m4v", ".webm", ".mov", ".mkv", ".avi"}
NATIVE_VIDEO_CODECS = {"h264", "vp8", "vp9", "mpeg4", "av1"}
NATIVE_VIDEO_AUDIO_CODECS = {"aac", "mp3", "opus", "vorbis"}
NATIVE_PIX_FMTS = {"yuv420p", "yuvj420p", "nv12"}

# Live Gemini uses explicit audio MIME allowlist; ReadFileTool rejects .flac/.aac/.aiff/.wma on Windows.
NATIVE_AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".ogg", ".opus"}
NATIVE_AUDIO_CODECS = {"mp3", "pcm_s16le", "pcm_s24le", "pcm_f32le", "opus", "vorbis", "aac"}

# .ico passes local image/* and crashes Gemini API; .avif/.heic/.heif lack Windows registry MIME.
NATIVE_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff"}
NATIVE_PDF_EXTS = {".pdf"}

# ReadFileTool hard ceiling: 100 MiB (104,857,600 bytes). Valid .mp4 up to 80 MiB verified.
HARD_LIMIT_BYTES = 104_857_600
SAFE_DIRECT_MAX_MB = 90.0
DEFAULT_TARGET_MB = 45.0

DEFAULT_MAX_MB: dict[str, float] = {
    "video": DEFAULT_TARGET_MB,
    "audio": DEFAULT_TARGET_MB,
    "image": DEFAULT_TARGET_MB,
    "pdf": SAFE_DIRECT_MAX_MB,
    "unknown": DEFAULT_TARGET_MB,
}

KNOWN_VIDEO_EXTS = {
    ".mp4", ".mov", ".webm", ".mkv", ".avi", ".flv", ".wmv",
    ".m4v", ".ts", ".mts", ".m2ts", ".3gp", ".mpeg", ".mpg", ".ogv",
}
KNOWN_AUDIO_EXTS = {
    ".mp3", ".wav", ".ogg", ".flac", ".aac", ".m4a", ".wma",
    ".opus", ".aiff", ".aif", ".amr", ".ac3", ".caf",
}
KNOWN_IMAGE_EXTS = {
    ".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif",
    ".tiff", ".heic", ".heif", ".avif", ".ico", ".tga",
}

FONT_CANDIDATES = (
    "C:/Windows/Fonts/consola.ttf",
    "C:/Windows/Fonts/arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/System/Library/Fonts/Menlo.ttc",
)


class MediaPrepError(RuntimeError):
    """Raised when media inspection or conversion fails with an actionable error."""


def require_binary(name: str) -> str:
    """Return full path to ffmpeg/ffprobe via env overrides, PATH, or standard locations."""
    env_override = os.environ.get(f"{name.upper()}_PATH")
    if env_override:
        p = Path(os.path.expanduser(env_override.strip()))
        if p.is_file():
            return str(p)

    ffmpeg_dir = os.environ.get("AGY_FFMPEG_DIR")
    if ffmpeg_dir:
        base_dir = Path(os.path.expanduser(ffmpeg_dir.strip()))
        candidates = (base_dir / f"{name}.exe", base_dir / name) if os.name == "nt" else (base_dir / name, base_dir / f"{name}.exe")
        for cand in candidates:
            if cand.is_file():
                return str(cand)

    found = shutil.which(name)
    if found:
        return found

    if os.name == "nt":
        fallback_paths = (
            Path(rf"C:\ffmpeg\bin\{name}.exe"),
            Path.home() / "scoop" / "shims" / f"{name}.exe",
        )
    else:
        fallback_paths = (
            Path(f"/opt/homebrew/bin/{name}"),
            Path(f"/usr/local/bin/{name}"),
            Path(f"/usr/bin/{name}"),
        )
    for candidate in fallback_paths:
        if candidate.is_file():
            return str(candidate)

    raise MediaPrepError(f"Required executable '{name}' not found on PATH.")


def run_cmd(cmd: list[str], timeout: int = 600) -> subprocess.CompletedProcess[str]:
    """Run a subprocess safely with UTF-8 decoding and no stdin prompt."""
    return subprocess.run(
        cmd,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )


def parse_timestamp(value: str | float | int | None) -> float | None:
    """Parse seconds or HH:MM:SS / MM:SS string into float seconds."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    if ":" not in text:
        try:
            return float(text)
        except ValueError as exc:
            raise MediaPrepError(f"Invalid timestamp '{value}'. Expected seconds or HH:MM:SS.") from exc
    parts = text.split(":")
    if len(parts) > 3:
        raise MediaPrepError(f"Invalid timestamp '{value}'.")
    try:
        nums = [float(p) for p in parts]
    except ValueError as exc:
        raise MediaPrepError(f"Invalid timestamp '{value}'.") from exc
    total = 0.0
    for n in nums:
        total = total * 60.0 + n
    return total


def format_tc(seconds: float, compact: bool = False) -> str:
    """Format seconds as HH:MM:SS (or HHhMMmSSs when compact=True)."""
    sec_int = max(0, int(math.floor(seconds)))
    h = sec_int // 3600
    m = (sec_int % 3600) // 60
    s = sec_int % 60
    if compact:
        return f"{h:02d}h{m:02d}m{s:02d}s" if h > 0 else f"{m:02d}m{s:02d}s"
    return f"{h:02d}:{m:02d}:{s:02d}"


def parse_fps(raw: str | None) -> float | None:
    """Parse ffprobe r_frame_rate / avg_frame_rate strings like '30000/1001'."""
    if not raw or raw in ("0/0", "N/A"):
        return None
    if "/" in raw:
        num, den = raw.split("/", 1)
        try:
            d = float(den)
            return round(float(num) / d, 3) if d > 0 else None
        except ValueError:
            return None
    try:
        return round(float(raw), 3)
    except ValueError:
        return None


def probe_media(path: Path) -> dict[str, Any]:
    """Probe file metadata via ffprobe (or lightweight inspection for PDF)."""
    if not path.exists():
        raise MediaPrepError(f"File not found: {path}")
    if not path.is_file():
        raise MediaPrepError(f"Path is not a file: {path}")

    ext = path.suffix.lower()
    size_bytes = path.stat().st_size
    size_mb = round(size_bytes / (1024 * 1024), 4)

    if ext in NATIVE_PDF_EXTS:
        header = path.read_bytes()[:8]
        return {
            "path": str(path),
            "extension": ext,
            "size_bytes": size_bytes,
            "size_mb": size_mb,
            "modality": "pdf",
            "format_name": "pdf" if header.startswith(b"%PDF-") else "unknown_pdf",
            "duration_sec": None,
            "streams": [],
            "has_video": False,
            "has_audio": False,
        }

    ffprobe = require_binary("ffprobe")
    cmd = [
        ffprobe,
        "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]
    proc = run_cmd(cmd, timeout=30)
    if proc.returncode != 0 or not proc.stdout.strip():
        modality = guess_modality_by_ext(ext)
        return {
            "path": str(path),
            "extension": ext,
            "size_bytes": size_bytes,
            "size_mb": size_mb,
            "modality": modality,
            "format_name": None,
            "duration_sec": None,
            "streams": [],
            "has_video": modality == "video",
            "has_audio": modality == "audio",
            "probe_error": proc.stderr.strip() or "ffprobe returned no data",
        }

    raw = json.loads(proc.stdout)
    fmt = raw.get("format") or {}
    raw_streams = raw.get("streams") or []

    streams: list[dict[str, Any]] = []
    real_video_streams: list[dict[str, Any]] = []
    audio_streams: list[dict[str, Any]] = []

    for st in raw_streams:
        ctype = st.get("codec_type")
        disp = st.get("disposition") or {}
        is_attached_pic = bool(disp.get("attached_pic"))
        entry: dict[str, Any] = {
            "index": st.get("index"),
            "codec_type": ctype,
            "codec_name": st.get("codec_name"),
        }
        if ctype == "video":
            entry["width"] = st.get("width")
            entry["height"] = st.get("height")
            entry["pix_fmt"] = st.get("pix_fmt")
            entry["fps"] = parse_fps(st.get("avg_frame_rate") or st.get("r_frame_rate"))
            entry["attached_pic"] = is_attached_pic
            if not is_attached_pic:
                real_video_streams.append(entry)
        elif ctype == "audio":
            entry["sample_rate"] = int(st["sample_rate"]) if st.get("sample_rate") else None
            entry["channels"] = st.get("channels")
            audio_streams.append(entry)
        if st.get("bit_rate"):
            try:
                entry["bit_rate"] = int(st["bit_rate"])
            except ValueError:
                pass
        streams.append(entry)

    duration_sec: float | None = None
    if fmt.get("duration"):
        try:
            duration_sec = round(float(fmt["duration"]), 3)
        except ValueError:
            duration_sec = None

    format_name = fmt.get("format_name")
    modality = classify_modality(ext, format_name, real_video_streams, audio_streams, duration_sec)

    return {
        "path": str(path),
        "extension": ext,
        "size_bytes": size_bytes,
        "size_mb": size_mb,
        "modality": modality,
        "format_name": format_name,
        "duration_sec": duration_sec,
        "streams": streams,
        "has_video": bool(real_video_streams) and modality == "video",
        "has_audio": bool(audio_streams),
    }


def guess_modality_by_ext(ext: str) -> str:
    """Fallback modality detection using file extension."""
    if ext in KNOWN_VIDEO_EXTS:
        return "video"
    if ext in KNOWN_AUDIO_EXTS:
        return "audio"
    if ext in KNOWN_IMAGE_EXTS:
        return "image"
    if ext in NATIVE_PDF_EXTS:
        return "pdf"
    return "unknown"


def classify_modality(
    ext: str,
    format_name: str | None,
    video_streams: list[dict[str, Any]],
    audio_streams: list[dict[str, Any]],
    duration_sec: float | None,
) -> str:
    """Classify file into video, audio, image, pdf, or unknown."""
    if ext in NATIVE_PDF_EXTS:
        return "pdf"
    if ext in KNOWN_IMAGE_EXTS and not audio_streams:
        return "image"
    fmt_tokens = set((format_name or "").split(","))
    if fmt_tokens & {"image2", "png_pipe", "jpeg_pipe", "webp_pipe", "bmp_pipe", "tiff_pipe"}:
        return "image"
    if video_streams:
        # Animated gif with .gif extension is image-like unless explicitly video;
        # multi-frame video formats or streams with audio or >0.5s duration -> video.
        if ext == ".gif" and not audio_streams:
            return "image"
        if ext in KNOWN_IMAGE_EXTS and not audio_streams and (duration_sec is None or duration_sec <= 0.1):
            return "image"
        return "video"
    if audio_streams:
        return "audio"
    return guess_modality_by_ext(ext)


def evaluate_read_file_support(info: dict[str, Any], max_mb: float | None = None) -> dict[str, Any]:
    """Determine whether read_file supports the media directly, or needs normalize/slice."""
    modality = info["modality"]
    ext = info["extension"]
    size_bytes = info["size_bytes"]
    direct_limit_mb = max_mb if max_mb is not None else SAFE_DIRECT_MAX_MB
    target_limit_mb = max_mb if max_mb is not None else DEFAULT_MAX_MB.get(modality, DEFAULT_TARGET_MB)
    direct_limit_bytes = min(int(direct_limit_mb * 1024 * 1024), HARD_LIMIT_BYTES)
    target_limit_bytes = int(target_limit_mb * 1024 * 1024)
    duration = info.get("duration_sec") or 0.0

    reasons: list[str] = []
    notes: list[str] = []
    streams = info.get("streams") or []
    v_streams = [s for s in streams if s.get("codec_type") == "video" and not s.get("attached_pic")]
    a_streams = [s for s in streams if s.get("codec_type") == "audio"]

    ext_ok = False
    codec_ok = True

    if info.get("probe_error") and modality in ("video", "audio", "image"):
        codec_ok = False
        reasons.append(f"Container metadata probe error ({info['probe_error']}); re-mux/transcode recommended.")

    if modality == "video":
        ext_ok = ext in NATIVE_VIDEO_EXTS
        if not ext_ok:
            reasons.append(f"Container '{ext}' requires bridging to .mp4 (H.264+AAC) for reliable native perception.")
        if v_streams:
            vc = (v_streams[0].get("codec_name") or "").lower()
            pf = (v_streams[0].get("pix_fmt") or "").lower()
            if vc not in NATIVE_VIDEO_CODECS:
                codec_ok = False
                reasons.append(f"Video codec '{vc}' should be transcoded to H.264 (yuv420p).")
            elif pf and pf not in NATIVE_PIX_FMTS:
                codec_ok = False
                reasons.append(f"Pixel format '{pf}' should be normalized to yuv420p.")
        if a_streams:
            ac = (a_streams[0].get("codec_name") or "").lower()
            if ac not in NATIVE_VIDEO_AUDIO_CODECS:
                codec_ok = False
                reasons.append(f"Audio codec '{ac}' should be transcoded to AAC.")
    elif modality == "audio":
        ext_ok = ext in NATIVE_AUDIO_EXTS
        if not ext_ok:
            reasons.append(f"Audio extension '{ext}' is rejected locally on Windows; convert to .mp3, .m4a, or .wav.")
        if a_streams:
            ac = (a_streams[0].get("codec_name") or "").lower()
            if ac not in NATIVE_AUDIO_CODECS:
                codec_ok = False
                reasons.append(f"Audio codec '{ac}' should be converted to MP3/AAC/PCM.")
    elif modality == "image":
        ext_ok = ext in NATIVE_IMAGE_EXTS
        if not ext_ok:
            reasons.append(f"Image extension '{ext}' is unsafe or unsupported; convert to .png.")
    elif modality == "pdf":
        ext_ok = True
    else:
        reasons.append(f"Unrecognized media format '{ext}'.")

    size_ok = size_bytes <= direct_limit_bytes
    if not size_ok:
        reasons.append(
            f"File size {info['size_mb']:.2f} MB exceeds safe limit {direct_limit_mb:.2f} MB (hard cap 100 MiB)."
        )

    long_oversized = (
        modality in ("video", "audio")
        and duration > 300
        and size_bytes > target_limit_bytes
    )

    if modality == "unknown":
        status = "unsupported"
        action = "unsupported"
    elif ext_ok and codec_ok and size_ok and not long_oversized:
        status = "direct"
        action = "read_file"
        if modality in ("video", "audio") and size_bytes > target_limit_bytes:
            notes.append(
                f"Readable directly (<= 100 MiB cap), though > {target_limit_mb:g} MB; slicing is optional for faster upload."
            )
    else:
        needs_slice = long_oversized
        if modality in ("video", "audio") and not size_ok and duration > 0:
            required_total_kbps = (target_limit_bytes * 8 * 0.9) / (duration * 1000.0)
            min_viable_kbps = 140.0 if modality == "video" else 24.0
            if required_total_kbps < min_viable_kbps or duration > 600:
                needs_slice = True
        if needs_slice:
            reasons.append(
                f"Duration ({duration:.1f}s) and size ({info['size_mb']:.2f} MB) are best handled by slicing into <= {target_limit_mb:g} MB segments."
            )
            status = "needs_slice"
            action = "slice"
        else:
            status = "needs_normalize"
            action = "normalize"

    script_ref = Path(__file__).name
    if action == "read_file":
        rec_cmd = f'read_file(Path="{info["path"]}")'
    elif action == "normalize":
        rec_cmd = f'python "{script_ref}" normalize "{info["path"]}" --max-mb {target_limit_mb:g}'
    elif action == "slice":
        rec_cmd = f'python "{script_ref}" slice "{info["path"]}" --chunk-sec 180 --overlap-sec 3 --burn-timecode --max-mb {target_limit_mb:g}'
    else:
        rec_cmd = None

    return {
        **info,
        "hard_limit_bytes": HARD_LIMIT_BYTES,
        "recommended_target_mb": target_limit_mb,
        "max_native_mb": direct_limit_mb,
        "max_native_bytes": direct_limit_bytes,
        "read_file_status": status,
        "reasons": reasons,
        "notes": notes,
        "recommended_action": action,
        "recommended_command": rec_cmd,
    }


def can_lossless_remux_video(info: dict[str, Any]) -> bool:
    """Return True if streams can be copied losslessly into .mp4 (H.264 yuv420p + AAC/MP3)."""
    streams = info.get("streams") or []
    v_streams = [s for s in streams if s.get("codec_type") == "video" and not s.get("attached_pic")]
    a_streams = [s for s in streams if s.get("codec_type") == "audio"]
    if not v_streams:
        return False
    vc = (v_streams[0].get("codec_name") or "").lower()
    pf = (v_streams[0].get("pix_fmt") or "").lower()
    if vc != "h264":
        return False
    if pf and pf not in NATIVE_PIX_FMTS:
        return False
    if a_streams:
        ac = (a_streams[0].get("codec_name") or "").lower()
        if ac not in {"aac", "mp3"}:
            return False
    return True


def can_lossless_remux_audio(info: dict[str, Any], out_ext: str) -> bool:
    """Return True if an audio stream can be stream-copied directly to out_ext."""
    streams = info.get("streams") or []
    a_streams = [s for s in streams if s.get("codec_type") == "audio"]
    if not a_streams:
        return False
    ac = (a_streams[0].get("codec_name") or "").lower()
    if out_ext == ".mp3" and ac == "mp3":
        return True
    if out_ext == ".wav" and ac == "pcm_s16le":
        return True
    if out_ext == ".m4a" and ac == "aac":
        return True
    return False


def ensure_safe_out_path(src: Path, out: Path | None, default_ext: str) -> Path:
    """Compute a non-colliding output path and ensure parent directory exists."""
    if out is None:
        candidate = src.with_name(f"{src.stem}.native{default_ext}")
    else:
        candidate = out
    if candidate.resolve() == src.resolve():
        candidate = candidate.with_name(f"{candidate.stem}.native{candidate.suffix or default_ext}")
    candidate.parent.mkdir(parents=True, exist_ok=True)
    return candidate


def find_drawtext_font() -> str | None:
    """Return an ffmpeg-escaped fontfile path if a known monospace/sans font exists."""
    for candidate in FONT_CANDIDATES:
        if os.path.exists(candidate):
            # Escape Windows drive colon for ffmpeg filtergraph syntax: C:/... -> C\:/...
            return candidate.replace("\\", "/").replace(":", "\\:")
    return None


def build_drawtext_filter(offset_sec: float) -> str:
    """Build a subtle HH:MM:SS timecode overlay filter anchored to global timeline."""
    offset_int = max(0, int(round(offset_sec)))
    font_opt = ""
    font_path = find_drawtext_font()
    if font_path:
        font_opt = f"fontfile='{font_path}':"
    return (
        f"drawtext={font_opt}"
        f"text='%{{pts\\:gmtime\\:{offset_int}\\:%T}}':"
        f"x=12:y=12:fontsize=max(16\\,round(h/22)):"
        f"fontcolor=white:box=1:boxcolor=black@0.55:boxborderw=5"
    )


def build_video_transcode_args(
    duration_sec: float,
    max_bytes: int,
    has_audio: bool,
    attempt: int,
    extra_vf: str | None = None,
) -> list[str]:
    """Build libx264 + aac encoding args calibrated to stay strictly under max_bytes."""
    # Progressive safety tightening across attempts in case mux overhead causes overshoot
    safety = (0.86, 0.72, 0.56, 0.42)[min(attempt, 3)]
    max_widths = (1280, 960, 720, 480)
    max_w = max_widths[min(attempt, 3)]

    vf_parts = [f"scale='min({max_w},iw)':-2", "scale=trunc(iw/2)*2:trunc(ih/2)*2"]
    if extra_vf:
        vf_parts.append(extra_vf)

    args: list[str] = ["-map", "0:v:0"]
    if has_audio:
        args.extend(["-map", "0:a:0?"])

    if duration_sec > 0:
        total_bps = (max_bytes * 8.0 * safety) / duration_sec
        if has_audio:
            audio_kbps = 96 if total_bps > 500_000 else (64 if total_bps > 220_000 else 32)
            audio_bps = audio_kbps * 1000
        else:
            audio_kbps = 0
            audio_bps = 0
        video_bps = max(60_000, int(total_bps - audio_bps))
        maxrate = int(video_bps * 1.15)
        bufsize = int(video_bps * 2.0)
        args.extend([
            "-vf", ",".join(vf_parts),
            "-c:v", "libx264",
            "-preset", "fast",
            "-pix_fmt", "yuv420p",
            "-b:v", str(video_bps),
            "-maxrate", str(maxrate),
            "-bufsize", str(bufsize),
        ])
        if attempt >= 2 or video_bps < 220_000:
            args.extend(["-r", "15" if attempt < 3 else "10"])
    else:
        audio_kbps = 96
        args.extend([
            "-vf", ",".join(vf_parts),
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", str(24 + attempt * 4),
            "-pix_fmt", "yuv420p",
        ])

    if has_audio:
        args.extend(["-c:a", "aac", "-b:a", f"{audio_kbps}k", "-ac", "1" if audio_kbps <= 48 else "2"])
    else:
        args.append("-an")

    args.extend(["-movflags", "+faststart"])
    return args


def encode_under_limit(
    ffmpeg: str,
    input_args: list[str],
    output_path: Path,
    max_bytes: int,
    build_args_fn: Any,
    max_attempts: int = 4,
) -> tuple[Path, int]:
    """Run ffmpeg encoding with automatic tightening retries until output <= max_bytes."""
    last_err = ""
    for attempt in range(max_attempts):
        enc_args = build_args_fn(attempt)
        cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", *input_args, *enc_args, str(output_path)]
        proc = run_cmd(cmd, timeout=600)
        if proc.returncode != 0:
            last_err = proc.stderr.strip()
            continue
        if not output_path.exists():
            last_err = "ffmpeg produced no output file."
            continue
        out_size = output_path.stat().st_size
        if out_size <= max_bytes:
            return output_path, out_size
        last_err = f"Output size {out_size} bytes exceeded limit {max_bytes} bytes (attempt {attempt + 1})."
    raise MediaPrepError(f"Failed to produce output under {max_bytes} bytes: {last_err}")


def normalize_media(path: Path, out: Path | None, max_mb: float | None) -> dict[str, Any]:
    """Normalize video, audio, or image into native-compatible format strictly under max_mb."""
    ffmpeg = require_binary("ffmpeg")
    info = probe_media(path)
    modality = info["modality"]
    limit_mb = max_mb if max_mb is not None else DEFAULT_MAX_MB.get(modality, 20.0)
    limit_bytes = int(limit_mb * 1024 * 1024)
    duration = float(info.get("duration_sec") or 0.0)

    if modality == "pdf":
        raise MediaPrepError("PDF files do not require ffmpeg normalization; use read_file directly or split pages if oversized.")
    if modality == "unknown":
        raise MediaPrepError(f"Unsupported or unrecognized media file: {path}")

    if modality == "video":
        out_path = ensure_safe_out_path(path, out, ".mp4")
        if out_path.suffix.lower() != ".mp4":
            raise MediaPrepError(f"Normalized video output must end with .mp4 (got {out_path.suffix}).")

        # Try lossless remux first when codecs are compatible and source fits under limit.
        if info["size_bytes"] <= limit_bytes and can_lossless_remux_video(info):
            remux_cmd = [
                ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
                "-i", str(path),
                "-map", "0:v:0", "-map", "0:a:0?",
                "-c", "copy", "-movflags", "+faststart",
                str(out_path),
            ]
            proc = run_cmd(remux_cmd, timeout=180)
            if proc.returncode == 0 and out_path.exists() and out_path.stat().st_size <= limit_bytes:
                sz = out_path.stat().st_size
                return {
                    "status": "ok",
                    "modality": "video",
                    "method": "remux",
                    "input": str(path),
                    "output": str(out_path),
                    "duration_sec": duration,
                    "size_bytes": sz,
                    "size_mb": round(sz / (1024 * 1024), 4),
                    "max_mb": limit_mb,
                    "under_limit": True,
                }

        _, sz = encode_under_limit(
            ffmpeg=ffmpeg,
            input_args=["-i", str(path)],
            output_path=out_path,
            max_bytes=limit_bytes,
            build_args_fn=lambda attempt: build_video_transcode_args(
                duration_sec=duration,
                max_bytes=limit_bytes,
                has_audio=bool(info["has_audio"]),
                attempt=attempt,
            ),
        )
        return {
            "status": "ok",
            "modality": "video",
            "method": "transcode",
            "input": str(path),
            "output": str(out_path),
            "duration_sec": duration,
            "size_bytes": sz,
            "size_mb": round(sz / (1024 * 1024), 4),
            "max_mb": limit_mb,
            "under_limit": True,
        }

    if modality == "audio":
        default_ext = ".wav" if (out and out.suffix.lower() == ".wav") else ".mp3"
        out_path = ensure_safe_out_path(path, out, default_ext)
        out_ext = out_path.suffix.lower()
        if out_ext not in {".mp3", ".wav", ".m4a"}:
            raise MediaPrepError(f"Normalized audio output must be .mp3, .wav, or .m4a (got {out_ext}).")

        if info["size_bytes"] <= limit_bytes and can_lossless_remux_audio(info, out_ext):
            remux_cmd = [
                ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
                "-i", str(path), "-vn", "-c:a", "copy",
            ]
            if out_ext == ".m4a" and info["extension"] == ".aac":
                remux_cmd.extend(["-bsf:a", "aac_adtstoasc"])
            remux_cmd.append(str(out_path))
            proc = run_cmd(remux_cmd, timeout=120)
            if proc.returncode == 0 and out_path.exists() and out_path.stat().st_size <= limit_bytes:
                sz = out_path.stat().st_size
                return {
                    "status": "ok",
                    "modality": "audio",
                    "method": "remux",
                    "input": str(path),
                    "output": str(out_path),
                    "duration_sec": duration,
                    "size_bytes": sz,
                    "size_mb": round(sz / (1024 * 1024), 4),
                    "max_mb": limit_mb,
                    "under_limit": True,
                }

        def build_audio_args(attempt: int) -> list[str]:
            if out_ext == ".wav":
                rates = (44100, 22050, 16000, 8000)
                ar = rates[min(attempt, 3)]
                ch = "2" if attempt == 0 else "1"
                return ["-vn", "-c:a", "pcm_s16le", "-ar", str(ar), "-ac", ch]
            safety = (0.88, 0.72, 0.56, 0.40)[min(attempt, 3)]
            target_kbps = 128
            if duration > 0:
                max_kbps = int((limit_bytes * 8.0 * safety) / (duration * 1000.0))
                target_kbps = max(24, min(192, max_kbps))
            else:
                target_kbps = (128, 96, 64, 32)[min(attempt, 3)]
            codec = "aac" if out_ext == ".m4a" else "libmp3lame"
            ch = "1" if target_kbps <= 48 else "2"
            return ["-vn", "-c:a", codec, "-b:a", f"{target_kbps}k", "-ac", ch]

        _, sz = encode_under_limit(
            ffmpeg=ffmpeg,
            input_args=["-i", str(path)],
            output_path=out_path,
            max_bytes=limit_bytes,
            build_args_fn=build_audio_args,
        )
        return {
            "status": "ok",
            "modality": "audio",
            "method": "transcode",
            "input": str(path),
            "output": str(out_path),
            "duration_sec": duration,
            "size_bytes": sz,
            "size_mb": round(sz / (1024 * 1024), 4),
            "max_mb": limit_mb,
            "under_limit": True,
        }

    # Image normalization -> .png (or specified .jpg/.webp)
    out_path = ensure_safe_out_path(path, out, ".png")
    out_ext = out_path.suffix.lower()
    if out_ext not in {".png", ".jpg", ".jpeg", ".webp"}:
        raise MediaPrepError(f"Normalized image output must be .png, .jpg, or .webp (got {out_ext}).")

    def build_image_args(attempt: int) -> list[str]:
        max_dims = (None, 2048, 1280, 720)
        dim = max_dims[min(attempt, 3)]
        args: list[str] = ["-frames:v", "1"]
        if dim is not None:
            args.extend(["-vf", f"scale='min({dim},iw)':'min({dim},ih)':force_original_aspect_ratio=decrease"])
        if out_ext in {".jpg", ".jpeg"}:
            args.extend(["-q:v", str(2 + attempt * 4)])
        return args

    _, sz = encode_under_limit(
        ffmpeg=ffmpeg,
        input_args=["-i", str(path)],
        output_path=out_path,
        max_bytes=limit_bytes,
        build_args_fn=build_image_args,
    )
    return {
        "status": "ok",
        "modality": "image",
        "method": "transcode",
        "input": str(path),
        "output": str(out_path),
        "size_bytes": sz,
        "size_mb": round(sz / (1024 * 1024), 4),
        "max_mb": limit_mb,
        "under_limit": True,
    }


def compute_slice_windows(
    duration_sec: float,
    chunk_sec: float,
    overlap_sec: float,
    start_sec: float | None,
    end_sec: float | None,
) -> tuple[float, float, list[tuple[float, float]]]:
    """Compute (range_start, range_end, [(start, end), ...]) windows with overlap."""
    if chunk_sec <= 0:
        raise MediaPrepError("--chunk-sec must be greater than 0.")
    if overlap_sec < 0 or overlap_sec >= chunk_sec:
        raise MediaPrepError("--overlap-sec must be >= 0 and strictly less than --chunk-sec.")

    r_start = max(0.0, start_sec if start_sec is not None else 0.0)
    r_end = min(duration_sec, end_sec) if end_sec is not None else duration_sec
    if r_start >= r_end:
        raise MediaPrepError(f"Invalid slice window: start ({r_start:.2f}s) >= end ({r_end:.2f}s).")

    step = chunk_sec - overlap_sec
    windows: list[tuple[float, float]] = []
    cur = r_start
    while cur < r_end - 1e-6:
        c_end = min(cur + chunk_sec, r_end)
        windows.append((round(cur, 3), round(c_end, 3)))
        if c_end >= r_end - 1e-6:
            break
        cur = c_end - overlap_sec
        if cur <= windows[-1][0]:
            cur = windows[-1][0] + step
    return round(r_start, 3), round(r_end, 3), windows


def slice_media(
    path: Path,
    chunk_sec: float,
    overlap_sec: float,
    start_raw: str | None,
    end_raw: str | None,
    burn_timecode: bool,
    max_mb: float | None,
    out_dir: Path | None,
) -> dict[str, Any]:
    """Slice video or audio into native .mp4 or .mp3 chunks under max_mb."""
    ffmpeg = require_binary("ffmpeg")
    info = probe_media(path)
    modality = info["modality"]
    if modality not in ("video", "audio"):
        raise MediaPrepError(f"slice supports video and audio files only (got modality='{modality}').")

    duration = info.get("duration_sec")
    if not duration or duration <= 0:
        raise MediaPrepError(f"Could not determine media duration for: {path}")

    limit_mb = max_mb if max_mb is not None else DEFAULT_MAX_MB.get(modality, 20.0)
    limit_bytes = int(limit_mb * 1024 * 1024)

    start_sec = parse_timestamp(start_raw)
    end_sec = parse_timestamp(end_raw)
    r_start, r_end, windows = compute_slice_windows(duration, chunk_sec, overlap_sec, start_sec, end_sec)

    target_dir = out_dir if out_dir is not None else path.parent / f"{path.stem}_slices"
    target_dir.mkdir(parents=True, exist_ok=True)

    chunks_out: list[dict[str, Any]] = []
    can_copy_video = (
        modality == "video"
        and not burn_timecode
        and can_lossless_remux_video(info)
    )
    can_copy_audio = (
        modality == "audio"
        and can_lossless_remux_audio(info, ".mp3")
    )

    for idx, (w_start, w_end) in enumerate(windows):
        w_dur = max(0.1, w_end - w_start)
        label = f"chunk_{idx:03d}_{format_tc(w_start, compact=True)}-{format_tc(w_end, compact=True)}"
        ext = ".mp4" if modality == "video" else ".mp3"
        chunk_path = target_dir / f"{label}{ext}"

        method = "transcode"
        sz = 0
        copied_ok = False

        if can_copy_video or can_copy_audio:
            copy_cmd = [
                ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
                "-ss", f"{w_start:.3f}", "-i", str(path), "-t", f"{w_dur:.3f}",
            ]
            if modality == "video":
                copy_cmd.extend(["-map", "0:v:0", "-map", "0:a:0?", "-c", "copy", "-avoid_negative_ts", "make_zero", "-movflags", "+faststart"])
            else:
                copy_cmd.extend(["-vn", "-c:a", "copy"])
            copy_cmd.append(str(chunk_path))
            proc = run_cmd(copy_cmd, timeout=180)
            if proc.returncode == 0 and chunk_path.exists() and chunk_path.stat().st_size <= limit_bytes:
                sz = chunk_path.stat().st_size
                method = "remux"
                copied_ok = True

        if not copied_ok:
            input_args = ["-ss", f"{w_start:.3f}", "-i", str(path), "-t", f"{w_dur:.3f}"]
            if modality == "video":
                extra_vf = build_drawtext_filter(w_start) if burn_timecode else None
                _, sz = encode_under_limit(
                    ffmpeg=ffmpeg,
                    input_args=input_args,
                    output_path=chunk_path,
                    max_bytes=limit_bytes,
                    build_args_fn=lambda attempt, wd=w_dur, evf=extra_vf: build_video_transcode_args(
                        duration_sec=wd,
                        max_bytes=limit_bytes,
                        has_audio=bool(info["has_audio"]),
                        attempt=attempt,
                        extra_vf=evf,
                    ),
                )
            else:
                def build_chunk_audio(attempt: int, wd: float = w_dur) -> list[str]:
                    safety = (0.88, 0.72, 0.56, 0.40)[min(attempt, 3)]
                    max_kbps = int((limit_bytes * 8.0 * safety) / (wd * 1000.0))
                    target_kbps = max(24, min(128, max_kbps))
                    ch = "1" if target_kbps <= 48 else "2"
                    return ["-vn", "-c:a", "libmp3lame", "-b:a", f"{target_kbps}k", "-ac", ch]

                _, sz = encode_under_limit(
                    ffmpeg=ffmpeg,
                    input_args=input_args,
                    output_path=chunk_path,
                    max_bytes=limit_bytes,
                    build_args_fn=build_chunk_audio,
                )

        chunks_out.append({
            "index": idx,
            "path": str(chunk_path),
            "start_sec": w_start,
            "end_sec": w_end,
            "start_tc": format_tc(w_start),
            "end_tc": format_tc(w_end),
            "duration_sec": round(w_dur, 3),
            "size_bytes": sz,
            "size_mb": round(sz / (1024 * 1024), 4),
            "method": method,
        })

    return {
        "status": "ok",
        "modality": modality,
        "input": str(path),
        "output_dir": str(target_dir),
        "total_duration_sec": duration,
        "range_start_sec": r_start,
        "range_end_sec": r_end,
        "chunk_sec": chunk_sec,
        "overlap_sec": overlap_sec,
        "burn_timecode_applied": bool(burn_timecode and modality == "video"),
        "max_mb": limit_mb,
        "chunk_count": len(chunks_out),
        "chunks": chunks_out,
    }


def build_parser() -> argparse.ArgumentParser:
    """Build CLI argument parser."""
    parser = argparse.ArgumentParser(
        description="Inspect, normalize, and slice media for Antigravity native read_file."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_ins = sub.add_parser("inspect", help="Inspect media streams and read_file compatibility.")
    p_ins.add_argument("path", help="Path to the media file.")
    p_ins.add_argument("--max-mb", type=float, default=None, help="Override native MB limit.")

    p_norm = sub.add_parser("normalize", help="Remux or transcode into a native format under --max-mb.")
    p_norm.add_argument("path", help="Path to the source media file.")
    p_norm.add_argument("--out", default=None, help="Optional output file path.")
    p_norm.add_argument("--max-mb", type=float, default=None, help="Strict output size ceiling in MB.")

    p_slc = sub.add_parser("slice", help="Slice video or audio into native .mp4/.mp3 chunks.")
    p_slc.add_argument("path", help="Path to the source video or audio file.")
    p_slc.add_argument("--chunk-sec", type=float, default=180.0, help="Chunk duration in seconds (default: 180).")
    p_slc.add_argument("--overlap-sec", type=float, default=3.0, help="Overlap between chunks in seconds (default: 3).")
    p_slc.add_argument("--start", default=None, help="Optional start time (seconds or HH:MM:SS).")
    p_slc.add_argument("--end", default=None, help="Optional end time (seconds or HH:MM:SS).")
    p_slc.add_argument("--burn-timecode", action="store_true", help="Burn global HH:MM:SS timecode onto video chunks.")
    p_slc.add_argument("--max-mb", type=float, default=None, help="Strict per-chunk size ceiling in MB.")
    p_slc.add_argument("--out-dir", default=None, help="Directory to write chunks into.")

    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint."""
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        src = Path(args.path).expanduser()
        if args.command == "inspect":
            result = evaluate_read_file_support(probe_media(src), max_mb=args.max_mb)
        elif args.command == "normalize":
            out = Path(args.out).expanduser() if args.out else None
            result = normalize_media(src, out=out, max_mb=args.max_mb)
        elif args.command == "slice":
            out_dir = Path(args.out_dir).expanduser() if args.out_dir else None
            result = slice_media(
                src,
                chunk_sec=args.chunk_sec,
                overlap_sec=args.overlap_sec,
                start_raw=args.start,
                end_raw=args.end,
                burn_timecode=args.burn_timecode,
                max_mb=args.max_mb,
                out_dir=out_dir,
            )
        else:
            raise MediaPrepError(f"Unknown command: {args.command}")
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except MediaPrepError as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 1
    except Exception as exc:
        print(json.dumps({"status": "error", "error": f"Unexpected error: {exc}"}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
