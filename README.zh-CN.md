<p align="center">
  <img src="./assets/logo.svg" width="96" height="96" alt="Native Multimodal logo" />
</p>

<h1 align="center">antigravity-native-multimodal</h1>

<p align="center">
  <strong>让 Google Antigravity 中的 Gemini Agent 真正使用原生多模态能力理解视频、音频、图片与 PDF。</strong>
</p>

<p align="center">
  <a href="https://github.com/creep-crow/antigravity-native-multimodal/actions/workflows/ci.yml"><img src="https://github.com/creep-crow/antigravity-native-multimodal/actions/workflows/ci.yml/badge.svg" alt="CI" /></a>
  <a href="./LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue.svg" alt="MIT License" /></a>
  <img src="https://img.shields.io/badge/platform-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey" alt="Platforms" />
</p>

<p align="center">
  <a href="./README.md">English</a> | 简体中文
</p>

---

## 为什么需要这个插件

Gemini 模型本身支持通过 Antigravity 的 `read_file` 工具直接原生感知**视频（连续画面动态 + 音轨）**、**音频**、**图片**与 **PDF**。但在实际使用中，模型经常会退化为调用 `ffmpeg` 抽帧、`ffprobe` 探针、`whisper` 转写或 `cv2` 脚本——不仅丢失连续动态、声学语气与音画同步信息，还会产生额外回合和命令审批延迟。

通过对 Antigravity `language_server` 与 `read_file` 执行链路的实测分析，根因主要有三点：

1. **工具描述误导**：`read_file` 提示的 `800 lines / 46,080 bytes` 截断**仅作用于纯文本**，二进制媒体单文件硬上限实际上是 **`100 MiB`（`104,857,600` 字节）**，有效 H.264 + AAC 的 `.mp4` 在 `80 MiB` 内可稳定原生音画同传。
2. **本地 MIME 与容器陷阱**：尤其在 Windows 上，`ReadFileTool` 会拒绝 `.flac`（`audio/x-flac`）、`.aac`（`audio/vnd.dlna.adts`）、`.aiff`、`.wma` 以及 `.flv` / `.avif` / `.heic` / `.heif`（`application/octet-stream`）；而 `.ico`（`image/x-icon`）虽能通过本地 `image/*` 检查，却会导致模型后端返回 HTTP 400 错误。
3. **缺少运行时护栏**：预训练习惯会让模型在未调用 `read_file` 前就执行 `ffmpeg` / `whisper`，且携带非法 `StartLine` / `EndLine` / `ByteOffset` 参数仍会触发参数校验报错。

**`native-multimodal`** 从规则、钩子拦截/透明桥接、长媒体分段技能三个层面彻底解决上述问题，同时不干扰正常剪辑任务。

---

## 核心架构

| 层级 | 文件 | 作用 |
| :--- | :--- | :--- |
| **常驻规则** | [`rules/AGENTS.md`](./rules/AGENTS.md) | 澄清纯文本与 `100 MiB` 媒体上限区别，强制直接调用 `read_file`、禁止传行号/偏移参数，禁止反射性抽帧与转写，并列明合法例外。 |
| **运行时守卫与自动桥接** | [`hooks.json`](./hooks.json)<br>[`hooks/multimodal_guard.py`](./hooks/multimodal_guard.py) | `PreToolUse` 钩子（拦截 `read_file\|view_file` 与 `run_command`）：遇到不支持的容器/编码或中度超限媒体（`<= 250 MB`、`<= 20 分钟`）时，自动无损 remux 或快速转码到缓存目录（`agy-native-multimodal-cache`）并透明重定向；自动修复非法行号/偏移参数；针对 Gemini/Orchestrator 模型拦截未读媒体的反射性 CLI 分析，对用户显式剪辑请求、`-f lavfi` 合成、已读文件及非多模态模型自动放行。 |
| **大文件准备技能与 CLI** | [`skills/native-multimodal/SKILL.md`](./skills/native-multimodal/SKILL.md)<br>[`prepare_native_media.py`](./skills/native-multimodal/scripts/prepare_native_media.py) | 独立 Python 3 CLI（`inspect`、`normalize`、`slice`），支持无损转封装、保动态保音轨控体积转码，以及带全局 `HH:MM:SS` 烧录时间码的重叠分片，供并行 `read_file` 使用。 |

---

## 实测能力矩阵

