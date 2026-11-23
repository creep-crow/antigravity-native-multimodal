#!/usr/bin/env python3
"""Unit and cmd /c integration tests for multimodal_guard.py."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import unittest

HOOK_DIR = Path(__file__).resolve().parent
PLUGIN_ROOT = HOOK_DIR.parent
HOOKS_JSON = PLUGIN_ROOT / "hooks.json"

sys.path.insert(0, str(HOOK_DIR))
import multimodal_guard as mg  # noqa: E402


def run_ffmpeg(args: list[str]) -> None:
    ffmpeg = mg.find_binary("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg binary not found for tests")
    proc = subprocess.run(
        [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {proc.stderr}")


def write_minimal_ico(path: Path) -> None:
    """Write a valid 16x16 32-bpp RGBA PNG-free BMP-DIB Windows .ico file."""
    w, h = 16, 16
    # BITMAPINFOHEADER (40 bytes), height is 2*h (XOR + AND mask)
    bih = struct.pack("<IIIHHIIIIII", 40, w, h * 2, 1, 32, 0, w * h * 4, 0, 0, 0, 0)
    xor_pixels = b"\x00\x00\xff\xff" * (w * h)  # Solid red BGRA
    and_mask = b"\x00" * (w * h // 8)
    dib = bih + xor_pixels + and_mask
    icon_dir = struct.pack("<HHH", 0, 1, 1)
    entry = struct.pack("<BBBBHHII", w, h, 0, 0, 1, 32, len(dib), 6 + 16)
    path.write_bytes(icon_dir + entry + dib)


class MultimodalGuardTests(unittest.TestCase):
    temp_root: Path
    work_dir: Path
    cache_dir: Path
    orig_cache_dir: str | None

    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_root = Path(tempfile.mkdtemp(prefix="test_mm_guard_"))
        cls.work_dir = cls.temp_root / "work"
        cls.work_dir.mkdir(parents=True, exist_ok=True)
        cls.cache_dir = cls.temp_root / "cache" / "agy-native-multimodal-cache"
        cls.orig_cache_dir = os.environ.get("AGY_MM_CACHE_DIR")
        os.environ["AGY_MM_CACHE_DIR"] = str(cls.cache_dir)

        # Natively supported formats (per capability_matrix.json)
        cls.mp4_path = cls.work_dir / "sample.mp4"
        run_ffmpeg([
            "-f", "lavfi", "-i", "color=c=red:s=160x120:d=1.2:r=10",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=1.2",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
            str(cls.mp4_path),
        ])
        cls.mkv_native_path = cls.work_dir / "sample_h264.mkv"
        run_ffmpeg(["-i", str(cls.mp4_path), "-c", "copy", str(cls.mkv_native_path)])

        cls.avi_native_path = cls.work_dir / "sample_mpeg4.avi"
        run_ffmpeg([
            "-f", "lavfi", "-i", "color=c=blue:s=160x120:d=1.0:r=10",
            "-f", "lavfi", "-i", "sine=frequency=660:duration=1.0",
            "-c:v", "mpeg4", "-c:a", "mp3",
            str(cls.avi_native_path),
        ])

        cls.wav_path = cls.work_dir / "tone.wav"
        run_ffmpeg(["-f", "lavfi", "-i", "sine=frequency=523:duration=1.0", str(cls.wav_path)])
        cls.ogg_path = cls.work_dir / "voice.ogg"
        run_ffmpeg(["-i", str(cls.wav_path), "-c:a", "libvorbis", str(cls.ogg_path)])
        cls.opus_path = cls.work_dir / "voice.opus"
        run_ffmpeg(["-i", str(cls.wav_path), "-c:a", "libopus", str(cls.opus_path)])
        cls.png_path = cls.work_dir / "shot.png"
        run_ffmpeg(["-f", "lavfi", "-i", "color=c=yellow:s=64x64:d=0.1", "-frames:v", "1", str(cls.png_path)])
        cls.bmp_path = cls.work_dir / "shot.bmp"
        run_ffmpeg(["-f", "lavfi", "-i", "color=c=green:s=64x64:d=0.1", "-frames:v", "1", str(cls.bmp_path)])
        cls.tif_path = cls.work_dir / "shot.tif"
        run_ffmpeg(["-f", "lavfi", "-i", "color=c=cyan:s=64x64:d=0.1", "-frames:v", "1", str(cls.tif_path)])
        cls.pdf_path = cls.work_dir / "doc.pdf"
        cls.pdf_path.write_bytes(b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n")

        # Formats requiring Auto-Bridge (per capability_matrix.json)
        cls.flv_path = cls.work_dir / "legacy.flv"
        run_ffmpeg(["-i", str(cls.mp4_path), "-c:v", "flv1", "-c:a", "mp3", "-ar", "44100", str(cls.flv_path)])

        cls.wmv_path = cls.work_dir / "legacy.wmv"
        run_ffmpeg(["-i", str(cls.mp4_path), "-c:v", "wmv2", "-c:a", "wmav2", str(cls.wmv_path)])

        cls.ts_path = cls.work_dir / "stream.ts"
        run_ffmpeg(["-i", str(cls.mp4_path), "-c:v", "libx264", "-c:a", "aac", str(cls.ts_path)])

        cls.hevc_mkv_path = cls.work_dir / "hevc_nonnative.mkv"
        try:
            run_ffmpeg([
                "-f", "lavfi", "-i", "color=c=purple:s=160x120:d=1.0:r=10",
                "-f", "lavfi", "-i", "sine=frequency=440:duration=1.0",
                "-c:v", "libx265", "-c:a", "ac3",
                str(cls.hevc_mkv_path),
            ])
        except RuntimeError:
            run_ffmpeg([
                "-f", "lavfi", "-i", "color=c=purple:s=160x120:d=1.0:r=10",
                "-f", "lavfi", "-i", "sine=frequency=440:duration=1.0",
                "-c:v", "mpeg4", "-c:a", "ac3",
                str(cls.hevc_mkv_path),
            ])

        cls.flac_path = cls.work_dir / "lossless.flac"
        run_ffmpeg(["-i", str(cls.wav_path), "-c:a", "flac", str(cls.flac_path)])

        cls.aac_path = cls.work_dir / "adts.aac"
        run_ffmpeg(["-i", str(cls.wav_path), "-c:a", "aac", str(cls.aac_path)])

        cls.aiff_path = cls.work_dir / "studio.aiff"
        run_ffmpeg(["-i", str(cls.wav_path), "-c:a", "pcm_s16be", str(cls.aiff_path)])

        cls.ico_path = cls.work_dir / "favicon.ico"
        write_minimal_ico(cls.ico_path)

        cls.zh_dir = cls.work_dir / "测试 目录"
        cls.zh_dir.mkdir(parents=True, exist_ok=True)
        cls.zh_flv = cls.zh_dir / "示例 视频.flv"
        shutil.copy2(cls.flv_path, cls.zh_flv)

        cls.audio_only_ts = cls.work_dir / "audio_only.mkv"
        run_ffmpeg(["-i", str(cls.wav_path), "-c:a", "ac3", str(cls.audio_only_ts)])

        cls.txt_path = cls.work_dir / "notes.py"
        cls.txt_path.write_text("print('hello')\n", encoding="utf-8")

        cls.huge_mp4 = cls.work_dir / "huge_sparse.mp4"
        with open(cls.huge_mp4, "wb") as f:
            f.seek(mg.AUTO_BRIDGE_MAX_SOURCE_BYTES + 1024 * 1024)
            f.write(b"\x00")

    @classmethod
    def tearDownClass(cls) -> None:
        if cls.orig_cache_dir is None:
            os.environ.pop("AGY_MM_CACHE_DIR", None)
        else:
            os.environ["AGY_MM_CACHE_DIR"] = cls.orig_cache_dir
        shutil.rmtree(cls.temp_root, ignore_errors=True)

    def _payload(self, tool_name: str, args: dict, **extra) -> dict:
        base = {
            "conversationId": "test-conv",
            "workspacePaths": [str(self.work_dir)],
            "modelName": "gemini-4-carbon-high",
            "lastUserInput": "",
            "hookEventName": "PreToolUse",
            "cwd": str(self.work_dir),
            "toolCall": {
                "id": "toolu_test",
                "name": tool_name,
                "args": args,
            },
        }
        base.update(extra)
        return base

    def test_hooks_json_schema_and_command(self) -> None:
        data = json.loads(HOOKS_JSON.read_text(encoding="utf-8"))
        self.assertIn("native-multimodal-guard", data)
        hook = data["native-multimodal-guard"]
        self.assertTrue(hook.get("enabled", True))
        matchers = {entry["matcher"]: entry["hooks"][0] for entry in hook["PreToolUse"]}
        self.assertIn("read_file|view_file", matchers)
        self.assertIn("run_command", matchers)
        self.assertEqual(matchers["read_file|view_file"]["timeout"], 25)
        self.assertEqual(matchers["run_command"]["timeout"], 10)
        self.assertTrue((PLUGIN_ROOT / "hooks" / "multimodal_guard.py").is_file())

    def test_non_media_read_file_fast_allow(self) -> None:
        t0 = time.perf_counter()
        res = mg.dispatch(self._payload("read_file", {"Path": "notes.py"}))
        elapsed_ms = (time.perf_counter() - t0) * 1000
        self.assertEqual(res, {"decision": "allow"})
        self.assertLess(elapsed_ms, 15.0)

    def test_missing_media_read_file_allows(self) -> None:
        res = mg.dispatch(self._payload("read_file", {"Path": "does_not_exist.mp4"}))
        self.assertEqual(res, {"decision": "allow"})

    def test_native_supported_formats_no_overwrite(self) -> None:
        for p in (
            self.mp4_path,
            self.mkv_native_path,
            self.avi_native_path,
            self.wav_path,
            self.ogg_path,
            self.opus_path,
            self.png_path,
            self.bmp_path,
            self.tif_path,
            self.pdf_path,
        ):
            res = mg.dispatch(self._payload("read_file", {"Path": str(p)}))
            self.assertEqual(res, {"decision": "allow"}, f"Unexpected result for {p.name}: {res}")

    def test_line_param_validation_sanitizer(self) -> None:
        # Valid params on media: allowed without unnecessary overwrite
        res_valid = mg.dispatch(
            self._payload("read_file", {"Path": "sample.mp4", "StartLine": 1, "EndLine": 5, "ByteOffset": 100})
        )
        self.assertEqual(res_valid, {"decision": "allow"})

        # Invalid params that fail tools.validateArgs are sanitized via overwrite
        res_bad = mg.dispatch(
            self._payload("read_file", {"Path": "sample.mp4", "StartLine": 0, "EndLine": -1, "ByteOffset": -5})
        )
        self.assertEqual(res_bad["decision"], "allow")
        self.assertEqual(res_bad["overwrite"], {"StartLine": 1, "EndLine": 1, "ByteOffset": 0})

        res_inverted = mg.dispatch(
            self._payload("read_file", {"Path": "sample.mp4", "StartLine": 10, "EndLine": 5})
        )
        self.assertEqual(res_inverted["overwrite"], {"EndLine": 10})

    def test_unsupported_video_containers_and_codecs_autobridge(self) -> None:
        for src in (self.flv_path, self.wmv_path, self.ts_path, self.hevc_mkv_path):
            res = mg.dispatch(self._payload("read_file", {"Path": str(src)}))
            self.assertEqual(res["decision"], "allow", f"Failed bridge for {src.name}: {res}")
            self.assertIn("overwrite", res, f"Expected overwrite for {src.name}")
            bridged = Path(res["overwrite"]["Path"])
            self.assertTrue(bridged.is_file())
            self.assertEqual(bridged.suffix.lower(), ".mp4")
            self.assertLessEqual(bridged.stat().st_size, mg.BRIDGE_TARGET_MAX_BYTES["video"])

        # Cache hit returns identical path immediately
        res_cached = mg.dispatch(self._payload("read_file", {"Path": str(self.flv_path)}))
        self.assertTrue(Path(res_cached["overwrite"]["Path"]).is_file())

        # view_file uses AbsolutePath key in overwrite
        res_view = mg.dispatch(self._payload("view_file", {"AbsolutePath": str(self.wmv_path)}))
        self.assertEqual(res_view["decision"], "allow")
        self.assertIn("AbsolutePath", res_view["overwrite"])
        self.assertTrue(Path(res_view["overwrite"]["AbsolutePath"]).is_file())

    def test_unsupported_audio_autobridge(self) -> None:
        for src in (self.flac_path, self.aac_path, self.aiff_path):
            res = mg.dispatch(self._payload("read_file", {"Path": str(src)}))
            self.assertEqual(res["decision"], "allow")
            self.assertIn("overwrite", res, f"Expected audio bridge for {src.name}")
            out = Path(res["overwrite"]["Path"])
            self.assertTrue(out.is_file())
            self.assertEqual(out.suffix.lower(), ".mp3")

    def test_unsafe_image_ico_autobridge(self) -> None:
        res = mg.dispatch(self._payload("read_file", {"Path": str(self.ico_path)}))
        self.assertEqual(res["decision"], "allow")
        self.assertIn("overwrite", res)
        out = Path(res["overwrite"]["Path"])
        self.assertTrue(out.is_file())
        self.assertIn(out.suffix.lower(), {".png", ".jpg"})

    def test_chinese_path_with_spaces_and_audio_only_nonnative_mkv(self) -> None:
        res_zh = mg.dispatch(self._payload("read_file", {"Path": str(self.zh_flv)}))
        self.assertEqual(res_zh["decision"], "allow")
        self.assertIn("overwrite", res_zh)
        self.assertTrue(Path(res_zh["overwrite"]["Path"]).is_file())

        res_ao = mg.dispatch(self._payload("read_file", {"Path": str(self.audio_only_ts)}))
        self.assertEqual(res_ao["decision"], "allow")
        self.assertIn("overwrite", res_ao)
        ao_out = Path(res_ao["overwrite"]["Path"])
        self.assertTrue(ao_out.is_file())
        self.assertEqual(ao_out.suffix.lower(), ".mp3")

    def test_oversized_video_autobridge_under_cap(self) -> None:
        big_src = self.work_dir / "oversized.mp4"
        run_ffmpeg([
            "-f", "lavfi", "-i", "testsrc2=s=320x240:d=1.5:r=15",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=1.5",
            "-c:v", "libx264", "-crf", "10", "-pix_fmt", "yuv420p", "-c:a", "aac",
            str(big_src),
        ])
        orig_direct = mg.NATIVE_DIRECT_MAX_BYTES["video"]
        orig_target = mg.BRIDGE_TARGET_MAX_BYTES["video"]
        try:
            src_size = big_src.stat().st_size
            mg.NATIVE_DIRECT_MAX_BYTES["video"] = max(20_000, src_size - 1024)
            mg.BRIDGE_TARGET_MAX_BYTES["video"] = max(20_000, src_size - 1024)
            res = mg.dispatch(self._payload("read_file", {"Path": str(big_src)}))
            self.assertEqual(res["decision"], "allow")
            self.assertIn("overwrite", res)
            out = Path(res["overwrite"]["Path"])
            self.assertTrue(out.is_file())
            self.assertLessEqual(out.stat().st_size, mg.BRIDGE_TARGET_MAX_BYTES["video"])
        finally:
            mg.NATIVE_DIRECT_MAX_BYTES["video"] = orig_direct
            mg.BRIDGE_TARGET_MAX_BYTES["video"] = orig_target

    def test_huge_file_denies_with_slice_command(self) -> None:
        res = mg.dispatch(self._payload("read_file", {"Path": str(self.huge_mp4)}))
        self.assertEqual(res["decision"], "deny")
        self.assertIn("prepare_native_media.py", res["reason"])
        self.assertIn("slice", res["reason"])

    def test_cache_pruning(self) -> None:
        prune_dir = self.temp_root / "prune_test"
        prune_dir.mkdir(parents=True, exist_ok=True)
        old_file = prune_dir / "old.mp4"
        new_file = prune_dir / "new.mp4"
        old_file.write_bytes(b"a" * 2048)
        new_file.write_bytes(b"b" * 2048)
        os.utime(old_file, (time.time() - 100, time.time() - 100))
        orig_limit = mg.CACHE_MAX_TOTAL_BYTES
        try:
            mg.CACHE_MAX_TOTAL_BYTES = 2500
            mg.prune_cache(prune_dir, keep_path=new_file)
            self.assertFalse(old_file.exists())
            self.assertTrue(new_file.exists())
        finally:
            mg.CACHE_MAX_TOTAL_BYTES = orig_limit

    def test_run_command_normal_fast_allow(self) -> None:
        t0 = time.perf_counter()
        res = mg.dispatch(self._payload("run_command", {"CommandLine": "git status"}))
        elapsed_ms = (time.perf_counter() - t0) * 1000
        self.assertEqual(res, {"decision": "allow"})
        self.assertLess(elapsed_ms, 15.0)

    def test_run_command_blocks_reflexive_inspection(self) -> None:
        for cmd in (
            "ffmpeg -i sample.mp4 frame_%03d.png",
            "ffprobe -v error -show_streams sample.mp4",
            'whisper "tone.wav" --model base',
            'python -c "import cv2; cv2.VideoCapture(\'sample.mp4\')"',
            'python -c "import av; av.open(\'legacy.flv\')"',
            'python -c "import librosa; librosa.load(\'voice.ogg\')"',
        ):
            for model in ("", "auto", "gemini-4-carbon-high", "orchestrator-v1"):
                res = mg.dispatch(self._payload("run_command", {"CommandLine": cmd}, modelName=model))
                self.assertEqual(res["decision"], "deny", f"Expected deny for cmd={cmd}, model={model}")
                self.assertIn("read_file", res["reason"])

    def test_run_command_allows_claude_and_gpt(self) -> None:
        for model in ("claude-opus-5-5-high", "gpt-5.4"):
            res = mg.dispatch(
                self._payload("run_command", {"CommandLine": "ffmpeg -i sample.mp4 frame_%03d.png"}, modelName=model)
            )
            self.assertEqual(res, {"decision": "allow"})

    def test_run_command_allows_own_scripts(self) -> None:
        res = mg.dispatch(
            self._payload(
                "run_command",
                {"CommandLine": f'python skills\\native-multimodal\\scripts\\prepare_native_media.py slice "{self.mp4_path}"'},
            )
        )
        self.assertEqual(res, {"decision": "allow"})

        chained = mg.dispatch(
            self._payload(
                "run_command",
                {"CommandLine": f'python prepare_native_media.py inspect "{self.mp4_path}" && ffmpeg -i sample.mp4 out_%03d.png'},
            )
        )
        self.assertEqual(chained["decision"], "deny")

    def test_run_command_allows_synthetic_lavfi(self) -> None:
        res = mg.dispatch(
            self._payload(
                "run_command",
                {"CommandLine": "ffmpeg -y -f lavfi -i testsrc=d=1:s=64x64 -f lavfi -i sine=f=440:d=1 sample.mp4"},
            )
        )
        self.assertEqual(res, {"decision": "allow"})

        res_mixed = mg.dispatch(
            self._payload(
                "run_command",
                {"CommandLine": "ffmpeg -f lavfi -i sine=f=440:d=1 -i sample.mp4 out.mp4"},
            )
        )
        self.assertEqual(res_mixed["decision"], "deny")

    def test_run_command_allows_explicit_user_intent(self) -> None:
        res_zh = mg.dispatch(
            self._payload(
                "run_command",
                {"CommandLine": "ffmpeg -i sample.mp4 -ss 5 out.mp4"},
                lastUserInput="帮我用ffmpeg把这个视频裁掉前5秒",
            )
        )
        self.assertEqual(res_zh, {"decision": "allow"})

        tpath = self.temp_root / "transcript_user.jsonl"
        tpath.write_text(
            json.dumps({
                "step_index": 1,
                "type": "USER_INPUT",
                "content": "<USER_REQUEST>Please convert sample.mp4 to 30fps and compress it</USER_REQUEST>",
            }) + "\n",
            encoding="utf-8",
        )
        res_t = mg.dispatch(
            self._payload(
                "run_command",
                {"CommandLine": "ffmpeg -i sample.mp4 -r 30 out.mp4"},
                lastUserInput="",
                transcriptPath=str(tpath),
            )
        )
        self.assertEqual(res_t, {"decision": "allow"})

    def test_run_command_allows_after_prior_read_file_in_transcript(self) -> None:
        tpath = self.temp_root / "transcript_read.jsonl"
        lines = [
            json.dumps({
                "step_index": 1,
                "type": "USER_INPUT",
                "content": "<USER_REQUEST>分析一下这个视频</USER_REQUEST>",
            }),
            json.dumps({
                "step_index": 2,
                "type": "PLANNER_RESPONSE",
                "tool_calls": [
                    {"name": "read_file", "args": {"Path": json.dumps(str(self.flv_path))}},
                ],
            }),
            json.dumps({"step_index": 3, "type": "GENERIC", "status": "DONE", "content": "OK"}),
        ]
        tpath.write_text("\n".join(lines) + "\n", encoding="utf-8")

        res = mg.dispatch(
            self._payload(
                "run_command",
                {"CommandLine": "ffprobe legacy.flv"},
                lastUserInput="分析一下这个视频",
                transcriptPath=str(tpath),
            )
        )
        self.assertEqual(res, {"decision": "allow"})

        # Also check denied read_file does NOT unlock ffprobe
        tpath_denied = self.temp_root / "transcript_denied.jsonl"
        denied_lines = [
            json.dumps({
                "step_index": 2,
                "type": "PLANNER_RESPONSE",
                "tool_calls": [
                    {"name": "read_file", "args": {"Path": str(self.mp4_path)}},
                ],
            }),
            json.dumps({"step_index": 3, "type": "GENERIC", "status": "ERROR", "content": "Denied by hook"}),
        ]
        tpath_denied.write_text("\n".join(denied_lines) + "\n", encoding="utf-8")
        res_denied = mg.dispatch(
            self._payload(
                "run_command",
                {"CommandLine": "ffprobe sample.mp4"},
                transcriptPath=str(tpath_denied),
            )
        )
        self.assertEqual(res_denied["decision"], "deny")

    def test_find_binary_respects_env_overrides(self) -> None:
        fake_dir = self.temp_root / "fake_ffmpeg_bin"
        fake_dir.mkdir(parents=True, exist_ok=True)
        exe_name = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
        fake_bin = fake_dir / exe_name
        fake_bin.write_bytes(b"")
        orig_dir = os.environ.get("AGY_FFMPEG_DIR")
        orig_path = os.environ.get("FFMPEG_PATH")
        try:
            os.environ["AGY_FFMPEG_DIR"] = str(fake_dir)
            os.environ.pop("FFMPEG_PATH", None)
            self.assertEqual(Path(mg.find_binary("ffmpeg") or ""), fake_bin)

            override_bin = self.temp_root / f"custom_{exe_name}"
            override_bin.write_bytes(b"")
            os.environ["FFMPEG_PATH"] = str(override_bin)
            self.assertEqual(Path(mg.find_binary("ffmpeg") or ""), override_bin)
        finally:
            if orig_dir is None:
                os.environ.pop("AGY_FFMPEG_DIR", None)
            else:
                os.environ["AGY_FFMPEG_DIR"] = orig_dir
            if orig_path is None:
                os.environ.pop("FFMPEG_PATH", None)
            else:
                os.environ["FFMPEG_PATH"] = orig_path

    def test_tilde_path_expansion_in_resolve_path_and_command(self) -> None:
        home_sub = Path.home() / "test_tilde_resolve.mp4"
        self.assertEqual(mg.resolve_path("~/test_tilde_resolve.mp4", self.work_dir), Path(os.path.abspath(str(home_sub))))

        real_home_sample = self.temp_root / "fake_home" / "clip.mp4"
        real_home_sample.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(self.mp4_path, real_home_sample)
        orig_home = os.environ.get("HOME")
        orig_userprofile = os.environ.get("USERPROFILE")
        try:
            os.environ["HOME"] = str(real_home_sample.parent)
            os.environ["USERPROFILE"] = str(real_home_sample.parent)
            res = mg.dispatch(self._payload("run_command", {"CommandLine": "ffmpeg -i ~/clip.mp4 frame_%03d.png"}))
            self.assertEqual(res["decision"], "deny")
        finally:
            if orig_home is None:
                os.environ.pop("HOME", None)
            else:
                os.environ["HOME"] = orig_home
            if orig_userprofile is None:
                os.environ.pop("USERPROFILE", None)
            else:
                os.environ["USERPROFILE"] = orig_userprofile

    def test_bridge_image_jpg_fallback_updates_overwrite_path(self) -> None:
        photo_bmp = self.work_dir / "noisy_photo.bmp"
        run_ffmpeg([
            "-f", "lavfi", "-i", "mandelbrot=size=320x240",
            "-frames:v", "1", str(photo_bmp),
        ])
        st = photo_bmp.stat()
        cache_png = mg.compute_cache_path(photo_bmp, st, ".png")
        cache_jpg = cache_png.with_suffix(".jpg")
        cache_png.unlink(missing_ok=True)
        cache_jpg.unlink(missing_ok=True)

        # Pick a ceiling strictly between the compressed JPG size and lossless PNG size
        self.assertTrue(mg.run_ffmpeg_transcode(["-i", str(photo_bmp), "-frames:v", "1"], cache_png, 50 * 1024 * 1024))
        self.assertTrue(mg.run_ffmpeg_transcode(["-i", str(photo_bmp), "-frames:v", "1", "-q:v", "3"], cache_jpg, 50 * 1024 * 1024))
        png_sz = cache_png.stat().st_size
        jpg_sz = cache_jpg.stat().st_size
        cache_png.unlink(missing_ok=True)
        cache_jpg.unlink(missing_ok=True)
        self.assertLess(jpg_sz, png_sz)

        orig_target = mg.BRIDGE_TARGET_MAX_BYTES["image"]
        orig_direct = mg.NATIVE_DIRECT_MAX_BYTES["image"]
        try:
            mid_cap = (jpg_sz + png_sz) // 2
            mg.BRIDGE_TARGET_MAX_BYTES["image"] = mid_cap
            mg.NATIVE_DIRECT_MAX_BYTES["image"] = min(orig_direct, jpg_sz - 1)
            res = mg.dispatch(self._payload("read_file", {"Path": str(photo_bmp)}))
            self.assertEqual(res["decision"], "allow")
            self.assertIn("overwrite", res)
            out_path = Path(res["overwrite"]["Path"])
            self.assertEqual(out_path.suffix.lower(), ".jpg")
            self.assertTrue(out_path.is_file())
            self.assertLessEqual(out_path.stat().st_size, mid_cap)
        finally:
            mg.BRIDGE_TARGET_MAX_BYTES["image"] = orig_target
            mg.NATIVE_DIRECT_MAX_BYTES["image"] = orig_direct

    def test_shell_execution_and_failsafe_malformed_json(self) -> None:
        hooks_data = json.loads(HOOKS_JSON.read_text(encoding="utf-8"))
        cmd_str = hooks_data["native-multimodal-guard"]["PreToolUse"][1]["hooks"][0]["command"]
        shell_cmd: str | list[str] = f'cmd /c "{cmd_str}"' if os.name == "nt" else ["sh", "-c", cmd_str]

        proc_bad = subprocess.run(
            shell_cmd,
            cwd=str(PLUGIN_ROOT),
            input="{not valid json",
            capture_output=True,
            text=True,
            encoding="utf-8",
            shell=False,
            timeout=10,
        )
        self.assertEqual(proc_bad.returncode, 0, f"stderr={proc_bad.stderr}")
        self.assertEqual(json.loads(proc_bad.stdout), {"decision": "allow"})

        payload = self._payload("run_command", {"CommandLine": "ffmpeg -i sample.mp4 frame_%03d.png"})
        proc_deny = subprocess.run(
            shell_cmd,
            cwd=str(PLUGIN_ROOT),
            input=json.dumps(payload, ensure_ascii=False),
            capture_output=True,
            text=True,
            encoding="utf-8",
            shell=False,
            timeout=10,
        )
        self.assertEqual(proc_deny.returncode, 0, f"stderr={proc_deny.stderr}")
        out = json.loads(proc_deny.stdout)
        self.assertEqual(out["decision"], "deny")
        self.assertIn("原生理解视频", out["reason"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
