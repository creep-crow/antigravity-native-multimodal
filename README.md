<p align="center">
  <img src="./assets/logo.svg" width="96" height="96" alt="Native Multimodal logo" />
</p>

<h1 align="center">antigravity-native-multimodal</h1>

<p align="center">
  <strong>Unlock true native video, audio, image, and PDF perception for Gemini agents in Google Antigravity.</strong>
</p>

<p align="center">
  <a href="https://github.com/creep-crow/antigravity-native-multimodal/actions/workflows/ci.yml"><img src="https://github.com/creep-crow/antigravity-native-multimodal/actions/workflows/ci.yml/badge.svg" alt="CI" /></a>
  <a href="./LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue.svg" alt="MIT License" /></a>
  <img src="https://img.shields.io/badge/platform-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey" alt="Platforms" />
</p>

<p align="center">
  English | <a href="./README.zh-CN.md">简体中文</a>
</p>

---

## Why This Plugin Exists

Gemini models natively understand **video (continuous visual motion + audio track together)**, **audio**, **images**, and **PDFs** when attached through Antigravity's `read_file` tool. Yet in practice, agents often fall back to `ffmpeg` frame extraction, `ffprobe`, `whisper`, or `cv2` scripts—discarding temporal motion, acoustic cues, and audio-visual synchronization while wasting turns and approval prompts.

Empirical probing of Antigravity's `language_server` and `read_file` pipeline revealed three root causes:

1. **Misleading `read_file` tool schema**: The `800 lines / 46,080 bytes` truncation notice applies **only to text files**—not binary media. `ReadFileTool` actually allows up to **`100 MiB` (`104,857,600` bytes)** per media file, and valid H.264 + AAC `.mp4` files up to `80 MiB` work directly with full video + audio perception.
2. **Local MIME & container traps**: On Windows in particular, `ReadFileTool` rejects valid audio like `.flac` (`audio/x-flac`), `.aac` (`audio/vnd.dlna.adts`), `.aiff`, and `.wma`, as well as `.flv` / `.avif` / `.heic` / `.heif` (`application/octet-stream`), while `.ico` (`image/x-icon`) passes the local `image/*` check and causes a backend HTTP 400 error.
3. **Lack of runtime guardrails**: Pretrained CLI habits still fire `ffmpeg` / `whisper` before attempting `read_file`, and invalid `StartLine` / `EndLine` / `ByteOffset` arguments still trip parameter validation even on binary files.

**`native-multimodal`** fixes all three layers without breaking legitimate editing workflows.

---

## Architecture

| Layer | File | What It Does |
| :--- | :--- | :--- |
| **Always-On Rule** | [`rules/AGENTS.md`](./rules/AGENTS.md) | Clarifies text vs. `100 MiB` media caps, enforces direct `read_file` usage without line/byte params, forbids reflexive `ffmpeg`/`whisper`/`cv2` analysis, and documents legitimate exceptions. |
| **Runtime Guard & Auto-Bridge** | [`hooks.json`](./hooks.json)<br>[`hooks/multimodal_guard.py`](./hooks/multimodal_guard.py) | `PreToolUse` hook (`read_file\|view_file` & `run_command`): transparently bridges unsupported/unsafe formats & moderately oversized media (`<= 250 MB`, `<= 20 min`) into cached native `.mp4`/`.mp3`/`.png`; sanitizes invalid line/offset params; blocks reflexive media inspection on unread files for Gemini/orchestrator models while allowing explicit user edit requests, `-f lavfi` generation, and already-read files. |
| **Preparation Skill & CLI** | [`skills/native-multimodal/SKILL.md`](./skills/native-multimodal/SKILL.md)<br>[`prepare_native_media.py`](./skills/native-multimodal/scripts/prepare_native_media.py) | Standalone Python 3 CLI (`inspect`, `normalize`, `slice`) for lossless remuxing, bounded transcoding, and overlapping native video/audio chapter slicing with optional global `HH:MM:SS` burned-in timecodes. |

---

## Empirical Antigravity Capability Matrix