| 模态 | `read_file` 直接支持 | 自动桥接 / 规范化 | 说明 |
| :--- | :--- | :--- | :--- |
| **视频** | `.mp4`, `.m4v`, `.webm`, `.mov`, `.mkv`, `.avi` *（H.264/VP8/VP9/MPEG4 + AAC/MP3/Opus）* | `.flv`, `.wmv`, `.ts`/`.mts`、HEVC/AC3/DTS/10-bit、损坏容器 | 规范目标为 `.mp4`（H.264 `yuv420p` + AAC），保留画面动态与音轨。 |
| **音频** | `.mp3`, `.wav`, `.m4a`, `.ogg`, `.opus` | `.flac`, `.aac` (ADTS), `.aiff`, `.wma` | Windows 本地 MIME 会拦截后几类格式，桥接为 `.mp3`/`.m4a`/`.wav`。 |
| **图片** | `.png`, `.jpg`, `.jpeg`, `.webp`, `.gif`, `.bmp`, `.tif`, `.tiff` | `.ico` *（原生读取会触发 API 400）*、`.avif`, `.heic`, `.heif`, `.tga` | 桥接为 `.png`（超限自动回退 `.jpg`）。 |
| **文档** | `.pdf` | — | 直接通过 `read_file` 读取，上限 `100 MiB`。 |

- **硬上限**：`100 MiB`（`104,857,600` 字节）。
- **推荐目标**：单次读取控制在 `<= 45 MB`、`<= 180–300 秒`，兼顾上传速度与时间分辨率。

---

## 快速安装

### 前置依赖
- **Python 3.10+**（`python3` 或 `python`）
- **`ffmpeg` 与 `ffprobe`**（加入 `PATH`；仅在自动桥接非常规格式或超大媒体切片时需要）

### 方式一：通过 Antigravity CLI 安装

```bash
agy plugin install https://github.com/creep-crow/antigravity-native-multimodal
```

### 方式二：克隆仓库并运行安装脚本

**macOS / Linux：**
```bash
git clone https://github.com/creep-crow/antigravity-native-multimodal.git
cd antigravity-native-multimodal
bash scripts/install.sh
```

**Windows (PowerShell)：**
```powershell
git clone https://github.com/creep-crow/antigravity-native-multimodal.git
cd antigravity-native-multimodal
powershell -ExecutionPolicy Bypass -File scripts\install.ps1
```

安装至 `~/.gemini/config/plugins/native-multimodal/` 后，可在 Antigravity 的 **Customizations / Plugins** 页面实时开关，或使用命令行：
```bash
agy plugin enable native-multimodal
agy plugin disable native-multimodal
```

---

## `prepare_native_media.py` 用法

针对超长录音/录像（`> 5–20 分钟`）或超大体积文件（`> 250 MB`）：

```bash
# 1. 检查容器、流编码、体积与 read_file 兼容性
python3 -X utf8 skills/native-multimodal/scripts/prepare_native_media.py inspect video.mkv

# 2. 无损 remux 或转码为 <= 45 MB 的原生格式（保留画面动态与音轨）
python3 -X utf8 skills/native-multimodal/scripts/prepare_native_media.py normalize audio.flac --max-mb 45

# 3. 切分为 180 秒重叠片段并烧录全局 HH:MM:SS 时间码，便于并行 read_file
python3 -X utf8 skills/native-multimodal/scripts/prepare_native_media.py slice lecture.mp4 --chunk-sec 180 --overlap-sec 3 --burn-timecode --max-mb 45
```

---

## 可选环境变量

| 环境变量 | 说明 |
| :--- | :--- |
| `AGY_FFMPEG_DIR` | 指定包含 `ffmpeg` 和 `ffprobe` 可执行文件的目录（未配置 `PATH` 时使用）。 |
| `FFMPEG_PATH` / `FFPROBE_PATH` | 显式指定 `ffmpeg` 或 `ffprobe` 二进制文件路径。 |
| `AGY_MM_CACHE_DIR` | 自定义透明桥接缓存目录（默认为系统临时目录下的 `agy-native-multimodal-cache`）。 |

---

## 常见问题

- **我明确要求用 `ffmpeg` 剪辑、转格式或压制视频时会被拦截吗？**  
  不会。钩子会检查当前 `lastUserInput` 和会话历史中最新的用户输入，只要包含剪辑/裁切/转换/压制/提取等意图或显式点名工具，或模型此前已经通过 `read_file` 读取过该媒体，都会自动放行。
- **切换到 Claude / GPT 等非原生音视频模型时会误拦截吗？**  
  不会。当 `modelName` 以 `claude` 或 `gpt` 开头时，命令行守卫自动跳过。
- **对日常读写代码性能有影响吗？**  
  钩子采用惰性导入，常规文本读取与普通终端命令在 Python 内耗时 `< 1 ms`，零额外阻塞。

---

## 运行测试

```bash
python3 -X utf8 -m unittest discover -s hooks -p "test_*.py" -v
python3 -X utf8 -m unittest discover -s tests -p "test_*.py" -v
```

## 开源协议

[MIT](./LICENSE) © 2026 Creep crow
