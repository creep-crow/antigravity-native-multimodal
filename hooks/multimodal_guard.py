#!/usr/bin/env python3
"""PreToolUse hook for Antigravity native multimodal media perception and auto-bridging."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import sys
from typing import Any

# ---------------------------------------------------------------------------
# Capability Matrix Constants (synced from mm_probe/capability_matrix.json)
# ---------------------------------------------------------------------------

NATIVE_VIDEO_EXTS = {".mp4", ".m4v", ".webm", ".mov", ".mkv", ".avi"}
NATIVE_VIDEO_CODECS = {"h264", "vp8", "vp9", "mpeg4"}
NATIVE_VIDEO_AUDIO_CODECS = {"aac", "mp3", "opus", "vorbis", "pcm_s16le"}

NATIVE_AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".ogg", ".opus"}
NATIVE_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff"}
NATIVE_DOC_EXTS = {".pdf"}

BRIDGE_VIDEO_EXTS = {
    ".flv", ".wmv", ".ts", ".mts", ".m2ts", ".vob", ".mpg", ".mpeg",
    ".3gp", ".3g2", ".ogv", ".mxf", ".rmvb",
}
BRIDGE_AUDIO_EXTS = {".flac", ".aac", ".aiff", ".aif", ".wma", ".amr", ".ac3", ".dts", ".mka", ".m4b", ".mp2"}
BRIDGE_IMAGE_EXTS = {".ico", ".avif", ".heic", ".heif", ".tga", ".ppm", ".pgm", ".pbm", ".psd"}

VIDEO_EXTS = NATIVE_VIDEO_EXTS | BRIDGE_VIDEO_EXTS
AUDIO_EXTS = NATIVE_AUDIO_EXTS | BRIDGE_AUDIO_EXTS
IMAGE_EXTS = NATIVE_IMAGE_EXTS | BRIDGE_IMAGE_EXTS
MEDIA_COMMAND_EXTS = VIDEO_EXTS | AUDIO_EXTS | IMAGE_EXTS
ALL_READ_MEDIA_EXTS = MEDIA_COMMAND_EXTS | NATIVE_DOC_EXTS

# Verified ReadFileTool hard limit is 100 MiB (104,857,600 B); 80 MiB .mp4 succeeded natively.
# Direct reads up to 90 MB pass through; bridged outputs target <= 45 MB for fast API upload.
NATIVE_DIRECT_MAX_BYTES: dict[str, int] = {
    "video": 90 * 1024 * 1024,
    "audio": 90 * 1024 * 1024,
    "image": 90 * 1024 * 1024,
    "pdf": 95 * 1024 * 1024,
}
BRIDGE_TARGET_MAX_BYTES: dict[str, int] = {
    "video": 45 * 1024 * 1024,
    "audio": 45 * 1024 * 1024,
    "image": 15 * 1024 * 1024,
}

AUTO_BRIDGE_MAX_SOURCE_BYTES = 250 * 1024 * 1024
AUTO_BRIDGE_MAX_DURATION_SEC = 20 * 60
FFPROBE_TIMEOUT_SEC = 4
FFMPEG_TIMEOUT_SEC = 18

CACHE_PROFILE_TAG = "v2"
CACHE_MAX_TOTAL_BYTES = 512 * 1024 * 1024
CACHE_MAX_ENTRIES = 64
CACHE_MAX_AGE_SEC = 7 * 24 * 3600

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
PYTHON_EXE = Path(sys.executable)
PREPARE_SCRIPT = PLUGIN_ROOT / "skills" / "native-multimodal" / "scripts" / "prepare_native_media.py"

DENY_RUN_COMMAND_REASON = (
    "[Native Multimodal] 当前 Gemini 模型支持通过 read_file 原生理解视频（含画面动态与音轨）、"
    "音频、图片和 PDF（不适用 46080 字节文本截断限制；非常规封装或超限文件也会自动桥接）。"
    "请直接对该文件调用 read_file(Path=\"...\")，不要用 ffmpeg/ffprobe/whisper 抽帧或转写。"
    "若文件极大需分段，请使用 native-multimodal 技能脚本生成原生音视频切片后再 read_file。"
)

# ---------------------------------------------------------------------------
# Command / Transcript Detection Patterns
# ---------------------------------------------------------------------------

MEDIA_TOOL_RE = re.compile(
    r"(?:^|[\s\"'`;|&(=/\\])"
    r"(?:ffmpeg(?:\.exe)?|ffprobe(?:\.exe)?|whisper(?:\.exe)?|faster[-_]whisper|"
    r"cv2|opencv(?:_python)?|moviepy|librosa|pydub|imageio|pyav)"
    r"(?:$|[\s\"'`;|&).,/\\:=])"
    r"|\b(?:import|from)\s+av\b|\bav\.[A-Za-z_]",
    re.IGNORECASE,
)

OWN_SCRIPT_RE = re.compile(
    r"(?:^|[\s\"'=/\\])(?:prepare_native_media\.py|test_multimodal_guard\.py|multimodal_guard\.py)(?:$|[\s\"'])",
    re.IGNORECASE,
)

SYNTH_LAVFI_RE = re.compile(r"(?:^|\s)-f\s+lavfi\b", re.IGNORECASE)
FFMPEG_INPUT_RE = re.compile(r"(?:^|\s)-i\s+(?:\"([^\"]+)\"|'([^']+)'|(\S+))")

USER_INTENT_RE = re.compile(
    r"(?:"
    r"\b(?:ffmpeg|ffprobe|whisper|faster[-_]whisper|opencv|cv2|moviepy|librosa|pydub|imageio|pyav)\b"
    r"|\b(?:edit|convert|cut|trim|crop|slice|split|merge|concat(?:enate)?|compress|transcode|re-?encode|remux|export|render|rotate|mute|watermark|subtitle|burn)\b"
    r"|\bextract\s+(?:audio|sound|track|frames?)\b"
    r"|剪辑|裁剪|裁掉|截取|截断|切片|分割|切分|拆分|合并|拼接|压缩|转码|重编码|重新编码|转封装|封装格式|转换格式|格式转换|转成|转为|导出|渲染|抽帧|提取音频|提取音轨|提取帧|去水印|加水印|加字幕|烧录|静音|消音|混音|调速|倍速|旋转|缩放|做成\s*gif"
    r")",
    re.IGNORECASE,
)

USER_REQ_RE = re.compile(r"<USER_REQUEST>\s*(.*?)\s*</USER_REQUEST>", re.S)
READ_TOOL_NAMES = {"read_file", "view_file"}

_EXT_PATTERN = "|".join(re.escape(e.lstrip(".")) for e in sorted(MEDIA_COMMAND_EXTS, key=len, reverse=True))
MEDIA_PATH_SCAN_RE = re.compile(
    rf"(?:~[\\/]|/|[A-Za-z]:[\\/]|[\w.~/\\-]+[\\/])?[\w .~()\-+@]+\.(?:{_EXT_PATTERN})(?=$|[\s\"'`;|&<>()\[\]{{}},])",
    re.IGNORECASE,
)
QUOTED_SPAN_RE = re.compile(r'"([^"\r\n]+)"|\'([^\'\r\n]+)\'')


# ---------------------------------------------------------------------------
# Basic I/O & Path Utilities
# ---------------------------------------------------------------------------

def reconfigure_utf8() -> None:
    """Force UTF-8 stdin/stdout on Windows to avoid GBK encoding failures."""
    if hasattr(sys.stdin, "reconfigure"):
        sys.stdin.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def allow(overwrite: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build a PreToolUse allow response."""
    res: dict[str, Any] = {"decision": "allow"}
    if overwrite:
        res["overwrite"] = overwrite
    return res


