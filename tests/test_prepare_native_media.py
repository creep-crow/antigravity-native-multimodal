#!/usr/bin/env python3
"""Cross-platform unit & CLI tests for prepare_native_media.py and plugin artifacts."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "skills" / "native-multimodal" / "scripts" / "prepare_native_media.py"
SCRIPT_DIR = SCRIPT.parent

sys.path.insert(0, str(SCRIPT_DIR))
import prepare_native_media as pnm  # noqa: E402


def write_minimal_ico(path: Path) -> None:
    """Write a valid 16x16 32-bpp RGBA Windows .ico file."""
    w, h = 16, 16
    bih = struct.pack("<IIIHHIIIIII", 40, w, h * 2, 1, 32, 0, w * h * 4, 0, 0, 0, 0)
    xor_pixels = b"\x00\x00\xff\xff" * (w * h)
    and_mask = b"\x00" * (w * h // 8)
    dib = bih + xor_pixels + and_mask
    icon_dir = struct.pack("<HHH", 0, 1, 1)
    entry = struct.pack("<BBBBHHII", w, h, 0, 0, 1, 32, len(dib), 6 + 16)
    path.write_bytes(icon_dir + entry + dib)


class PrepareNativeMediaTests(unittest.TestCase):
    temp_dir_obj: tempfile.TemporaryDirectory[str]
    test_dir: Path
    fixtures: dict[str, Path]

    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir_obj = tempfile.TemporaryDirectory(prefix="test_prepare_media_")
        cls.test_dir = Path(cls.temp_dir_obj.name)
        cls.fixtures = cls._make_fixtures(cls.test_dir)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp_dir_obj.cleanup()

    @staticmethod
    def _run_ffmpeg(args: list[str]) -> None:
        ffmpeg = pnm.require_binary("ffmpeg")
        proc = subprocess.run(
            [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"ffmpeg failed: {proc.stderr}")

    @classmethod
    def _make_fixtures(cls, test_dir: Path) -> dict[str, Path]:
        fixtures: dict[str, Path] = {}

        mp4 = test_dir / "sample_native.mp4"
        cls._run_ffmpeg([
            "-f", "lavfi", "-i", "color=c=red:s=320x240:d=2",
            "-f", "lavfi", "-i", "color=c=blue:s=320x240:d=2",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
            "-f", "lavfi", "-i", "sine=frequency=880:duration=2",
            "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v];[2:a][3:a]concat=n=2:v=0:a=1[a]",
            "-map", "[v]", "-map", "[a]",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
            str(mp4),
        ])
        fixtures["mp4"] = mp4

        mkv = test_dir / "sample_remux.mkv"
        cls._run_ffmpeg(["-i", str(mp4), "-c", "copy", str(mkv)])
        fixtures["mkv"] = mkv

        flv = test_dir / "测试_非原生封装.flv"
        cls._run_ffmpeg(["-i", str(mp4), "-c:v", "flv1", "-c:a", "mp3", "-ar", "44100", str(flv)])
        fixtures["flv"] = flv

        avi = test_dir / "测试_非原生视频.avi"
        cls._run_ffmpeg(["-i", str(mp4), "-c:v", "mpeg4", "-c:a", "mp2", str(avi)])
        fixtures["avi"] = avi

        wav = test_dir / "sample_audio.wav"
        cls._run_ffmpeg(["-f", "lavfi", "-i", "sine=frequency=523:duration=8", "-c:a", "pcm_s16le", str(wav)])
        fixtures["wav"] = wav

        flac = test_dir / "sample_audio.flac"
        cls._run_ffmpeg(["-i", str(wav), "-c:a", "flac", str(flac)])
        fixtures["flac"] = flac

        aac = test_dir / "sample_audio.aac"
        cls._run_ffmpeg(["-i", str(wav), "-c:a", "aac", str(aac)])
        fixtures["aac"] = aac

        cover_png = test_dir / "cover.png"
        cls._run_ffmpeg(["-f", "lavfi", "-i", "color=c=green:s=128x128:d=0.1", "-frames:v", "1", str(cover_png)])

        cover_mp3 = test_dir / "sample_cover_audio.mp3"
        cls._run_ffmpeg([
            "-i", str(wav), "-i", str(cover_png),
            "-map", "0:a", "-map", "1:v",
            "-c:a", "libmp3lame", "-c:v", "png", "-disposition:v:0", "attached_pic",
            str(cover_mp3),
        ])
        fixtures["cover_mp3"] = cover_mp3

        bmp = test_dir / "sample_img.bmp"
        cls._run_ffmpeg(["-f", "lavfi", "-i", "testsrc=size=640x360:duration=0.1", "-frames:v", "1", str(bmp)])
        fixtures["bmp"] = bmp

        ico = test_dir / "sample_unsafe.ico"
        write_minimal_ico(ico)
        fixtures["ico"] = ico

        long_mp4 = test_dir / "sample_long.mp4"
        cls._run_ffmpeg([
            "-f", "lavfi", "-i", "testsrc=size=480x270:rate=24:duration=10",
            "-f", "lavfi", "-i", "sine=frequency=660:duration=10",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
            str(long_mp4),
        ])
        fixtures["long_mp4"] = long_mp4

        pdf = test_dir / "sample_doc.pdf"
        pdf.write_bytes(b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF\n")
        fixtures["pdf"] = pdf

        return fixtures

    def run_cli(self, *args: str) -> dict:
        cmd = [sys.executable, "-X", "utf8", str(SCRIPT), *args]
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
        self.assertEqual(
            proc.returncode,
            0,
            f"CLI failed ({proc.returncode}): {' '.join(cmd)}\nSTDERR:\n{proc.stderr}\nSTDOUT:\n{proc.stdout}",
        )
        return json.loads(proc.stdout)

    def test_plugin_artifacts_and_manifest(self) -> None:
        pjson = json.loads((REPO_ROOT / "plugin.json").read_text(encoding="utf-8"))
        allowed_keys = {
            "name", "displayName", "version", "description", "author",
            "logo", "suggestedPrompts", "homepage", "repository", "disabled",
            "license", "keywords",
        }
        extra_keys = set(pjson.keys()) - allowed_keys
        self.assertFalse(extra_keys, f"Undocumented top-level plugin.json keys: {extra_keys}")
        self.assertEqual(pjson["name"], "native-multimodal")
        self.assertEqual(len(pjson["suggestedPrompts"]), 3)

        agents_bytes = (REPO_ROOT / "rules" / "AGENTS.md").read_bytes()
        self.assertFalse(agents_bytes.startswith(b"---"), "AGENTS.md must not have YAML frontmatter")
        self.assertLess(len(agents_bytes), 3000, f"AGENTS.md too large: {len(agents_bytes)} bytes")

        svg_root = ET.fromstring((REPO_ROOT / "assets" / "logo.svg").read_text(encoding="utf-8"))
        self.assertEqual(svg_root.attrib.get("viewBox"), "0 0 128 128")

    def test_require_binary_env_overrides(self) -> None:
        fake_dir = self.test_dir / "fake_bin"
        fake_dir.mkdir(parents=True, exist_ok=True)
        exe_name = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
        fake_bin = fake_dir / exe_name
        fake_bin.write_bytes(b"")
        orig_dir = os.environ.get("AGY_FFMPEG_DIR")
        orig_path = os.environ.get("FFMPEG_PATH")
        try:
            os.environ["AGY_FFMPEG_DIR"] = str(fake_dir)
            os.environ.pop("FFMPEG_PATH", None)
            self.assertEqual(Path(pnm.require_binary("ffmpeg")), fake_bin)

            override_bin = self.test_dir / f"override_{exe_name}"
            override_bin.write_bytes(b"")
            os.environ["FFMPEG_PATH"] = str(override_bin)
            self.assertEqual(Path(pnm.require_binary("ffmpeg")), override_bin)
        finally:
            if orig_dir is None:
                os.environ.pop("AGY_FFMPEG_DIR", None)
            else:
                os.environ["AGY_FFMPEG_DIR"] = orig_dir
            if orig_path is None:
                os.environ.pop("FFMPEG_PATH", None)
            else:
                os.environ["FFMPEG_PATH"] = orig_path

    def test_inspect_direct_and_bridge_formats(self) -> None:
        fx = self.fixtures
        self.assertEqual(self.run_cli("inspect", str(fx["mp4"]))["read_file_status"], "direct")
        self.assertEqual(self.run_cli("inspect", str(fx["mkv"]))["read_file_status"], "direct")
        self.assertEqual(self.run_cli("inspect", str(fx["flv"]))["read_file_status"], "needs_normalize")
        self.assertEqual(self.run_cli("inspect", str(fx["avi"]))["read_file_status"], "needs_normalize")
        self.assertEqual(self.run_cli("inspect", str(fx["cover_mp3"]))["read_file_status"], "direct")
        self.assertEqual(self.run_cli("inspect", str(fx["flac"]))["read_file_status"], "needs_normalize")
        self.assertEqual(self.run_cli("inspect", str(fx["aac"]))["read_file_status"], "needs_normalize")
        self.assertEqual(self.run_cli("inspect", str(fx["bmp"]))["read_file_status"], "direct")
        self.assertEqual(self.run_cli("inspect", str(fx["ico"]))["read_file_status"], "needs_normalize")
        self.assertEqual(self.run_cli("inspect", str(fx["pdf"]))["read_file_status"], "direct")

    def test_normalize_remux_and_transcode(self) -> None:
        fx = self.fixtures

        n_mkv = self.run_cli("normalize", str(fx["mkv"]))
        self.assertEqual(n_mkv["method"], "remux")
        self.assertEqual(Path(n_mkv["output"]).suffix.lower(), ".mp4")

        avi_out = self.test_dir / "测试_输出.mp4"
        n_avi = self.run_cli("normalize", str(fx["avi"]), "--out", str(avi_out), "--max-mb", "0.15")
        self.assertEqual(n_avi["method"], "transcode")
        self.assertLessEqual(n_avi["size_bytes"], int(0.15 * 1024 * 1024))
        avi_probe = self.run_cli("inspect", str(avi_out))
        self.assertTrue(avi_probe["has_video"] and avi_probe["has_audio"])
        self.assertEqual(avi_probe["read_file_status"], "direct")

        n_flv = self.run_cli("normalize", str(fx["flv"]), "--max-mb", "0.20")
        self.assertEqual(Path(n_flv["output"]).suffix.lower(), ".mp4")
        self.assertLessEqual(n_flv["size_bytes"], int(0.20 * 1024 * 1024))

        n_flac = self.run_cli("normalize", str(fx["flac"]), "--max-mb", "0.10")
        self.assertEqual(Path(n_flac["output"]).suffix.lower(), ".mp3")
        self.assertLessEqual(n_flac["size_bytes"], int(0.10 * 1024 * 1024))

        aac_m4a_out = self.test_dir / "sample_remuxed.m4a"
        n_aac = self.run_cli("normalize", str(fx["aac"]), "--out", str(aac_m4a_out))
        self.assertEqual(n_aac["method"], "remux")
        self.assertEqual(Path(n_aac["output"]).suffix.lower(), ".m4a")

        n_ico = self.run_cli("normalize", str(fx["ico"]), "--max-mb", "0.10")
        self.assertEqual(Path(n_ico["output"]).suffix.lower(), ".png")
        self.assertLessEqual(n_ico["size_bytes"], int(0.10 * 1024 * 1024))

    def test_slice_video_and_audio_with_tilde(self) -> None:
        fx = self.fixtures

        slice_dir = self.test_dir / "video_slices"
        s_vid = self.run_cli(
            "slice", str(fx["long_mp4"]),
            "--chunk-sec", "4", "--overlap-sec", "1",
            "--start", "1", "--end", "00:00:09",
            "--burn-timecode", "--max-mb", "0.25",
            "--out-dir", str(slice_dir),
        )
        self.assertEqual(s_vid["chunk_count"], 3)
        self.assertTrue(s_vid["burn_timecode_applied"])
        for ch in s_vid["chunks"]:
            self.assertLessEqual(ch["size_bytes"], int(0.25 * 1024 * 1024))
            p_ch = self.run_cli("inspect", ch["path"])
            self.assertTrue(p_ch["has_video"] and p_ch["has_audio"])
            self.assertEqual(p_ch["read_file_status"], "direct")

        a_slice_dir = self.test_dir / "audio_slices"
        s_aud = self.run_cli(
            "slice", str(fx["cover_mp3"]),
            "--chunk-sec", "3", "--overlap-sec", "1",
            "--max-mb", "0.05",
            "--out-dir", str(a_slice_dir),
        )
        self.assertEqual(s_aud["modality"], "audio")
        self.assertGreaterEqual(s_aud["chunk_count"], 3)
        for ch in s_aud["chunks"]:
            self.assertEqual(Path(ch["path"]).suffix.lower(), ".mp3")
            self.assertLessEqual(ch["size_bytes"], int(0.05 * 1024 * 1024))

        # Verify CLI expands ~/ paths
        fake_home = self.test_dir / "fake_home"
        fake_home.mkdir(parents=True, exist_ok=True)
        shutil.copy2(fx["mp4"], fake_home / "home_clip.mp4")
        env = dict(os.environ)
        env["HOME"] = str(fake_home)
        env["USERPROFILE"] = str(fake_home)
        proc = subprocess.run(
            [sys.executable, "-X", "utf8", str(SCRIPT), "inspect", "~/home_clip.mp4"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            timeout=30,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout)["read_file_status"], "direct")


if __name__ == "__main__":
    unittest.main(verbosity=2)
