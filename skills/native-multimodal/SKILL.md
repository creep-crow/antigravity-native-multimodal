---
name: native-multimodal
description: >-
  Use when reading, inspecting, normalizing, or slicing video, audio, image, or PDF
  files for Antigravity's native multimodal read_file tool — especially for
  oversized media (>45 MB or >100 MiB), long recordings needing time-stamped
  parallel chunks, or formats that Windows read_file rejects or mis-handles
  (.flac, .aac, .aiff, .wma, .flv, .wmv, .ts, .ico, .avif, .heic, .heif, or
  non-H.264/HEVC containers).
---

# Native Multimodal Media Preparation

Antigravity's `read_file` attaches binary media directly to Gemini models. Its
`800 lines / 46,080 bytes` cap applies **only to text files**. For video and
audio, Gemini perceives **continuous visual motion AND the full audio track**
natively without `ffmpeg` frame extraction or `whisper` transcription.

Use [prepare_native_media.py](./scripts/prepare_native_media.py) only when a
media file is in an incompatible format/codec or too large/long for a single
`read_file` call.

## Empirical Antigravity Capability Matrix

| Modality | Direct `read_file` Formats | Must Bridge / Normalize | Notes |
| :--- | :--- | :--- | :--- |
| **Video** | `.mp4`, `.m4v`, `.webm`, `.mov`, `.mkv`, `.avi` (H.264/VP8/VP9/MPEG4 + AAC/MP3/Opus) | `.flv` (`octet-stream`), `.wmv`, `.ts`/`.mts`, HEVC/AC3/DTS/10-bit or corrupt containers | Target canonical `.mp4` (`h264 yuv420p` + `aac`). Preserves both motion and audio. |
| **Audio** | `.mp3`, `.wav`, `.m4a`, `.ogg`, `.opus` | `.flac` (`audio/x-flac`), `.aac` (`audio/vnd.dlna.adts`), `.aiff`, `.wma` | Windows `ReadFileTool` rejects these local MIME types; convert to `.mp3` / `.m4a` / `.wav`. |
| **Image** | `.png`, `.jpg`, `.jpeg`, `.webp`, `.gif`, `.bmp`, `.tif`, `.tiff` | `.ico` (crashes backend API!), `.avif`, `.heic`, `.heif`, `.tga` | Images > 5 MiB / 2000px are auto-compressed by `ReadFileTool`. Bridge unsafe formats to `.png`. |
| **Document** | `.pdf` | — | Direct `read_file` up to hard limit. |

### Size Limits & Parameters

- **Hard `read_file` limit**: `100 MiB` (`104,857,600` bytes). Valid `.mp4` files up to `80 MiB` are verified to work directly.
- **Recommended single-read target**: `<= 45 MB` and `<= 180–300s` per read for fast upload and crisp temporal detail.
- **Never pass `StartLine`, `EndLine`, or `ByteOffset` for media/PDF**: valid values are ignored (whole file attached), and invalid values (`< 1`, `StartLine > EndLine`, `ByteOffset < 0`) fail argument validation.

## Workflow

### 1. Inspect unfamiliar or large media

```powershell
python -X utf8 "<skill_dir>\scripts\prepare_native_media.py" inspect "<media_path>"
```

Check `read_file_status` in the JSON output:
- `"direct"` -> Call `read_file(Path="<media_path>")` immediately (do not extract frames or transcribe).
- `"needs_normalize"` -> Run `normalize`.
- `"needs_slice"` -> Run `slice`.

### 2. Normalize unsupported formats or moderately oversized files

Losslessly remuxes (`-c copy`) when streams are already native-compatible (e.g. H.264+AAC inside `.mkv` or `.flv` -> `.mp4`, or ADTS `.aac` -> `.m4a`), or transcodes into `.mp4` (H.264 `yuv420p` + AAC, preserving motion + audio), `.mp3`/`.wav`, or `.png` strictly under `--max-mb` (default `45`):

```powershell
python -X utf8 "<skill_dir>\scripts\prepare_native_media.py" normalize "<media_path>" --max-mb 45
```

Optional `--out "<output_path>"` specifies the output file (e.g. `.m4a` or `.wav` for audio). Verify `"under_limit": true` in the JSON output, then call `read_file(Path="<output>")`.

### 3. Slice long or huge video/audio for parallel `read_file`

Splits long recordings into overlapping native `.mp4` or `.mp3` chunks strictly under `--max-mb`, optionally burning a subtle global `HH:MM:SS` timecode (`--burn-timecode`) for exact timestamp grounding:

```powershell
python -X utf8 "<skill_dir>\scripts\prepare_native_media.py" slice "<media_path>" --chunk-sec 180 --overlap-sec 3 --burn-timecode --max-mb 45
```

Optional range flags: `--start 00:05:00 --end 00:12:00` (accepts seconds or `HH:MM:SS`) and `--out-dir "<dir>"`.

After slicing, inspect the `"chunks"` array in JSON output and call `read_file(Path=...)` **in parallel** across the chunks you need to analyze.

## Validation

- Confirm every generated file exists and satisfies `size_bytes <= max_mb * 1024 * 1024`.
- Confirm video outputs retain both `has_video` and `has_audio` (when the source had audio).
- Do not run `ffmpeg` frame extraction (`fps=...`, `frame_%03d.png`) or `whisper` unless the user explicitly asked for frame images or standalone subtitle/transcription files.