def deny(reason: str) -> dict[str, Any]:
    """Build a PreToolUse deny response."""
    return {"decision": "deny", "reason": reason}


def emit(obj: dict[str, Any]) -> None:
    """Serialize response JSON to stdout."""
    print(json.dumps(obj, ensure_ascii=False))


def get_cache_dir() -> Path:
    """Return the content-addressed media bridge cache directory."""
    import tempfile
    explicit_cache = os.environ.get("AGY_MM_CACHE_DIR")
    if explicit_cache and explicit_cache.strip():
        return Path(os.path.expanduser(explicit_cache.strip()))
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "Temp" / "agy-native-multimodal-cache"
    return Path(tempfile.gettempdir()) / "agy-native-multimodal-cache"


def resolve_cwd(payload: dict[str, Any]) -> Path:
    """Resolve the agent working directory from hook payload."""
    raw = payload.get("cwd")
    if isinstance(raw, str) and raw.strip():
        p = Path(raw)
        if p.is_dir():
            return p
    return Path.cwd()


def resolve_path(raw_path: str, cwd: Path) -> Path:
    """Resolve a relative, home-relative, or absolute path against cwd."""
    cleaned = os.path.expanduser(raw_path.strip())
    p = Path(cleaned)
    if not p.is_absolute():
        p = cwd / p
    return Path(os.path.abspath(str(p)))