| Modality | Direct `read_file` Support | Auto-Bridged / Normalized | Notes |
| :--- | :--- | :--- | :--- |
| **Video** | `.mp4`, `.m4v`, `.webm`, `.mov`, `.mkv`, `.avi` *(H.264/VP8/VP9/MPEG4 + AAC/MP3/Opus)* | `.flv`, `.wmv`, `.ts`/`.mts`, HEVC/AC3/DTS/10-bit, corrupt containers | Canonical target: `.mp4` (`h264 yuv420p` + `aac`). Preserves motion + audio. |
| **Audio** | `.mp3`, `.wav`, `.m4a`, `.ogg`, `.opus` | `.flac`, `.aac` (ADTS), `.aiff`, `.wma` | Windows `ReadFileTool` rejects local MIME types for `.flac`/`.aac`/`.aiff`/`.wma`; bridged to `.mp3`/`.m4a`/`.wav`. |
| **Image** | `.png`, `.jpg`, `.jpeg`, `.webp`, `.gif`, `.bmp`, `.tif`, `.tiff` | `.ico` *(API 400 error if raw)*, `.avif`, `.heic`, `.heif`, `.tga` | Bridged to `.png` (with `.jpg` fallback if size-constrained). |
| **Document** | `.pdf` | — | Direct `read_file` up to `100 MiB`. |

- **Hard `read_file` limit**: `100 MiB` (`104,857,600` bytes).
- **Recommended single-read target**: `<= 45 MB` and `<= 180–300s` per read for fast upload and crisp temporal fidelity.

---

## Quick Install

### Prerequisites
- **Python 3.10+** (`python3` or `python`)
- **`ffmpeg` & `ffprobe`** on `PATH` (needed only when auto-bridging exotic formats or slicing oversized media)

### Option 1: Install via Antigravity CLI

```bash
agy plugin install https://github.com/creep-crow/antigravity-native-multimodal
```

### Option 2: Clone & Run Installer Script

**macOS / Linux:**
```bash
git clone https://github.com/creep-crow/antigravity-native-multimodal.git
cd antigravity-native-multimodal
bash scripts/install.sh
```

**Windows (PowerShell):**
```powershell
git clone https://github.com/creep-crow/antigravity-native-multimodal.git
cd antigravity-native-multimodal
powershell -ExecutionPolicy Bypass -File scripts\install.ps1
```

Once installed into `~/.gemini/config/plugins/native-multimodal/`, toggle it anytime in Antigravity **Customizations / Plugins** or via:
```bash
agy plugin enable native-multimodal
agy plugin disable native-multimodal
```

---

## CLI Usage (`prepare_native_media.py`)

For long recordings (`> 5–20 min`) or very large files (`> 250 MB`), the `native-multimodal` skill guides the agent to run:

```bash
# 1. Inspect container, streams, codecs, and read_file compatibility
python3 -X utf8 skills/native-multimodal/scripts/prepare_native_media.py inspect video.mkv

# 2. Losslessly remux (-c copy) or transcode under 45 MB while keeping motion + audio
python3 -X utf8 skills/native-multimodal/scripts/prepare_native_media.py normalize audio.flac --max-mb 45

# 3. Slice long video into 180s overlapping chunks with burned global HH:MM:SS timecodes
python3 -X utf8 skills/native-multimodal/scripts/prepare_native_media.py slice lecture.mp4 --chunk-sec 180 --overlap-sec 3 --burn-timecode --max-mb 45
```

---

## Environment Variables (Optional)

| Variable | Purpose |
| :--- | :--- |
| `AGY_FFMPEG_DIR` | Directory containing `ffmpeg` and `ffprobe` executables if not on `PATH`. |
| `FFMPEG_PATH` / `FFPROBE_PATH` | Explicit path to `ffmpeg` or `ffprobe` binary. |
| `AGY_MM_CACHE_DIR` | Override the default temp cache directory (`agy-native-multimodal-cache`). |

---

## FAQ

- **Does this block me when I actually want `ffmpeg` to trim, convert, or render a video?**  
  No. The `run_command` hook inspects `lastUserInput` and the latest transcript `USER_INPUT`, automatically allowing `ffmpeg`/`ffprobe`/`whisper` whenever you ask to edit/convert/cut/merge/export/extract or mention those tools explicitly—as well as after the agent has already read the media via `read_file`.
- **Does it interfere with non-multimodal models (like Claude or GPT)?**  
  No. When `modelName` starts with `claude` or `gpt`, the `run_command` media guard stays out of the way.
- **What is the overhead for normal text/code tasks?**  
  Non-media `read_file` calls and normal shell commands return `{"decision": "allow"}` immediately using lazy imports (`< 1 ms` inside Python).

---

## Running Tests

```bash
python3 -X utf8 -m unittest discover -s hooks -p "test_*.py" -v
python3 -X utf8 -m unittest discover -s tests -p "test_*.py" -v
```

## License

[MIT](./LICENSE) © 2026 Creep crow
