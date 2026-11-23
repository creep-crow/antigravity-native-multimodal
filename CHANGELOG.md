# Changelog

All notable changes to **Native Multimodal (`antigravity-native-multimodal`)** are documented here.

## [1.0.0] - 2026-11-24

### Added
- **Always-on multimodal rules** (`rules/AGENTS.md`): clarifies that `read_file`'s `800 lines / 46,080 bytes` cap applies only to text, documents the `100 MiB` (`104,857,600` bytes) media ceiling, prohibits reflexive `ffmpeg`/`ffprobe`/`whisper`/`cv2` analysis on unread media, and defines legitimate exceptions.
- **PreToolUse runtime guard & transparent auto-bridge** (`hooks.json`, `hooks/multimodal_guard.py`):
  - Fast-path allow (`< 1 ms` in-process) for non-media reads and normal shell commands.
  - Automatic lossless remux or fast transcode into content-addressed cache (`agy-native-multimodal-cache`) for Windows-rejected / API-unsafe containers and codecs (`.flv`, `.wmv`, `.ts`, `.flac`, `.aac`, `.aiff`, `.wma`, `.ico`, `.avif`, `.heic`, `.heif`, HEVC/AC3, etc.) and moderately oversized files (`<= 250 MB`, `<= 20 min`).
  - Automatic sanitization of invalid `StartLine`/`EndLine`/`ByteOffset` arguments on binary media reads.
  - Reflexive `ffmpeg`/`ffprobe`/`whisper`/`cv2`/`librosa`/`pydub`/`moviepy`/`imageio`/`pyav` command blocking for Gemini/orchestrator models, while allowing `prepare_native_media.py`, `-f lavfi` synthetic generation, explicit user processing requests, and already-read media.
- **Media preparation skill & CLI** (`skills/native-multimodal/SKILL.md`, `skills/native-multimodal/scripts/prepare_native_media.py`): `inspect`, `normalize`, and `slice` (with optional global `HH:MM:SS` burned timecode and overlap) for oversized or long recordings.
- **Cross-platform packaging**: portable Python hook invocation (`python3` / `python`), `AGY_FFMPEG_DIR` / `FFMPEG_PATH` / `FFPROBE_PATH` lookup, bilingual documentation (`README.md`, `README.zh-CN.md`), MIT license, installers (`scripts/install.sh`, `scripts/install.ps1`), and multi-OS GitHub Actions CI (`ubuntu`, `macos`, `windows`).
