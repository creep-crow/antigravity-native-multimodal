# 原生多模态读取规则（native-multimodal）

`read_file` 工具描述中的 `800 行 / 46,080 字节` 截断**仅适用于纯文本文件**，不影响音视频、图片与 PDF；二进制媒体的单次硬上限为 `100 MiB`（`104,857,600` 字节）。

## 1. 直接用 `read_file` 原生理解媒体

当前 Gemini 模型具备原生音画同传理解能力，遇到以下文件时**直接调用 `read_file(Path="...")`**，同一轮可并行读取多个媒体或分片：

- **视频（同时感知连续画面动态与音轨）**：`.mp4` `.m4v` `.webm` `.mov` `.mkv` `.avi`（标准编码 H.264/VP8/VP9/MPEG4 + AAC/MP3/Opus）。
- **音频**：`.mp3` `.wav` `.m4a` `.ogg` `.opus`。
- **图片与文档**：`.png` `.jpg` `.jpeg` `.webp` `.gif` `.bmp` `.tif` `.tiff` 与 `.pdf`。
- **禁止传行号/偏移参数**：读取上述媒体或 PDF 时**不要传 `StartLine`、`EndLine`、`ByteOffset`**（合法值会被静默忽略并附带整份媒体，非法值如 `< 1` 或 `StartLine > EndLine` 会直接报错）。

## 2. 禁止反射性退化为抽帧或转写

- **禁止**用 `ffmpeg`/`cv2`/`imageio`/`moviepy` 对视频抽帧或转图片拼图来代替观看视频。
- **禁止**用 `whisper`/`faster-whisper`/`librosa`/`pydub` 抽取或转写音频来代替聆听音轨/音频文件。
- **禁止**只用 `ffprobe` 查看音视频而回避 `read_file` 原生感知。
- 遇到 Windows 端不被 `read_file` 直接接受或易出错的格式（`.flac` `.aac` `.aiff` `.wma` `.flv` `.wmv` `.ts` `.ico` `.avif` `.heic` `.heif`、非常规编码或损坏容器），应直接依赖插件桥接或使用 `native-multimodal` 技能转换为原生 `.mp4`/`.mp3`/`.png` 后再 `read_file`。

## 3. 合法例外

仅在以下场景允许对媒体运行命令行工具：

1. **用户明确要求**剪辑、裁剪、转码、压缩、合并、加字幕、导出渲染，或明确点名使用 `ffmpeg`/`ffprobe`/`whisper`。
2. **文件过大或过长**（如超过 `100 MiB` 硬上限，或超过 `45 MB` 且长于 5 分钟的视频/音频），调用 `native-multimodal` 技能脚本 `prepare_native_media.py` 做 `normalize` 或 `slice` 分片生成原生 `.mp4`/`.mp3`，再并行 `read_file`。
3. **批量盘点纯技术元数据**（如统计目录内十几个视频的分辨率、时长、码率报表），且无需理解音画内容。
4. **当前切换为非原生多模态模型**（如 `claude-*`、`gpt-*` 不支持原生音视频读取时）。