def norm_path_key(path: str | Path) -> str:
    """Normalize a filesystem path for case-insensitive Windows comparisons."""
    return os.path.normcase(os.path.abspath(str(path)))


def find_binary(name: str) -> str | None:
    """Locate ffmpeg/ffprobe via env overrides, PATH, or standard platform locations."""
    import shutil

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
    return None


def modality_for_ext(ext: str) -> str:
    """Map lowercase file extension to modality."""
    if ext in VIDEO_EXTS:
        return "video"
    if ext in AUDIO_EXTS:
        return "audio"
    if ext in IMAGE_EXTS:
        return "image"
    if ext in NATIVE_DOC_EXTS:
        return "pdf"
    return ""


# ---------------------------------------------------------------------------
# Media Probing, Caching & Auto-Bridge
# ---------------------------------------------------------------------------

def probe_media(path: Path) -> dict[str, Any] | None:
    """Inspect media streams and duration using ffprobe."""
    import subprocess
    ffprobe = find_binary("ffprobe")
    if not ffprobe:
        return None
    cmd = [
        ffprobe,
        "-v", "error",
        "-show_entries", "format=duration,size,format_name:stream=codec_type,codec_name,pix_fmt,width,height",
        "-of", "json",
        str(path),
    ]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=FFPROBE_TIMEOUT_SEC,
        )
        if proc.returncode != 0 or not proc.stdout.strip():
            return None
        return json.loads(proc.stdout)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return None


def parse_probe_info(probe_data: dict[str, Any] | None) -> tuple[float | None, list[str], list[str]]:
    """Extract (duration_sec, video_codecs, audio_codecs) from ffprobe JSON."""
    if not probe_data:
        return None, [], []
    fmt = probe_data.get("format") or {}
    dur: float | None = None
    try:
        if fmt.get("duration") is not None:
            dur = float(fmt["duration"])
    except (TypeError, ValueError):
        dur = None

    v_codecs: list[str] = []
    a_codecs: list[str] = []
    for stream in probe_data.get("streams") or []:
        ctype = (stream.get("codec_type") or "").lower()
        cname = (stream.get("codec_name") or "").lower()
        if ctype == "video" and cname:
            v_codecs.append(cname)
        elif ctype == "audio" and cname:
            a_codecs.append(cname)
    return dur, v_codecs, a_codecs


def compute_cache_path(src_path: Path, st: os.stat_result, target_ext: str) -> Path:
    """Compute content-addressed cache path from normalized path and file stat."""
    import hashlib
    key_src = f"{norm_path_key(src_path)}|{st.st_size}|{st.st_mtime_ns}|{CACHE_PROFILE_TAG}|{target_ext}"
    digest = hashlib.sha256(key_src.encode("utf-8")).hexdigest()[:32]
    return get_cache_dir() / f"{digest}{target_ext}"


def prune_cache(cache_dir: Path, keep_path: Path | None = None) -> None:
    """Evict stale or excess entries from the cache directory."""
    import time
    try:
        if not cache_dir.is_dir():
            return
        now = time.time()
        entries: list[tuple[float, int, Path]] = []
        total_bytes = 0
        for item in cache_dir.iterdir():
            if not item.is_file():
                continue
            if item.name.startswith("tmp_"):
                if now - item.stat().st_mtime > 300:
                    item.unlink(missing_ok=True)
                continue
            st = item.stat()
            if keep_path and norm_path_key(item) == norm_path_key(keep_path):
                total_bytes += st.st_size
                continue
            if now - st.st_mtime > CACHE_MAX_AGE_SEC:
                item.unlink(missing_ok=True)
                continue
            entries.append((st.st_mtime, st.st_size, item))
            total_bytes += st.st_size

        entries.sort(key=lambda row: row[0])
        count = len(entries) + (1 if keep_path and keep_path.is_file() else 0)
        for _, sz, item in entries:
            if total_bytes <= CACHE_MAX_TOTAL_BYTES and count <= CACHE_MAX_ENTRIES:
                break
            item.unlink(missing_ok=True)
            total_bytes -= sz
            count -= 1
    except OSError:
        pass


def target_ext_for_modality(modality: str) -> str:
    """Choose the canonical native bridge container extension per modality."""
    if modality == "video":
        return ".mp4"
    if modality == "audio":
        return ".mp3"
    return ".png"


def run_ffmpeg_transcode(args: list[str], final_path: Path, max_bytes: int) -> bool:
    """Run ffmpeg writing atomically via a temporary file in cache_dir."""
    import subprocess
    ffmpeg = find_binary("ffmpeg")
    if not ffmpeg:
        return False
    cache_dir = final_path.parent
    cache_dir.mkdir(parents=True, exist_ok=True)
    tmp_path = cache_dir / f"tmp_{os.getpid()}_{final_path.name}"
    try:
        tmp_path.unlink(missing_ok=True)
        proc = subprocess.run(
            [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", *args, str(tmp_path)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=FFMPEG_TIMEOUT_SEC,
        )
        if proc.returncode != 0 or not tmp_path.is_file():
            tmp_path.unlink(missing_ok=True)
            return False
        size = tmp_path.stat().st_size
        if size <= 0 or size > max_bytes:
            tmp_path.unlink(missing_ok=True)
            return False
        os.replace(tmp_path, final_path)
        prune_cache(cache_dir, keep_path=final_path)
        return True
    except (OSError, subprocess.SubprocessError):
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        return False


def bridge_video(
    src_path: Path,
    final_path: Path,
    src_size: int,
    direct_max_bytes: int,
    target_max_bytes: int,
    duration_sec: float | None,
    v_codecs: list[str],
    a_codecs: list[str],
) -> bool:
    """Remux or fast-transcode a video into a native-compatible MP4."""
    codecs_compatible = (
        bool(v_codecs)
        and all(c in NATIVE_VIDEO_CODECS for c in v_codecs)
        and all(c in NATIVE_VIDEO_AUDIO_CODECS for c in a_codecs)
    )
    if codecs_compatible and src_size <= direct_max_bytes:
        if run_ffmpeg_transcode(["-i", str(src_path), "-c", "copy", "-movflags", "+faststart"], final_path, direct_max_bytes):
            return True

    # Constrained fast-transcode targeting target_max_bytes (45 MB) for oversized/exotic videos.
    vf = "scale='min(1280,iw)':-2"
    encode_args = [
        "-i", str(src_path),
        "-map", "0:v:0",
        "-map", "0:a:0?",
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-crf", "28",
        "-pix_fmt", "yuv420p",
        "-vf", vf,
        "-c:a", "aac",
        "-b:a", "64k",
        "-movflags", "+faststart",
    ]
    if duration_sec and duration_sec > 5.0:
        total_kbps = int((target_max_bytes * 0.85 * 8) / duration_sec / 1000)
        audio_kbps = 64 if total_kbps >= 250 else 48
        video_kbps = max(80, total_kbps - audio_kbps)
        if video_kbps < 2500:
            if video_kbps < 400:
                vf = "scale='min(640,iw)':-2"
            elif video_kbps < 900:
                vf = "scale='min(960,iw)':-2"
            encode_args = [
                "-i", str(src_path),
                "-map", "0:v:0",
                "-map", "0:a:0?",
                "-c:v", "libx264",
                "-preset", "ultrafast",
                "-crf", "28",
                "-maxrate", f"{video_kbps}k",
                "-bufsize", f"{video_kbps * 2}k",
                "-pix_fmt", "yuv420p",
                "-vf", vf,
                "-c:a", "aac",
                "-b:a", f"{audio_kbps}k",
                "-movflags", "+faststart",
            ]
    return run_ffmpeg_transcode(encode_args, final_path, target_max_bytes)


def bridge_audio(src_path: Path, final_path: Path, max_bytes: int, duration_sec: float | None) -> bool:
    """Fast-transcode audio into native MP3 under max_bytes."""
    kbps = 64
    if duration_sec and duration_sec > 10.0:
        max_kbps = int((max_bytes * 0.88 * 8) / duration_sec / 1000)
        kbps = max(24, min(64, max_kbps))
    args = ["-i", str(src_path), "-vn", "-c:a", "libmp3lame", "-b:a", f"{kbps}k"]
    return run_ffmpeg_transcode(args, final_path, max_bytes)


def bridge_image(src_path: Path, final_path: Path, max_bytes: int) -> Path | None:
    """Convert exotic image formats into native PNG (or JPEG fallback) under max_bytes."""
    if run_ffmpeg_transcode(["-i", str(src_path), "-frames:v", "1"], final_path, max_bytes):
        return final_path
    jpg_fallback = final_path.with_suffix(".jpg")
    if run_ffmpeg_transcode(["-i", str(src_path), "-frames:v", "1", "-q:v", "3"], jpg_fallback, max_bytes):
        return jpg_fallback
    return None


def format_slice_deny_reason(target: Path, reason_detail: str) -> str:
    """Build actionable deny guidance pointing to prepare_native_media.py slice."""
    script_str = str(PREPARE_SCRIPT)
    return (
        f"[Native Multimodal] 文件 {target.name} {reason_detail}，无法单文件自动桥接为原生可读媒体。"
        f"请先使用原生切片脚本将其切分为符合大小上限的 .mp4/.mp3 片段后再并行调用 read_file：\n"
        f"{PYTHON_EXE} -X utf8 \"{script_str}\" slice \"{target}\" --chunk-sec 180 --overlap-sec 3"
    )


def _parse_optional_int(val: Any) -> int | None:
    if val is None or val == "":
        return None
    try:
        return int(val)
    except (TypeError, ValueError):
        return None


def sanitize_media_line_params(args: dict[str, Any]) -> dict[str, Any]:
    """Fix invalid StartLine/EndLine/ByteOffset values that fail tools.validateArgs on binary files."""
    fixes: dict[str, Any] = {}
    start_val = _parse_optional_int(args.get("StartLine"))
    end_val = _parse_optional_int(args.get("EndLine"))
    offset_val = _parse_optional_int(args.get("ByteOffset"))

    eff_start = start_val
    if start_val is not None and start_val < 1:
        fixes["StartLine"] = 1
        eff_start = 1

    if end_val is not None:
        min_end = eff_start if (eff_start is not None and eff_start >= 1) else 1
        if end_val < 1 or end_val < min_end:
            fixes["EndLine"] = min_end

    if offset_val is not None and offset_val < 0:
        fixes["ByteOffset"] = 0

    return fixes


def handle_read_file(payload: dict[str, Any], tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Handle PreToolUse for read_file and view_file."""
    preferred_keys = ("AbsolutePath", "Path") if tool_name == "view_file" else ("Path", "AbsolutePath")
    raw_path = ""
    path_key = preferred_keys[0]
    for key in preferred_keys:
        val = args.get(key)
        if isinstance(val, str) and val.strip():
            raw_path = val.strip()
            path_key = key
            break
    if not raw_path:
        return allow()

    ext = Path(raw_path).suffix.lower()
    if ext not in ALL_READ_MEDIA_EXTS:
        return allow()

    cwd = resolve_cwd(payload)
    target = resolve_path(raw_path, cwd)
    try:
        if not target.is_file():
            return allow()
        st = target.stat()
    except OSError:
        return allow()

    modality = modality_for_ext(ext)
    if not modality:
        return allow()

    direct_max_bytes = NATIVE_DIRECT_MAX_BYTES[modality]
    overwrite = sanitize_media_line_params(args)

    if modality == "pdf":
        if st.st_size > direct_max_bytes:
            return deny(
                f"[Native Multimodal] PDF 文件 {target.name} ({st.st_size / (1024 * 1024):.1f} MB) "
                f"超过原生上限 ({direct_max_bytes / (1024 * 1024):.1f} MB)，请先拆分页面后再调用 read_file。"
            )
        return allow(overwrite or None)

    needs_bridge = False
    duration_sec: float | None = None
    v_codecs: list[str] = []
    a_codecs: list[str] = []
    in_cache_dir = norm_path_key(target.parent) == norm_path_key(get_cache_dir())

    if modality == "video":
        if in_cache_dir and ext in NATIVE_VIDEO_EXTS and st.st_size <= direct_max_bytes:
            needs_bridge = False
        elif ext not in NATIVE_VIDEO_EXTS or st.st_size > direct_max_bytes:
            needs_bridge = True
        else:
            # Verify inner video/audio codecs are natively supported (fast ~50ms ffprobe).
            probe_data = probe_media(target)
            duration_sec, v_codecs, a_codecs = parse_probe_info(probe_data)
            if v_codecs and not all(c in NATIVE_VIDEO_CODECS for c in v_codecs):
                needs_bridge = True
            if a_codecs and not all(c in NATIVE_VIDEO_AUDIO_CODECS for c in a_codecs):
                needs_bridge = True
    elif modality == "audio":
        if ext not in NATIVE_AUDIO_EXTS or st.st_size > direct_max_bytes:
            needs_bridge = True
    elif modality == "image":
        if ext not in NATIVE_IMAGE_EXTS or st.st_size > direct_max_bytes:
            needs_bridge = True

    if not needs_bridge:
        return allow(overwrite or None)

    if st.st_size > AUTO_BRIDGE_MAX_SOURCE_BYTES:
        return deny(format_slice_deny_reason(target, f"体积过大 ({st.st_size / (1024 * 1024):.1f} MB)"))

    if modality in ("video", "audio") and duration_sec is None:
        duration_sec, v_codecs, a_codecs = parse_probe_info(probe_media(target))
        if duration_sec is not None and duration_sec > AUTO_BRIDGE_MAX_DURATION_SEC:
            return deny(format_slice_deny_reason(target, f"时长过长 ({duration_sec / 60:.1f} 分钟)"))

    effective_modality = "audio" if (modality == "video" and not v_codecs and a_codecs) else modality
    target_max_bytes = BRIDGE_TARGET_MAX_BYTES[effective_modality]
    accept_max_bytes = max(target_max_bytes, NATIVE_DIRECT_MAX_BYTES[effective_modality])
    target_ext = target_ext_for_modality(effective_modality)
    cached_path = compute_cache_path(target, st, target_ext)
    if cached_path.is_file() and 0 < cached_path.stat().st_size <= accept_max_bytes:
        try:
            os.utime(cached_path, None)
        except OSError:
            pass
        overwrite[path_key] = str(cached_path)
        return allow(overwrite)

    # Check jpg fallback for oversized exotic images
    if effective_modality == "image":
        jpg_path = cached_path.with_suffix(".jpg")
        if jpg_path.is_file() and 0 < jpg_path.stat().st_size <= accept_max_bytes:
            try:
                os.utime(jpg_path, None)
            except OSError:
                pass
            overwrite[path_key] = str(jpg_path)
            return allow(overwrite)

    ok = False
    if effective_modality == "video":
        ok = bridge_video(
            target,
            cached_path,
            st.st_size,
            direct_max_bytes,
            target_max_bytes,
            duration_sec,
            v_codecs,
            a_codecs,
        )
    elif effective_modality == "audio":
        ok = bridge_audio(target, cached_path, target_max_bytes, duration_sec)
    elif effective_modality == "image":
        out_img = bridge_image(target, cached_path, target_max_bytes)
        if out_img is not None:
            cached_path = out_img
            ok = True

    if ok and cached_path.is_file() and 0 < cached_path.stat().st_size <= accept_max_bytes:
        overwrite[path_key] = str(cached_path)
        return allow(overwrite)

    return deny(format_slice_deny_reason(target, "转码压缩后仍超出原生单文件大小或超时限制"))


# ---------------------------------------------------------------------------
# Transcript Inspection & Smart Exceptions for run_command
# ---------------------------------------------------------------------------

def unquote_transcript_arg(val: Any) -> str:
    """Decode transcript tool_calls arg values that may be double-JSON-encoded."""
    if not isinstance(val, str):
        return ""
    s = val.strip()
    if len(s) >= 2 and s[0] == '"' and s[-1] == '"':
        try:
            decoded = json.loads(s)
            if isinstance(decoded, str):
                return decoded.strip()
        except json.JSONDecodeError:
            pass
    return s


def extract_user_text(raw: str) -> str:
    """Extract clean user prompt text from transcript USER_INPUT content."""
    if not raw:
        return ""
    matches = USER_REQ_RE.findall(raw)
    if matches:
        return "\n".join(m.strip() for m in matches if m.strip())
    cleaned = re.sub(r"<ADDITIONAL_METADATA>.*?</ADDITIONAL_METADATA>", "", raw, flags=re.S)
    return cleaned.strip()


def user_explicitly_requested_media_processing(text: str) -> bool:
    """Return True if the user explicitly asked for editing/transcoding or named media CLI tools."""
    if not text:
        return False
    return bool(USER_INTENT_RE.search(text))


def candidate_cache_exts(modality: str) -> tuple[str, ...]:
    """Return possible cache extensions for a source modality."""
    if modality == "video":
        return (".mp4", ".mp3")
    if modality == "audio":
        return (".mp3",)
    if modality == "image":
        return (".png", ".jpg")
    return ()


def _record_read_path(read_keys: set[str], raw_path: str, cwd: Path) -> None:
    if not raw_path:
        return
    resolved = resolve_path(raw_path, cwd)
    read_keys.add(norm_path_key(resolved))
    try:
        if resolved.is_file():
            mod = modality_for_ext(resolved.suffix.lower())
            exts = candidate_cache_exts(mod)
            if exts:
                st = resolved.stat()
                for cext in exts:
                    read_keys.add(norm_path_key(compute_cache_path(resolved, st, cext)))
    except OSError:
        pass


def scan_transcript(transcript_path_str: str | None, cwd: Path) -> tuple[str, set[str]]:
    """Scan transcript.jsonl once for the latest USER_INPUT and previously read media paths."""
    if not transcript_path_str:
        return "", set()
    tpath = Path(transcript_path_str)
    if not tpath.is_file():
        return "", set()

    latest_user_text = ""
    read_keys: set[str] = set()
    pending_reads: list[tuple[int, str]] = []
    pending_total = 0
    seen_results = 0

    try:
        with open(tpath, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                stype = obj.get("type")
                if stype == "USER_INPUT":
                    txt = extract_user_text(str(obj.get("content") or ""))
                    if txt:
                        latest_user_text = txt
                    for _, rp in pending_reads:
                        _record_read_path(read_keys, rp, cwd)
                    pending_reads.clear()
                    pending_total = 0
                    seen_results = 0
                elif stype == "PLANNER_RESPONSE":
                    for _, rp in pending_reads:
                        _record_read_path(read_keys, rp, cwd)
                    pending_reads.clear()
                    pending_total = 0
                    seen_results = 0
                    tcs = obj.get("tool_calls") or []
                    pending_total = len(tcs)
                    for idx, tc in enumerate(tcs):
                        if tc.get("name") in READ_TOOL_NAMES:
                            tc_args = tc.get("args") or {}
                            raw = ""
                            for k in ("Path", "AbsolutePath", "File"):
                                cand = unquote_transcript_arg(tc_args.get(k))
                                if cand:
                                    raw = cand
                                    break
                            if raw:
                                pending_reads.append((idx, raw))
                elif stype == "GENERIC" and pending_total > 0:
                    status = str(obj.get("status") or "").upper()
                    content = str(obj.get("content") or "")
                    failed = status == "ERROR" or "denied" in content[:160].lower()
                    matched = [rp for idx, rp in pending_reads if idx == seen_results]
                    if matched and not failed:
                        _record_read_path(read_keys, matched[0], cwd)
                    pending_reads = [(idx, rp) for idx, rp in pending_reads if idx != seen_results]
                    seen_results += 1
    except OSError:
        return latest_user_text, read_keys

    # Flush any pending reads that have no following GENERIC step (e.g. minimal transcripts).
    for _, rp in pending_reads:
        _record_read_path(read_keys, rp, cwd)
    return latest_user_text, read_keys


def extract_existing_media_paths(cmd: str, cwd: Path) -> list[Path]:
    """Extract existing local media file paths referenced anywhere in a shell command."""
    candidates: list[str] = []
    for m in QUOTED_SPAN_RE.finditer(cmd):
        span = m.group(1) or m.group(2) or ""
        if span:
            candidates.append(span)
    for tok in re.split(r"[\s;|&=<>()\[\]{},]+", cmd):
        tok = tok.strip("\"'`")
        if tok:
            candidates.append(tok)
    for m in MEDIA_PATH_SCAN_RE.finditer(cmd):
        candidates.append(m.group(0))

    seen: set[str] = set()
    found: list[Path] = []
    for raw in candidates:
        for variant in (raw, raw.replace("\\\\", "\\")):
            ext = Path(variant).suffix.lower()
            if ext not in MEDIA_COMMAND_EXTS:
                continue
            resolved = resolve_path(variant, cwd)
            key = norm_path_key(resolved)
            if key in seen:
                continue
            try:
                if resolved.is_file():
                    seen.add(key)
                    found.append(resolved)
            except OSError:
                pass
    return found


def is_synthetic_generation(cmd: str, cwd: Path) -> bool:
    """Return True if the command is pure synthetic media generation (e.g. ffmpeg -f lavfi)."""
    if not SYNTH_LAVFI_RE.search(cmd):
        return False
    for m in FFMPEG_INPUT_RE.finditer(cmd):
        inp = (m.group(1) or m.group(2) or m.group(3) or "").strip()
        if not inp:
            continue
        ext = Path(inp).suffix.lower()
        if ext in MEDIA_COMMAND_EXTS and resolve_path(inp, cwd).is_file():
            return False
    return True


def media_file_already_read(media_path: Path, read_keys: set[str]) -> bool:
    """Return True if media_path or its bridged cache derivative was already read via read_file."""
    if norm_path_key(media_path) in read_keys:
        return True
    try:
        st = media_path.stat()
        mod = modality_for_ext(media_path.suffix.lower())
        for cext in candidate_cache_exts(mod):
            if norm_path_key(compute_cache_path(media_path, st, cext)) in read_keys:
                return True
    except OSError:
        pass
    return False


def should_enforce_for_model(model_name: Any) -> bool:
    """Enforce native multimodal guard on Gemini/orchestrator/auto/empty; skip Claude/GPT."""
    m = str(model_name or "").strip().lower()
    if m.startswith(("claude", "gpt")):
        return False
    return m == "" or m == "auto" or "gemini" in m or "orchestrator" in m


def is_own_script_command(cmd: str) -> bool:
    """Allow prepare_native_media.py / test commands unless chained with disallowed media tools."""
    if not OWN_SCRIPT_RE.search(cmd):
        return False
    for seg in re.split(r"(?:&&|\|\||;|\r?\n)", cmd):
        if MEDIA_TOOL_RE.search(seg) and not OWN_SCRIPT_RE.search(seg):
            return False
    return True


def handle_run_command(payload: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
    """Handle PreToolUse for run_command."""
    if not should_enforce_for_model(payload.get("modelName")):
        return allow()

    cmd = args.get("CommandLine") or args.get("Command") or ""
    if not isinstance(cmd, str) or not cmd.strip():
        return allow()

    if not MEDIA_TOOL_RE.search(cmd):
        return allow()

    if is_own_script_command(cmd):
        return allow()

    cwd = resolve_cwd(payload)
    media_paths = extract_existing_media_paths(cmd, cwd)
    if not media_paths:
        return allow()

    if is_synthetic_generation(cmd, cwd):
        return allow()

    last_user_raw = str(payload.get("lastUserInput") or "")
    last_user_clean = extract_user_text(last_user_raw)
    if user_explicitly_requested_media_processing(last_user_clean):
        return allow()

    latest_transcript_user, read_keys = scan_transcript(payload.get("transcriptPath"), cwd)
    if user_explicitly_requested_media_processing(latest_transcript_user):
        return allow()

    if all(media_file_already_read(p, read_keys) for p in media_paths):
        return allow()

    return deny(DENY_RUN_COMMAND_REASON)


# ---------------------------------------------------------------------------
# Entry Point
# ---------------------------------------------------------------------------

def dispatch(payload: dict[str, Any]) -> dict[str, Any]:
    """Route a parsed PreToolUse payload to the appropriate tool handler."""
    hook_event = str(payload.get("hookEventName") or "PreToolUse")
    if hook_event and hook_event != "PreToolUse":
        return {}
    tool_call = payload.get("toolCall") or {}
    tool_name = str(tool_call.get("name") or "").strip().lower()
    args = tool_call.get("args") or {}
    if not isinstance(args, dict):
        return allow()

    if tool_name in ("read_file", "view_file"):
        return handle_read_file(payload, tool_name, args)
    if tool_name in ("run_command", "run_in_terminal"):
        return handle_run_command(payload, args)
    return allow()


def main() -> None:
    """Hook entry point with guaranteed fail-safe allow fallback."""
    try:
        reconfigure_utf8()
        raw_in = sys.stdin.read()
        if not raw_in.strip():
            emit(allow())
            return
        payload = json.loads(raw_in)
        if not isinstance(payload, dict):
            emit(allow())
            return
        emit(dispatch(payload))
    except Exception:
        try:
            emit(allow())
        except Exception:
            print('{"decision": "allow"}')


if __name__ == "__main__":
    main()
