# 短剧总集篇自动分集工具 · 分层开发提示词

> **项目名称** drama-splitter
> **开发方式** 全程借助 AI 编程助手，按阶段逐步推进
> **依据文档** 《短剧总集篇自动分集工具 · 技术方案》《短剧总集篇自动分集工具 · 产品需求文档（PRD）》

---

## 文档说明

本文档将短剧总集篇自动分集工具的完整开发过程拆解为五个阶段、共二十个独立执行步骤。每个步骤配有一条自包含的专属提示词，可直接输入给 AI 编程助手执行，无需额外补充背景信息。

### 使用方式

1. 严格按阶段顺序推进。阶段内标注"可并行"的步骤可同时启动多个 AI 会话分头实现，其余步骤按编号顺序执行。
2. 每条提示词已内置该步骤所需的全部上下文（技术规格、数据格式、验收标准），但须在会话开始时提供项目根目录路径。
3. 每个阶段末尾标注了标准化交付物清单，阶段结束前须逐项核验通过后方可进入下一阶段。
4. 阶段切换时运行一次端到端冒烟测试，确保前序产出仍然可用。
5. 每步完成后运行该步的单元测试；阶段完成后运行阶段集成测试；全部完成后运行端到端测试。

### 项目技术概要

| 维度 | 内容 |
|------|------|
| 语言 | Python 3.10+ |
| 核心依赖 | FFmpeg/ffprobe、RapidOCR (onnxruntime)、Chromaprint (fpcalc)、PySceneDetect、OpenCV (headless)、Streamlit、PyInstaller、pydantic v2 |
| 架构分层 | 信号采集层（多信号并行）→ 融合决策层 → 输出层（裁剪 + UI） |
| 数据流 | 视频文件 → 各检测器候选点 JSON → 融合时间清单 JSON → 单集文件 |

### 全局编码规范

所有步骤遵循以下统一约定，后续提示词不再重复声明。

**目录结构**

```
drama-splitter/
├── pyproject.toml
├── config.yaml
├── drama-splitter.spec      # PyInstaller 打包配置
├── src/
│   └── drama_splitter/
│       ├── __init__.py
│       ├── exceptions.py     # 统一异常体系
│       ├── logging_config.py # 日志配置
│       ├── models/          # 数据模型
│       ├── detectors/       # 信号检测器
│       ├── fusion/          # 融合决策引擎
│       ├── output/          # 裁剪与导出
│       ├── ui/              # Web UI
│       └── cli.py           # CLI 入口
├── tests/
│   ├── conftest.py
│   ├── fixtures/            # 测试数据和生成脚本
│   ├── test_models.py
│   ├── test_detectors.py
│   ├── test_fusion.py
│   ├── test_output.py
│   └── test_e2e.py
└── scripts/
    └── build.sh             # 打包脚本
```

**统一异常体系**

所有自定义异常继承自 `DramaSplitterError`，定义在 `src/drama_splitter/exceptions.py`：

```python
class DramaSplitterError(Exception):
    """所有自定义异常的基类"""

class EnvironmentError(DramaSplitterError):
    """环境依赖缺失"""
class FFmpegNotFoundError(EnvironmentError): ...
class ChromaprintNotFoundError(EnvironmentError): ...
class FFprobeError(EnvironmentError): ...

class DetectionError(DramaSplitterError):
    """检测过程异常"""
class AllDetectorsFailedError(DetectionError): ...
class FrameExtractionError(DetectionError): ...
class InsufficientCandidatesError(DetectionError): ...

class FusionError(DramaSplitterError):
    """融合决策异常"""
class NoBoundariesError(FusionError): ...

class OutputError(DramaSplitterError):
    """输出裁剪异常"""
class FFmpegCutError(OutputError): ...
class InsufficientDiskSpaceError(OutputError): ...

class ConfigError(DramaSplitterError):
    """配置异常"""
class ConfigValidationError(ConfigError): ...
```

**日志规范**

- 使用标准库 `logging`，每个模块顶部 `logger = logging.getLogger(__name__)`
- 不使用 `print` 做日志输出
- CLI 层统一配置 `logging.basicConfig`，同时输出到终端（rich 格式）和文件（JSON 格式）
- 日志级别：默认 INFO，`-v` 切换为 DEBUG

**统一候选点 JSON 格式**

所有信号检测器输出统一为此格式，供融合引擎消费：

```json
{
  "signal_type": "ocr | audio_fingerprint | black_silence | scene_cluster",
  "video_name": "input.mp4",
  "duration_sec": 3600.5,
  "generated_at": "2026-08-07T12:00:00",
  "candidates": [
    {
      "timestamp_sec": 195.5,
      "episode": 3,
      "confidence": 0.92,
      "metadata": {}
    }
  ]
}
```

- `episode` 字段：仅 OCR 信号填充整数值，其余信号为 `null`
- `metadata` 字段：各信号自定义附加信息
- `confidence` 字段：0.0–1.0 浮点数，表示该候选点自身检测器的内部置信度

**统一时间清单 JSON 格式**

融合决策引擎的最终输出：

```json
{
  "video_name": "input.mp4",
  "duration_sec": 3600.5,
  "generated_at": "2026-08-07T12:05:00",
  "episodes": [
    {
      "episode": 1,
      "start_sec": 0.0,
      "end_sec": 185.5,
      "duration_sec": 185.5,
      "confidence": "high",
      "signals_hit": ["ocr", "audio_fingerprint", "black_silence"]
    }
  ]
}
```

- `confidence` 取值：`high` / `medium` / `low`
- `signals_hit`：该边界命中的信号类型列表

**断点续传机制**

每路检测器完成后将结果写入 `work_dir/candidates/<signal_type>.json`。融合结果写入 `work_dir/time_list.json`。工具启动时检查这些文件是否存在：已存在且未损坏的跳过对应步骤。通过 CLI 参数 `--fresh` 可强制忽略缓存重新执行。

**帧时间戳计算约定（全局修正）**

抽帧使用 FFmpeg `fps` 滤镜按固定频率采样，帧文件按序号命名（`frame_00000001.png`）。时间戳通过 `帧序号 / fps` 计算，**不使用** `-frame_pts` 模式——后者写入的 PTS 是视频时间基单位的原始值，直接除以 1000 会得到错误结果。按序号乘间隔的方式对 0.5 秒间隔的标题卡识别精度足够。

**Python 代码规范**

- 类型标注全覆盖（函数参数、返回值、变量声明）
- 函数 docstring 采用 Google 风格
- 所有公开函数和类须有对应的单元测试，测试覆盖率不低于 80%
- 外部命令调用统一通过 `subprocess.run` 并捕获 stderr

---

## 阶段 0：项目初始化与基础架构

本阶段不产出业务功能，但为后续所有阶段铺设地基。三个步骤之间存在依赖关系，须按顺序执行。

---

### 步骤 0.1：项目脚手架、依赖管理与异常体系

**提示词**

> 你是一位资深 Python 工程师。请创建一个名为 `drama-splitter` 的 Python 项目，完成以下工作：
>
> 1. 在指定路径下创建完整目录结构：
>    - `src/drama_splitter/` 为源码包，内含 `models/`、`detectors/`、`fusion/`、`output/`、`ui/` 子包，每个子包内有空的 `__init__.py`
>    - `tests/` 为测试目录，内含 `conftest.py` 和 `fixtures/` 子目录
>    - `scripts/` 目录
>    - 根目录有 `config.yaml`、`.gitignore`、`pyproject.toml`
>
> 2. `pyproject.toml` 使用 PEP 621 格式，项目名 `drama-splitter`，Python 要求 `>=3.10`。依赖分两组：
>    - 核心依赖：`ffmpeg-python`、`opencv-python-headless`、`numpy`、`pydantic>=2`、`pyyaml`、`click`、`rich`、`rapidocr-onnxruntime`、`pyacoustid`、`scenedetect[opencv]`、`streamlit`
>    - 开发依赖：`pytest`、`pytest-cov`、`ruff`
>
> 3. 创建 `src/drama_splitter/exceptions.py`，定义全局统一异常体系。所有异常继承自 `DramaSplitterError(Exception)`，按以下层级组织：
>    - `EnvironmentError` → `FFmpegNotFoundError`、`ChromaprintNotFoundError`、`FFprobeError`
>    - `DetectionError` → `AllDetectorsFailedError`、`FrameExtractionError`、`InsufficientCandidatesError`
>    - `FusionError` → `NoBoundariesError`
>    - `OutputError` → `FFmpegCutError`、`InsufficientDiskSpaceError`
>    - `ConfigError` → `ConfigValidationError`
>    - 每个异常类有简短 docstring
>
> 4. 创建 `src/drama_splitter/logging_config.py`，提供 `setup_logging(log_dir: str, verbose: bool = False)` 函数：
>    - 配置 root logger，同时输出到终端（用 `rich.logging.RichHandler`）和文件（`log_dir/run.log`，JSON 格式用 `logging.FileHandler`）
>    - 默认级别 INFO，`verbose=True` 时切换为 DEBUG
>    - 避免重复添加 handler（检查已有 handler 列表）
>
> 5. `config.yaml` 写入以下默认配置：
>    - `ocr_engine: rapidocr`（可选 `paddleocr`）
>    - `frame_interval_sec: 0.5`（抽帧间隔）
>    - `frame_width: 640`（抽帧缩放宽度）
>    - `cut_mode: lossless`（可选 `reencode`）
>    - `naming_template: "第{ep:02d}集.mp4"`
>    - `output_dir: ./output`
>    - `cleanup_frames: true`（OCR 完成后是否删除帧文件）
>    - `fingerprint_template_sec: 20`（音频指纹模板长度）
>    - `fingerprint_threshold: 0.85`（指纹相似度阈值）
>    - `black_duration: 0.3`（黑帧最短持续时间秒）
>    - `black_pix_threshold: 0.10`（黑帧像素阈值）
>    - `silence_noise: -40dB`（静音噪声阈值）
>    - `silence_duration: 0.3`（静音最短持续时间秒）
>    - `cluster_window_sec: 3.0`（候选点聚类窗口）
>    - `min_episode_sec: 60`（最短单集时长）
>    - `max_episode_sec: 300`（最长单集时长）
>
> 6. `.gitignore` 排除 `__pycache__/`、`.venv/`、`output/`、`*.pyc`、`dist/`、`build/`、`*.egg-info/`
>
> 7. `src/drama_splitter/__init__.py` 中定义 `__version__ = "0.1.0"`
>
> 8. 在 `src/drama_splitter/cli.py` 中创建一个 Click 入口函数 `main()`，暂不实现子命令，只调用 `setup_logging` 并打印版本号和欢迎信息。
>
> 9. 在 `tests/conftest.py` 中创建共享 fixture：
>    - `test_video_path`：用 FFmpeg 生成 10 秒测试视频（`ffmpeg -f lavfi -i testsrc=duration=10:size=320x240:rate=30 -c:v libx264 -y test.mp4`），返回路径。使用 `tmp_path_factory` 确保测试间隔离。
>    - `test_video_meta`：返回一个模拟的 `VideoMeta` 对象（此 fixture 在步骤 0.2 后可用，先用 `pytest.skip` 占位）
>
> 完成后运行 `pip install -e ".[dev]"` 确认可安装，运行 `python -m drama_splitter` 确认入口可用。

---

### 步骤 0.2：数据模型与配置体系

**提示词**

> 你是一位资深 Python 工程师。在已有的 `drama-splitter` 项目中，使用 pydantic v2 完成数据模型定义和配置加载体系。具体要求：
>
> **1. 在 `src/drama_splitter/models/__init__.py` 中定义以下模型**
>
> - `VideoMeta`：视频元信息
>   - `file_path: str`、`file_name: str`、`format: str`、`duration_sec: float`、`width: int`、`height: int`、`codec: str`、`fps: float`、`file_size_mb: float`
>
> - `Candidate`：单个候选边界点
>   - `signal_type: Literal["ocr", "audio_fingerprint", "black_silence", "scene_cluster"]`
>   - `timestamp_sec: float`（非负）
>   - `episode: int | None`（默认 None）
>   - `confidence: float`（0.0–1.0，用 `Field(ge=0, le=1)` 约束）
>   - `metadata: dict`（默认空 dict）
>
> - `CandidateList`：一个检测器的完整输出
>   - `signal_type: str`、`video_name: str`、`duration_sec: float`、`generated_at: datetime`
>   - `candidates: list[Candidate]`
>   - 提供 `to_json(path: str)` 和 `from_json(path: str)` 类方法，实现 JSON 序列化/反序列化
>
> - `Episode`：时间清单中的单集记录
>   - `episode: int`、`start_sec: float`、`end_sec: float`、`duration_sec: float`、`confidence: Literal["high", "medium", "low"]`、`signals_hit: list[str]`
>   - 添加 `@field_validator` 确保 `end_sec > start_sec` 且 `duration_sec == end_sec - start_sec`
>
> - `TimeList`：完整时间清单
>   - `video_name: str`、`duration_sec: float`、`generated_at: datetime`、`episodes: list[Episode]`
>   - 提供 `to_json(path)` / `from_json(path)` / `to_csv(path)` 方法
>   - 添加验证器确保集数连续且无时间重叠
>
> **2. 在 `src/drama_splitter/models/config.py` 中定义 `AppConfig`**
>
> - 字段与 `config.yaml` 一一对应，所有字段有默认值
> - 提供 `AppConfig.from_yaml(path: str) -> AppConfig` 类方法
> - 提供 `merge_cli_args(self, **kwargs) -> AppConfig` 方法，接收 CLI 参数覆盖配置（值为 None 的参数不覆盖）
> - 提供 `validate(self) -> None` 方法，执行以下校验，不通过时抛出 `ConfigValidationError`：
>   - `frame_interval_sec > 0`
>   - `min_episode_sec < max_episode_sec`
>   - `fingerprint_threshold ∈ [0, 1]`
>   - `black_pix_threshold ∈ [0, 1]`
>   - `naming_template` 包含 `{ep}` 占位符
>   - `ocr_engine ∈ {"rapidocr", "paddleocr"}`
>   - `cut_mode ∈ {"lossless", "reencode"}`
> - `from_yaml` 方法内部调用 `validate()`
>
> **3. 在 `tests/test_models.py` 中为每个模型编写测试**
>
> - 测试 `Candidate` 的 confidence 越界抛出 `ValidationError`
> - 测试 `Episode` 的 `end_sec <= start_sec` 抛出 `ValidationError`
> - 测试 `CandidateList.to_json` / `from_json` 往返一致
> - 测试 `TimeList.to_json` / `from_json` / `to_csv` 往返一致
> - 测试 `AppConfig.from_yaml` 能正确加载 `config.yaml`
> - 测试 `AppConfig.validate` 对各项非法配置抛出 `ConfigValidationError`
> - 测试 `merge_cli_args` 中 None 值不覆盖原有配置
>
> 完成后运行 `pytest tests/test_models.py -v` 确保全部通过。

---

### 步骤 0.3：FFmpeg/ffprobe 命令封装层

**提示词**

> 你是一位资深 Python 工程师。在 `drama-splitter` 项目中创建 `src/drama_splitter/ffmpeg_wrapper.py`，封装所有 FFmpeg/ffprobe 命令调用。具体要求：
>
> **1. `probe_video(file_path: str) -> VideoMeta`**
>
> 调用 `ffprobe -v quiet -print_format json -show_format -show_streams <file>`，解析 JSON 输出，填充 `VideoMeta` 模型。需要正确提取：时长（从 format.duration）、分辨率（从 video stream 的 width/height）、编码（codec_name）、帧率（r_frame_rate，需解析分数如 "30000/1001" 转为浮点）。文件大小从 format.size 换算为 MB。
>
> 如果 ffprobe 不可用，抛出 `FFmpegNotFoundError`；如果返回错误，抛出 `FFprobeError`。
>
> **2. `extract_frames(file_path: str, output_dir: str, interval_sec: float, width: int = 640) -> list[tuple[str, float]]`**
>
> 调用 FFmpeg 按固定间隔抽帧。使用 `fps` 滤镜按固定频率采样，帧文件按序号命名：
> ```
> ffmpeg -i <input> -vf "fps=<1/interval>,scale=<width>:-1" <output_dir>/frame_%08d.png
> ```
> 返回 `[(frame_path, timestamp_sec), ...]` 列表。时间戳计算方式：从文件名提取帧序号（从 1 开始），`timestamp_sec = (frame_index - 1) * interval_sec`。
>
> **不要使用 `-frame_pts 1`**——该选项将 PTS 原始值（时间基单位）写入文件名，直接除以 1000 会得到错误的时间戳。按序号乘间隔的方式对 0.5 秒间隔的标题卡识别精度足够。
>
> 如果抽帧结果为空（FFmpeg 未输出任何文件），抛出 `FrameExtractionError`。
>
> **3. `extract_audio(file_path: str, output_path: str, sample_rate: int = 16000) -> str`**
>
> 提取 16kHz 单声道 WAV（默认取第一条音轨 `-map 0:a:0`）：
> ```
> ffmpeg -i <input> -vn -ac 1 -ar <sample_rate> -map 0:a:0 -f wav <output_path>
> ```
> 返回输出文件路径。如果视频无音轨，抛出 `FFprobeError` 并在消息中说明"未检测到音轨"。
>
> **4. `detect_black_silence(file_path: str, black_duration: float, black_pix_threshold: float, silence_noise: str, silence_duration: float) -> dict`**
>
> 同时运行 blackdetect 和 silencedetect：
> ```
> ffmpeg -i <input> -vf "blackdetect=d=<bd>:pix_th=<bpt>" -af "silencedetect=n=<sn>:d=<sd>" -f null - 2> <log>
> ```
> 解析 stderr 中的 `black_start`、`black_end`、`silence_start`、`silence_end` 标记，返回：
> ```python
> {"black_segments": [(start, end), ...], "silence_segments": [(start, end), ...]}
> ```
>
> **5. `cut_segment(file_path: str, start_sec: float, end_sec: float, output_path: str, mode: str = "lossless") -> str`**
>
> - `lossless` 模式：`ffmpeg -ss <start> -i <input> -to <end> -c copy -avoid_negative_ts make_zero <output>`
> - `reencode` 模式：`ffmpeg -i <input> -ss <start> -to <end> -c:v libx264 -preset fast -crf 20 -c:a aac <output>`
>
> 返回输出文件路径。失败时抛出 `FFmpegCutError`。
>
> **6. 所有函数统一使用 `subprocess.run` 调用，设置 `capture_output=True`、`text=True`**
>
> 检查 returncode，非零时将 stderr 内容包含在异常消息中。使用 `shutil.which("ffmpeg")` 在模块加载时验证 ffmpeg 可用性。
>
> **7. 在 `tests/test_ffmpeg_wrapper.py` 中编写测试**
>
> - 使用 conftest.py 中的 `test_video_path` fixture
> - 测试 `probe_video` 返回的 `VideoMeta` 字段类型和值合理
> - 测试 `extract_frames` 返回的帧数量约为 `duration / interval`（允许 ±2 的误差）
> - 测试 `extract_frames` 返回的时间戳为 `(index - 1) * interval_sec`，误差 ≤ interval_sec
> - 测试 `extract_audio` 输出文件存在且大小 > 0
> - 测试 `detect_black_silence` 返回的 dict 结构正确
> - 测试 `cut_segment` 两种模式都能生成输出文件
> - 测试 ffmpeg 不可用时抛出 `FFmpegNotFoundError`（mock `shutil.which` 返回 None）
>
> 完成后运行 `pytest tests/test_ffmpeg_wrapper.py -v` 确保全部通过。

---

**本阶段交付物**

| 序号 | 交付物 | 验证方式 |
|------|--------|----------|
| 1 | 可安装的 Python 项目，`pip install -e ".[dev]"` 成功 | 命令行执行无报错 |
| 2 | `python -m drama_splitter` 打印版本信息 | 命令行执行有输出 |
| 3 | `exceptions.py` 统一异常体系，`logging_config.py` 日志配置 | 导入无报错 |
| 4 | `models/` 下 5 个 pydantic 模型 + `AppConfig`，全部带类型标注和验证器 | `pytest tests/test_models.py` 全通过 |
| 5 | `ffmpeg_wrapper.py` 封装 5 个函数，帧时间戳用序号×间隔计算 | `pytest tests/test_ffmpeg_wrapper.py` 全通过 |
| 6 | `config.yaml` 含全部默认配置项，`AppConfig.from_yaml` 可正确加载并通过校验 | 单元测试覆盖 |

---

## 阶段 1：最短路径——OCR 标题识别与无损裁剪

本阶段实现从视频文件到单集输出的最短链路：抽帧 → OCR → 正则匹配集数 → 生成时间清单 → 无损裁剪。完成后即可处理大多数有明确集数标题的短剧总集篇。

步骤 1.1–1.3 须顺序执行；步骤 1.4 依赖前三步；步骤 1.5 依赖步骤 1.4。

---

### 步骤 1.1：视频抽帧模块（frame_sampler）

**提示词**

> 你是一位资深 Python 工程师。在 `drama-splitter` 项目中创建 `src/drama_splitter/detectors/frame_sampler.py`，实现视频抽帧模块。
>
> **任务目标**
>
> 将长视频按固定时间间隔抽取缩略帧序列，供后续 OCR 和场景检测共用，避免重复解码。
>
> **前置依赖**
>
> - `src/drama_splitter/ffmpeg_wrapper.py` 中的 `extract_frames` 函数和 `probe_video` 函数
> - `src/drama_splitter/models/` 中的 `VideoMeta` 模型
>
> **实现要求**
>
> 1. 定义 `FrameSampler` 类：
>    ```python
>    class FrameSampler:
>        def __init__(self, interval_sec: float = 0.5, frame_width: int = 640):
>            ...
>        def sample(self, video_path: str, output_dir: str) -> list[Frame]:
>            """抽取帧并返回带时间戳的帧列表"""
>    ```
>
> 2. 定义 `Frame` 数据类（dataclass）：
>    - `path: str` — 帧图片文件路径
>    - `timestamp_sec: float` — 该帧在视频中的时间位置（秒）
>
> 3. `sample()` 方法内部流程：
>    - 调用 `probe_video` 获取视频时长和帧率
>    - 调用 `extract_frames` 抽取帧到 `output_dir/frames/` 子目录
>    - `extract_frames` 已返回 `[(frame_path, timestamp_sec), ...]`，时间戳按 `(帧序号 - 1) * interval_sec` 计算
>    - 直接使用返回值构造 `Frame` 列表
>    - 按时间戳升序排列
>    - 日志记录：抽取了多少帧、总耗时、平均每帧耗时
>
> 4. 容错处理：
>    - 如果 `output_dir/frames/` 已存在，先清空再抽取，避免残留旧帧干扰
>    - 如果抽帧结果为空（FFmpeg 未输出任何文件），抛出 `FrameExtractionError` 并附带视频路径和参数信息
>
> 5. 性能考量：
>    - 对于 1 小时视频、0.5 秒间隔，预计产出约 7200 帧，每帧缩放到 640 宽。帧文件写入磁盘而非全量加载内存。
>
> **测试要求**
>
> 在 `tests/test_frame_sampler.py` 中：
> - 使用 conftest.py 中的测试视频 fixture
> - 测试 `sample()` 返回的帧列表非空且按时间戳升序
> - 测试帧数量约等于 `duration / interval`（允许 ±2 的误差）
> - 测试每帧的 `path` 指向的文件确实存在
> - 测试 `timestamp_sec` 为非负值且不超过视频时长
> - 测试 `timestamp_sec` ≈ `(index - 1) * interval_sec`（误差 ≤ interval_sec）

---

### 步骤 1.2：OCR 识别引擎封装（ocr_detector）

**提示词**

> 你是一位资深 Python 工程师。在 `drama-splitter` 项目中创建 `src/drama_splitter/detectors/ocr_detector.py`，实现 OCR 识别引擎封装。
>
> **任务目标**
>
> 对抽帧模块产出的帧图片做文字识别，返回每帧中识别到的文本块及其位置信息，供后续集数标题匹配使用。
>
> **前置依赖**
>
> - `src/drama_splitter/detectors/frame_sampler.py` 中的 `Frame` 数据类
> - `rapidocr_onnxruntime` 包
>
> **实现要求**
>
> 1. 定义 `OCRResult` 数据类：
>    - `frame_path: str`
>    - `timestamp_sec: float`
>    - `text_blocks: list[TextBlock]`
>
> 2. 定义 `TextBlock` 数据类：
>    - `text: str` — 识别到的文本
>    - `score: float` — 置信度分数（0–1）
>    - `bbox: tuple[int, int, int, int]` — 文本框坐标 (x1, y1, x2, y2)
>
> 3. 定义 `OCRDetector` 类：
>    ```python
>    class OCRDetector:
>        def __init__(self, engine: str = "rapidocr", region: str | None = None):
>            """engine: 'rapidocr' 或 'paddleocr'；region: 'top'/'center'/'bottom' 或 None（全图）"""
>
>        def detect(self, frames: list[Frame], batch_size: int = 500) -> list[OCRResult]:
>            """对帧列表做 OCR，返回识别结果。分批处理避免内存峰值。"""
>    ```
>
> 4. 引擎初始化：
>    - `rapidocr`：`from rapidocr_onnxruntime import RapidOCR; self._ocr = RapidOCR()`
>    - `paddleocr`：用 try/import 包裹，未安装时抛出 `ImportError` 并提示安装命令
>
> 5. 区域过滤逻辑：
>    - 如果 `region="top"`，只保留 bbox 中心点 y 坐标在画面上 1/3 区域的文本块
>    - 如果 `region="center"`，只保留中心 1/3 区域
>    - 如果 `region="bottom"`，只保留下 1/3 区域
>    - 如果 `region=None`，保留全部
>
> 6. `detect()` 方法流程：
>    - 将帧列表按 `batch_size` 分批，每批处理后释放内存
>    - 遍历每帧调用 OCR 引擎
>    - 按区域过滤文本块
>    - 跳过 `score < 0.5` 的低置信度文本块
>    - 构造 `OCRResult` 加入结果列表
>    - 每处理 100 帧输出一次进度日志
>    - OCR 引擎实例在 `__init__` 中创建一次，`detect()` 中复用
>
> **测试要求**
>
> 在 `tests/test_ocr_detector.py` 中：
> - 生成一张包含中文文字"第1集"的测试图片（用 PIL 绘制白底黑字图片），作为测试帧
> - 测试 `detect()` 对该图片能识别出包含"第1集"的文本块
> - 测试 `region="top"` 过滤逻辑正确
> - 测试低置信度文本块被过滤
> - 测试空帧列表返回空结果
> - 测试 `batch_size` 参数正确分批

---

### 步骤 1.3：集数标题正则匹配与多帧投票

**提示词**

> 你是一位资深 Python 工程师。在 `drama-splitter` 项目中创建 `src/drama_splitter/detectors/episode_matcher.py`，实现集数标题的正则匹配和多帧投票逻辑。
>
> **任务目标**
>
> 从 OCR 识别结果中提取集数信息，通过多帧投票消除单帧误识别，输出标准候选点 JSON。
>
> **前置依赖**
>
> - `src/drama_splitter/detectors/ocr_detector.py` 中的 `OCRResult`、`TextBlock`
> - `src/drama_splitter/models/` 中的 `Candidate`、`CandidateList`
>
> **实现要求**
>
> 1. 定义正则模式，覆盖中英文常见集数写法：
>    ```python
>    EPISODE_PATTERNS = [
>        # 中文数字：第三集、第十二话、第二十回、第一百二十三集
>        re.compile(r"第\s*([零一二三四五六七八九十百]+)\s*[集话話回]"),
>        # 阿拉伯数字中文格式：第1集、第03话、第12回
>        re.compile(r"第\s*(\d+)\s*[集话話回]"),
>        # 英文格式：EP.3 / EP3 / Episode 12 / EPISODE 1
>        re.compile(r"EP\.?\s*(\d+)", re.I),
>        re.compile(r"Episode\s*(\d+)", re.I),
>    ]
>    ```
>
> 2. 实现中文数字转整数函数 `cn2num(text: str) -> int`：
>    - **支持 0–999 的转换**，覆盖"百"位
>    - 处理规则：
>      - "零"→0，"一"→1 ... "九"→9
>      - "十"→10，"十一"→11，"十九"→19
>      - "二十"→20，"二十三"→23
>      - "一百"→100，"一百零一"→101，"一百二十三"→123
>      - "二百"→200，"九百九十九"→999
>    - 算法：先检查是否含"百"，如果有，分离百位数字；再检查是否含"十"，分离十位数字；剩余为个位
>    - 输入已是阿拉伯数字时直接 `int()` 返回
>
> 3. 定义 `EpisodeMatcher` 类：
>    ```python
>    class EpisodeMatcher:
>        def match(self, ocr_results: list[OCRResult], duration_sec: float, video_name: str) -> CandidateList:
>            """从 OCR 结果中提取集数候选点"""
>    ```
>
> 4. `match()` 方法流程：
>
>    **第一阶段：逐帧匹配**
>    - 遍历每个 `OCRResult`，对其每个 `TextBlock` 依次尝试所有正则模式
>    - 匹配成功时，提取集数（阿拉伯数字直接转 int，中文数字调 `cn2num`）
>    - 记录 `(timestamp_sec, episode, score)` 三元组到原始命中列表
>
>    **第二阶段：多帧投票**
>    - 将原始命中按集数分组
>    - 对每个集数，检查其命中的帧是否在时间上连续（相邻命中间隔 ≤ 2 秒视为连续）
>    - 如果同一集数在连续帧中被识别到 ≥ 2 次，置信度设为 0.9
>    - 如果只命中 1 次但 score ≥ 0.8，置信度设为 0.7
>    - 如果只命中 1 次且 score < 0.8，丢弃该命中（视为误识别）
>
>    **第三阶段：去重**
>    - 同一集数只保留首次出现的时间戳作为该集起点
>    - 构造 `Candidate` 列表，`signal_type="ocr"`，`episode` 填入集数，`metadata` 包含 `{"text": 原始文本, "score": OCR置信度, "vote_count": 投票帧数}`
>
>    **第四阶段：构造 CandidateList**
>    - 按时间戳升序排列候选点
>    - 填充 `video_name`、`duration_sec`、`generated_at`
>
> 5. 边界情况处理：
>    - 集数超过 999 或为 0 时视为异常，记录警告日志并跳过
>    - 集数序列有跳号时不在此步处理，留给融合引擎补全
>    - OCR 结果为空时返回空的 `CandidateList`
>
> **测试要求**
>
> 在 `tests/test_episode_matcher.py` 中：
> - 测试 `cn2num` 覆盖 0–999 的边界值：
>    - `cn2num("零")` → 0，`cn2num("九")` → 9
>    - `cn2num("十")` → 10，`cn2num("十一")` → 11，`cn2num("十九")` → 19
>    - `cn2num("二十")` → 20，`cn2num("二十三")` → 23
>    - `cn2num("一百")` → 100，`cn2num("一百零一")` → 101
>    - `cn2num("一百二十三")` → 123，`cn2num("九百九十九")` → 999
>    - `cn2num("99")` → 99（阿拉伯数字直接转）
> - 构造模拟 `OCRResult` 列表，包含"第1集"在连续 3 帧中出现，测试投票后置信度为 0.9
> - 测试单帧命中且 score < 0.8 时被丢弃
> - 测试同一集数只保留首次时间戳
> - 测试英文格式 "EP.3" 和 "Episode 12" 能正确匹配
> - 测试空输入返回空 `CandidateList`

---

### 步骤 1.4：基础时间清单生成（单信号）

**提示词**

> 你是一位资深 Python 工程师。在 `drama-splitter` 项目中创建 `src/drama_splitter/fusion/basic_builder.py`，实现基于单一 OCR 信号的基础时间清单生成器。
>
> **任务目标**
>
> 将 OCR 检测器产出的候选点（含集数信息）转化为可直接用于裁剪的时间清单。这是阶段 1 的简化版融合逻辑——只有 OCR 一个信号，不需要聚类和多信号对齐，但要处理集数缺失和自动编号。
>
> **前置依赖**
>
> - `src/drama_splitter/models/` 中的 `CandidateList`、`TimeList`、`Episode`
>
> **实现要求**
>
> 1. 定义 `BasicTimeListBuilder` 类：
>    ```python
>    class BasicTimeListBuilder:
>        def build(self, candidate_list: CandidateList) -> TimeList:
>            """从 OCR 候选点构建时间清单"""
>    ```
>
> 2. `build()` 方法流程：
>
>    **情况 A：OCR 有有效候选点（≥ 2 个集数）**
>    - 按集数升序排列候选点
>    - 每集的 `start_sec` = 该集候选点的 `timestamp_sec`
>    - 每集的 `end_sec` = 下一集的 `start_sec`（最后一集的 `end_sec` = 视频总时长）
>    - `confidence` 全部设为 `"medium"`（单信号，阶段 3 会重新评级）
>    - `signals_hit` 全部设为 `["ocr"]`
>
>    **情况 B：OCR 候选点不足（0 或 1 个）**
>    - 抛出 `InsufficientCandidatesError`，提示"OCR 未能识别到足够的集数标题"
>
>    **情况 C：集数序列有跳号**
>    - 保留已有集数，在日志中记录警告"检测到集数序列跳号：缺失第 X 集"
>    - 缺失集的边界在阶段 3 融合引擎中尝试用其他信号补全
>
>    **情况 D：集数从 0 开始而非 1**
>    - 最小集数为 0 时修正为 1 并记录警告
>    - 最小集数大于 1 时保留原值但记录警告
>
> 3. 如果候选点中没有集数信息但有候选时间点，按配置的 `min_episode_sec` 间隔切分视频并自动编号，在 `metadata` 中标注 `"auto_numbered": true`。此分支在阶段 1 不应触发（OCR 信号一定带集数）。
>
> **测试要求**
>
> 在 `tests/test_basic_builder.py` 中：
> - 构造 3 个 OCR 候选点（集数 1@0s, 2@180s, 3@360s, 视频时长 540s），测试生成的 `TimeList` 有 3 个 `Episode`，end_sec 正确
> - 测试最后一集的 `end_sec` 等于视频总时长
> - 测试候选点 < 2 个时抛出 `InsufficientCandidatesError`
> - 测试集数跳号时保留已有集数且记录警告
> - 测试集数从 0 开始时被修正为 1

---

### 步骤 1.5：FFmpeg 无损批量裁剪（cutter）

**提示词**

> 你是一位资深 Python 工程师。在 `drama-splitter` 项目中创建 `src/drama_splitter/output/cutter.py`，实现批量裁剪输出模块。
>
> **任务目标**
>
> 读取时间清单 JSON，逐集调用 FFmpeg 进行裁剪，输出独立的单集视频文件。
>
> **前置依赖**
>
> - `src/drama_splitter/models/` 中的 `TimeList`
> - `src/drama_splitter/ffmpeg_wrapper.py` 中的 `cut_segment` 函数
>
> **实现要求**
>
> 1. 定义 `Cutter` 类：
>    ```python
>    class Cutter:
>        def __init__(self, mode: str = "lossless", naming_template: str = "第{ep:02d}集.mp4"):
>            ...
>        def cut(self, video_path: str, time_list: TimeList, output_dir: str) -> list[CutResult]:
>            """批量裁剪并返回结果列表"""
>        def cut_single(self, video_path: str, episode: Episode, output_dir: str) -> CutResult:
>            """裁剪单集"""
>    ```
>
> 2. 定义 `CutResult` 数据类：
>    - `episode: int`、`output_path: str`、`file_size_mb: float`、`duration_sec: float`、`success: bool`、`error: str | None`
>
> 3. 文件命名逻辑：
>    - 使用 `naming_template` 格式化文件名，`{ep}` 替换为集数，`{ep:02d}` 替换为零填充两位集数
>    - 实现模板解析：用 `string.Formatter` 或正则提取 `{ep}` 和 `{ep:0Nd}` 占位符
>
> 4. `cut()` 方法流程：
>    - 检查 `output_dir` 是否存在，不存在则创建
>    - 预估所需磁盘空间：`sum(ep.duration_sec for ep in time_list.episodes) / time_list.duration_sec * 视频文件大小`，与 `shutil.disk_usage(output_dir)` 的可用空间比较，不足时抛出 `InsufficientDiskSpaceError`
>    - 遍历 `time_list.episodes`，对每集调用 `cut_single()`
>    - 每集裁剪完成后输出进度日志
>    - 结束后输出汇总日志：总集数、成功数、失败数、总输出大小
>
> 5. `cut_single()` 方法流程：
>    - 生成输出文件路径
>    - 处理文件名冲突：追加 `_1`、`_2` 后缀
>    - 调用 `ffmpeg_wrapper.cut_segment`
>    - 成功后获取输出文件大小
>    - 失败时捕获异常，填充 `success=False` 和 `error`，不中断后续集
>
> **测试要求**
>
> 在 `tests/test_cutter.py` 中：
> - 使用 conftest.py 中的测试视频
> - 构造包含 2 集的 `TimeList`（第 1 集 0–5s，第 2 集 5–10s）
> - 测试 `cut()` 返回 2 个 `CutResult`，`success` 均为 True
> - 测试输出文件存在且大小 > 0
> - 测试文件名模板解析正确
> - 测试文件名冲突时自动追加后缀
> - 测试单集裁剪失败时不影响其他集

---

**本阶段交付物**

| 序号 | 交付物 | 验证方式 |
|------|--------|----------|
| 1 | `frame_sampler.py` — 按固定间隔抽取帧序列，时间戳用序号×间隔计算 | 单元测试通过 |
| 2 | `ocr_detector.py` — RapidOCR 封装，支持区域过滤和分批处理 | 单元测试通过 |
| 3 | `episode_matcher.py` — 正则匹配 + 多帧投票 + cn2num 支持 0–999 | 单元测试通过 |
| 4 | `basic_builder.py` — 单信号时间清单生成器 | 单元测试通过 |
| 5 | `cutter.py` — 无损批量裁剪 | 单元测试通过 |
| 6 | **阶段集成测试**：用真实或合成短剧视频跑通 抽帧→OCR→匹配→清单→裁剪 全链路 | 产出 N 个单集文件，文件名含集数 |

---

## 阶段 2：信号补强——多检测器并行采集

本阶段在 OCR 之外增加三路检测信号。步骤 2.1、2.2、2.3 之间无依赖，可并行开发。步骤 2.4 依赖前三步。

---

### 步骤 2.1：Chromaprint 音频指纹片头检测（audio_fingerprint）

**提示词**

> 你是一位资深 Python 工程师。在 `drama-splitter` 项目中创建 `src/drama_splitter/detectors/audio_fingerprint.py`，实现基于 Chromaprint 的音频指纹片头检测器。
>
> **任务目标**
>
> 提取整条音轨的音频指纹，通过滑窗自相关找到片头音乐重复出现的时间点，每个重复点对应一集的起始位置。
>
> **前置依赖**
>
> - `src/drama_splitter/ffmpeg_wrapper.py` 中的 `extract_audio` 函数
> - `src/drama_splitter/models/` 中的 `Candidate`、`CandidateList`
> - 系统已安装 `fpcalc`（Chromaprint 命令行工具）
>
> **实现要求**
>
> 1. 定义 `AudioFingerprintDetector` 类：
>    ```python
>    class AudioFingerprintDetector:
>        def __init__(self, template_sec: float = 20.0, threshold: float = 0.85):
>            ...
>        def detect(self, video_path: str, duration_sec: float, video_name: str, work_dir: str) -> CandidateList:
>            """提取指纹、滑窗匹配、输出候选点"""
>    ```
>
> 2. `detect()` 方法流程：
>
>    **步骤 A：提取音轨**
>    - 调用 `extract_audio(video_path, wav_path, sample_rate=16000)` 输出到 `work_dir/audio.wav`
>
>    **步骤 B：生成指纹序列**
>    - 调用 `fpcalc -raw -json <wav_path>` 获取原始指纹数据
>    - 解析 JSON 输出，提取 `duration` 和 `fingerprint` 字段
>    - `fingerprint` 是一个 32 位整数数组（每个整数是一个子指纹，编码约 1.27 秒音频的频谱特征）
>    - 将指纹数组转为 numpy 数组 `fp_full`（`dtype=np.uint32`）
>    - `fingerprint_rate` = `len(fp_full) / duration_sec`（每秒指纹数）
>    - 封装为内部方法 `_generate_fingerprint(wav_path: str) -> tuple[np.ndarray, float]`
>
>    **步骤 C：提取片头模板 + 有效性校验**
>    - 取 `fp_full[:int(template_sec * fingerprint_rate)]` 作为片头模板指纹
>    - 模板有效性校验：计算模板与紧随其后的等长片段的相似度，如果低于 0.5，说明开头并非固定重复片头（可能是广告或非标准内容），记录警告并返回空 `CandidateList`
>
>    **步骤 D：滑窗匹配（位级 Hamming 距离）**
>    - 实现 `fingerprint_similarity(window: np.ndarray, template: np.ndarray) -> float`：
>      - Chromaprint 指纹是 32 位整数数组，不能简单地比较整数是否相等
>      - 正确做法：对每对子指纹做 XOR，统计不同的位数（popcount），位错误率 BER = 总错误位数 / (32 × 子指纹数)
>      - 相似度 = 1 - BER
>      ```python
>      def fingerprint_similarity(window: np.ndarray, template: np.ndarray) -> float:
>          if len(window) != len(template):
>              return 0.0
>          xor_result = np.bitwise_xor(
>              window.astype(np.uint32), template.astype(np.uint32)
>          )
>          try:
>              bit_counts = np.bitwise_count(xor_result)  # NumPy 2.0+
>          except AttributeError:
>              bit_counts = np.vectorize(
>                  lambda x: bin(int(x) & 0xFFFFFFFF).count('1')
>              )(xor_result)
>          total_bits = len(window) * 32
>          total_errors = int(np.sum(bit_counts))
>          return 1.0 - total_errors / total_bits
>      ```
>    - 滑动窗口从 `offset=0` 到 `offset=len(fp_full) - template_len`，计算每个偏移的相似度
>    - 返回 `[(timestamp_sec, similarity), ...]` 列表，其中 `timestamp_sec = offset / fingerprint_rate`
>
>    **步骤 E：峰值检测**
>    - 筛选 `similarity >= threshold` 的点
>    - 在筛选结果中找局部极大值
>    - 合并距离 < 30 秒的峰值（只保留相似度最高的）
>    - 第一个峰值（offset=0 附近，即片头自身）跳过
>
>    **步骤 F：构造 CandidateList**
>    - 每个峰值生成一个 `Candidate`：`signal_type="audio_fingerprint"`、`episode=None`、`confidence` = similarity 值、`metadata = {"similarity": similarity, "peak_rank": 排名序号}`
>    - 按 `timestamp_sec` 升序排列
>
> 3. 容错处理：
>    - `fpcalc` 不可用时抛出 `ChromaprintNotFoundError`
>    - 音轨提取失败或指纹为空时返回空 `CandidateList`
>    - 未找到任何峰值时返回空 `CandidateList` 并记录警告
>
> 4. 性能考量：
>    - 使用 numpy 向量化操作加速 Hamming 距离计算
>    - 可用 `np.lib.stride_tricks.sliding_window_view` 构造滑窗矩阵，一次性计算所有偏移的相似度
>
> **测试要求**
>
> 在 `tests/test_audio_fingerprint.py` 中：
> - 测试 `fingerprint_similarity` 对相同数组返回 1.0
> - 测试 `fingerprint_similarity` 对完全不同数组返回较低值（< 0.5）
> - 测试 `fingerprint_similarity` 对部分匹配数组返回中间值（0.5–0.9）
> - 测试 `peak_detect` 能找到显著峰值
> - 测试 `detect()` 返回的 `CandidateList` 结构正确
> - 测试 `fpcalc` 不可用时抛出 `ChromaprintNotFoundError`
> - 注意：音频指纹测试可能因环境差异不稳定，对相似度阈值和峰值检测使用宽松的断言

---

### 步骤 2.2：黑帧 + 静音联合检测（black_silence）

**提示词**

> 你是一位资深 Python 工程师。在 `drama-splitter` 项目中创建 `src/drama_splitter/detectors/black_silence.py`，实现黑帧与静音联合检测器。
>
> **任务目标**
>
> 利用 FFmpeg 的 blackdetect 和 silencedetect 滤镜，找到视频中同时出现黑帧和静音的时间段。两者的交集作为拼接缝的高置信候选点。
>
> **前置依赖**
>
> - `src/drama_splitter/ffmpeg_wrapper.py` 中的 `detect_black_silence` 函数
> - `src/drama_splitter/models/` 中的 `Candidate`、`CandidateList`
>
> **实现要求**
>
> 1. 定义 `BlackSilenceDetector` 类：
>    ```python
>    class BlackSilenceDetector:
>        def __init__(self, black_duration: float = 0.3, black_pix_threshold: float = 0.10,
>                     silence_noise: str = "-40dB", silence_duration: float = 0.3):
>            ...
>        def detect(self, video_path: str, duration_sec: float, video_name: str) -> CandidateList:
>            """运行黑帧静音检测并输出候选点"""
>    ```
>
> 2. `detect()` 方法流程：
>
>    **步骤 A**：调用 `ffmpeg_wrapper.detect_black_silence` 获取黑帧段和静音段
>
>    **步骤 B：计算交集**
>    - 对每个黑帧段，查找与之有时间重叠的静音段
>    - 重叠条件：`black.start < silence.end` 且 `silence.start < black.end`
>    - 交集时间段：`max(black.start, silence.start)` 到 `min(black.end, silence.end)`
>    - 交集的中点作为候选边界点
>
>    **步骤 C：构造候选点**
>    - `signal_type="black_silence"`、`episode=None`
>    - `confidence` = `min(intersection_duration / 1.0, 1.0)`（交集 ≥ 1 秒时满分）
>    - `metadata` 含黑帧和静音的起止时间及交集时长
>
>    **步骤 D：过滤**
>    - 排除时间戳 < 1.0 秒和 > `duration_sec - 1.0` 的候选点
>    - 合并距离 < 3 秒的候选点
>
>    **步骤 E：降级处理**
>    - 无交集但有黑帧段或静音段时，作为低置信度候选输出（`confidence=0.3`）
>
> 3. 构造 `CandidateList`，按 `timestamp_sec` 升序排列。
>
> **测试要求**
>
> 在 `tests/test_black_silence.py` 中：
> - 使用 FFmpeg 生成测试视频：10 秒正常画面 + 1 秒黑屏静音 + 10 秒正常画面 + 1 秒黑屏静音 + 10 秒正常画面
> - 测试 `detect()` 返回 2 个候选点
> - 测试候选点的 `timestamp_sec` 在预期位置附近
> - 测试无交集时的降级处理
> - 测试 `signal_type` 为 `"black_silence"`

---

### 步骤 2.3：场景突变 + 时长聚类检测（scene_cluster）

**提示词**

> 你是一位资深 Python 工程师。在 `drama-splitter` 项目中创建 `src/drama_splitter/detectors/scene_cluster.py`，实现场景检测与时长聚类检测器。
>
> **任务目标**
>
> 使用 PySceneDetect 扫描视频中的所有场景切换点，结合短剧单集时长特征做直方图聚类，筛选出可能是集边界的切换点。
>
> **前置依赖**
>
> - `scenedetect` 包
> - `src/drama_splitter/models/` 中的 `Candidate`、`CandidateList`
>
> **实现要求**
>
> 1. 定义 `SceneClusterDetector` 类：
>    ```python
>    class SceneClusterDetector:
>        def __init__(self, min_episode_sec: float = 60.0, max_episode_sec: float = 300.0):
>            ...
>        def detect(self, video_path: str, duration_sec: float, video_name: str) -> CandidateList:
>            """场景检测 + 时长聚类，输出候选边界点"""
>    ```
>
> 2. `detect()` 方法流程：
>
>    **步骤 A：场景检测**
>    - 使用 PySceneDetect 的 ContentDetector：
>      ```python
>      from scenedetect import detect, ContentDetector
>      scene_list = detect(video_path, ContentDetector(threshold=27.0), show_progress=False)
>      ```
>    - 提取所有场景切换点时间戳（每个场景的结束时间即下一个场景的开始时间）
>    - 第一个场景的起始时间（0 秒）不算切换点
>
>    **步骤 B：计算间隔**
>    - 计算相邻切换点之间的时间间隔，加上首尾段间隔
>
>    **步骤 C：直方图聚类（算法细化）**
>    - 对所有间隔做直方图统计，bin 宽度为 10 秒，范围 0 到 `max_episode_sec * 2`
>    - 在 `[min_episode_sec, max_episode_sec]` 范围内找到频次最高的 bin，该 bin 的中心值为"典型单集时长" `typical_episode_sec`
>    - 如果该范围内出现双峰（间距 < 30 秒），取频次更高的；频次相同取更接近 120 秒的
>    - 如果该范围内无间隔，`typical_episode_sec` 默认为 120 秒
>    - 实现 `_cluster_by_duration(intervals, min_sec, max_sec) -> float`
>
>    **步骤 D：筛选集边界候选**
>    - 对每个切换点 `t_i`，检查是否存在切换点 `t_j` 使得 `t_j - t_i` 落在 `[typical * 0.8, typical * 1.2]` 范围内
>    - 如果存在，`t_i` 标记为候选集边界，`confidence` = 0.5
>    - 实现 `_filter_by_duration(cut_points, typical_sec, tolerance=0.2) -> list[tuple[float, float]]`
>
>    **步骤 E：构造候选点**
>    - `signal_type="scene_cluster"`、`episode=None`、`confidence=0.5`
>    - `metadata` 含 `interval_to_next` 和 `typical_episode_sec`
>
> 3. 容错处理：
>    - 未检测到场景时返回空 `CandidateList`
>    - 场景数量 < 3 时将所有切换点作为低置信度候选输出
>
> **测试要求**
>
> 在 `tests/test_scene_cluster.py` 中：
> - 使用 FFmpeg 生成测试视频：3 段不同画面拼接，每段约 5 秒
> - 测试 `detect()` 返回的 `CandidateList` 非空
> - 测试 `_cluster_by_duration` 对 `[5, 60, 5, 65, 5, 62]` 的间隔能找到 ~60 秒的典型时长
> - 测试场景数量 < 3 时降级为低置信度输出
> - 测试空场景列表返回空 `CandidateList`

---

### 步骤 2.4：信号采集层并行调度引擎

**提示词**

> 你是一位资深 Python 工程师。在 `drama-splitter` 项目中创建 `src/drama_splitter/detectors/scheduler.py`，实现多信号检测器的并行调度引擎。
>
> **任务目标**
>
> 将四路检测器（OCR、音频指纹、黑帧静音、场景聚类）统一调度，并行运行，独立收集结果。任一检测器失败不影响其他检测器。
>
> **前置依赖**
>
> - 前述所有检测器模块
> - `src/drama_splitter/models/` 中的 `CandidateList`、`VideoMeta`、`AppConfig`
>
> **实现要求**
>
> 1. 定义 `DetectorScheduler` 类：
>    ```python
>    class DetectorScheduler:
>        def __init__(self, config: AppConfig):
>            """根据配置初始化四路检测器实例"""
>        def run(self, video_path: str, video_meta: VideoMeta, work_dir: str,
>                progress_callback: Callable[[str, float], None] | None = None,
>                fresh: bool = False
>                ) -> dict[str, CandidateList]:
>            """并行运行所有检测器，返回 {signal_type: CandidateList} 字典"""
>    ```
>
> 2. `__init__` 中根据 `config` 初始化各检测器实例。
>
> 3. `run()` 方法流程：
>
>    **断点续传检查**：
>    - 如果 `fresh=False`，检查 `work_dir/candidates/<signal_type>.json` 是否已存在
>    - 已存在且可正常 `from_json` 加载的，直接使用缓存结果，跳过该路检测
>    - 记录日志"使用缓存的 {signal_type} 检测结果"
>
>    **OCR 信号链路**（两步链路：抽帧 → OCR → 匹配）：
>    - 封装为 `_run_ocr_pipeline(video_path, video_meta, work_dir) -> CandidateList`
>
>    **其他三路信号各自独立**
>
>    **并行调度**：
>    - 使用 `concurrent.futures.ThreadPoolExecutor(max_workers=4)` 并行运行
>    - **GIL 说明**：ThreadPoolExecutor 对 FFmpeg subprocess 调用（blackdetect、silencedetect、extract_audio）、NumPy 数组运算和 OpenCV 图像处理有效（这些操作释放 GIL）。OCR 链路中 ONNX Runtime C++ 后端也释放 GIL，但 Python 层预处理/后处理受 GIL 限制。实测中四路并行仍比串行快 2–3 倍，因为大部分耗时在原生库中。如果 OCR 成为严重瓶颈，可将其改为 `ProcessPoolExecutor`。
>
>    **错误隔离**：
>    - 每路检测用 try/except 包裹
>    - 某路失败时记录错误日志，该路返回空 `CandidateList`
>    - 四路全失败时抛出 `AllDetectorsFailedError`
>
>    **结果持久化**：
>    - 每路结果保存为 `work_dir/candidates/<signal_type>.json`（实现断点续传）
>
> 4. 进度回调：
>    - `progress_callback(signal_type: str, progress: float)` 其中 progress 为 0.0–1.0
>    - 进度基于各检测器内部实际处理进度，不使用硬编码比例
>    - 各检测器在关键节点（如 OCR 每处理 100 帧、音频指纹每个 10% 偏移区间）调用回调
>
> 5. 日志记录：
>    - 每路检测开始/完成时记录，含耗时和候选点数量
>    - 全部完成时记录汇总
>
> **测试要求**
>
> 在 `tests/test_scheduler.py` 中：
> - 使用 conftest.py 中的测试视频
> - 测试 `run()` 返回包含 4 个 key 的字典
> - 测试某路检测器失败时（mock 抛异常），其他路仍正常返回
> - 测试四路全失败时抛出 `AllDetectorsFailedError`
> - 测试候选点 JSON 文件被正确保存
> - 测试 `progress_callback` 被调用且 progress 在 0–1 范围内
> - 测试断点续传：第二次调用（`fresh=False`）时跳过已有结果的检测器

---

**本阶段交付物**

| 序号 | 交付物 | 验证方式 |
|------|--------|----------|
| 1 | `audio_fingerprint.py` — 位级 Hamming 距离指纹匹配 | 单元测试通过 |
| 2 | `black_silence.py` — 黑帧静音交集检测 | 单元测试通过 |
| 3 | `scene_cluster.py` — 场景检测 + 细化聚类算法 | 单元测试通过 |
| 4 | `scheduler.py` — 四路并行调度 + 断点续传 | 单元测试通过 |
| 5 | **阶段集成测试**：四路检测 + 候选点 JSON 格式统一 | 人工检查 JSON 结构 |

---

## 阶段 3：融合决策引擎

本阶段实现多信号融合。步骤 3.1–3.3 须按顺序执行，步骤 3.4 依赖前三步。

---

### 步骤 3.1：候选点时空聚类（candidate_merger）

**提示词**

> 你是一位资深 Python 工程师。在 `drama-splitter` 项目中创建 `src/drama_splitter/fusion/candidate_merger.py`，实现多信号候选点的时空聚类合并。
>
> **任务目标**
>
> 将四路检测器产出的候选点按时间 proximity 聚类，±3 秒窗口内的点归为同一簇，取簇内中位时间作为该边界的最终时间。
>
> **前置依赖**
>
> - `src/drama_splitter/models/` 中的 `Candidate`、`CandidateList`
>
> **实现要求**
>
> 1. 定义 `MergedBoundary` 数据类：
>    - `timestamp_sec: float`、`signals: list[str]`、`episode: int | None`、`candidates: list[Candidate]`、`signal_count: int`
>
> 2. 定义 `CandidateMerger` 类：
>    ```python
>    class CandidateMerger:
>        def __init__(self, window_sec: float = 3.0):
>            ...
>        def merge(self, candidate_lists: dict[str, CandidateList]) -> list[MergedBoundary]:
>            """合并多路候选点，返回聚类后的边界列表"""
>    ```
>
> 3. `merge()` 方法流程：
>    - **步骤 A**：汇总所有候选点，保留来源 `signal_type`
>    - **步骤 B**：按 `timestamp_sec` 升序排列
>    - **步骤 C**：聚类——相邻候选点时间差 ≤ `window_sec` 归入同一簇
>    - 簇内中位时间 = `np.median`
>    - 簇内信号列表 = 去重后的 `signal_type`
>    - 簇内集数 = 有 OCR 候选点时取其 `episode`；多个不同集数时取 `confidence` 最高的
>    - **步骤 D**：过滤——移除仅 `scene_cluster` 的单信号簇
>    - **特殊处理**：同一簇内有两个不同集数的 OCR 候选点时拆分为两个边界
>
> 4. 边界情况：某路为空时跳过；所有路为空时返回空列表。
>
> **测试要求**
>
> 在 `tests/test_candidate_merger.py` 中：
> - 构造 4 路候选点，部分时间戳接近
> - 测试时间差 ≤ 3 秒的归入同一簇
> - 测试簇内中位时间、信号列表去重、OCR 集数传递
> - 测试仅 `scene_cluster` 的单信号簇被过滤
> - 测试不同集数 OCR 候选点被拆分
> - 测试空输入返回空列表

---

### 步骤 3.2：集数序列对齐与缺口修补（episode_aligner）

**提示词**

> 你是一位资深 Python 工程师。在 `drama-splitter` 项目中创建 `src/drama_splitter/fusion/episode_aligner.py`，实现集数序列对齐与缺口修补逻辑。
>
> **任务目标**
>
> 以 OCR 集数序列为骨架，将其他信号的候选点向骨架对齐。对于 OCR 漏识别导致的集数缺口，利用附近的其他信号候选点推断缺失集数并补全。
>
> **前置依赖**
>
> - `src/drama_splitter/fusion/candidate_merger.py` 中的 `MergedBoundary`
> - `src/drama_splitter/models/` 中的 `Episode`
>
> **实现要求**
>
> 1. 定义 `AlignedEpisode` 数据类：
>    - `episode: int`、`timestamp_sec: float`、`signals: list[str]`、`is_inferred: bool`、`confidence_note: str`
>
> 2. 定义 `EpisodeAligner` 类：
>    ```python
>    class EpisodeAligner:
>        def align(self, boundaries: list[MergedBoundary], duration_sec: float) -> list[AlignedEpisode]:
>            """将候选边界对齐到集数序列"""
>    ```
>
> 3. `align()` 方法流程：
>
>    **情况 A：有 OCR 骨架（≥ 2 个带 episode 的边界）**
>    - 提取带 episode 的边界按集数升序排列作为骨架
>    - 遍历相邻两集，集数差 > 1 时在时间区间内查找非 OCR 边界点
>    - 找到则推断为缺失集数，`is_inferred=True`
>    - 找到多个取最接近"前后两集时间中点"的
>    - 未找到记录警告
>
>    **情况 B：OCR 骨架不足（0 或 1 个）**
>    - 退化为按时间排序 + 自动编号模式
>    - 从第 1 集开始顺序编号，`is_inferred=True`
>    - 空边界列表抛出 `NoBoundariesError`
>
>    **情况 C：OCR 骨架完整**
>    - 直接使用骨架，`is_inferred=False`
>
> 4. 集数序列规范化：
>    - 最小集数 > 1 且 ≤ 5 时前插缺失集
>    - 最小集数 > 5 时不前插，记录警告
>
> **测试要求**
>
> 在 `tests/test_episode_aligner.py` 中：
> - 构造 3 个 OCR 边界（1, 2, 4）+ 1 个无集数边界在 2 和 4 之间，测试缺口补全为第 3 集
> - 测试 OCR 骨架完整时非 OCR 边界被丢弃
> - 测试 OCR 骨架不足时自动编号
> - 测试最小集数 > 1 时前插逻辑
> - 测试空边界列表抛出 `NoBoundariesError`

---

### 步骤 3.3：置信度分级标注（confidence_scorer）

**提示词**

> 你是一位资深 Python 工程师。在 `drama-splitter` 项目中创建 `src/drama_splitter/fusion/confidence_scorer.py`，实现置信度分级标注和最终时间清单生成。
>
> **任务目标**
>
> 根据每集边界命中的信号种类和数量，标注置信度等级，并生成最终的 `TimeList`。
>
> **前置依赖**
>
> - `src/drama_splitter/fusion/episode_aligner.py` 中的 `AlignedEpisode`
> - `src/drama_splitter/models/` 中的 `TimeList`、`Episode`
> - `src/drama_splitter/models/config.py` 中的 `AppConfig`
>
> **实现要求**
>
> 1. 定义 `ConfidenceScorer` 类：
>    ```python
>    class ConfidenceScorer:
>        def __init__(self, min_episode_sec: float = 60.0, max_episode_sec: float = 300.0):
>            ...
>        def score(self, aligned_episodes: list[AlignedEpisode], duration_sec: float,
>                  video_name: str) -> TimeList:
>            """标注置信度并生成最终时间清单"""
>    ```
>
> 2. **置信度分级规则（三份文档统一标准）**：
>
>    | 置信度 | 判定条件 |
>    |--------|----------|
>    | `high` | OCR 集数命中 + 至少 1 个其他信号在 ±3 秒窗口内同时命中 |
>    | `medium` | 仅 OCR 命中（无其他信号佐证），或 `is_inferred=True` 但有 ≥ 2 个其他信号佐证 |
>    | `low` | `is_inferred=True` 且其他信号佐证 < 2 个，或仅单一弱信号命中 |
>
> 3. `score()` 方法流程：
>
>    **步骤 A：逐集评级**
>    - 遍历 `aligned_episodes`，应用上述分级规则
>    - 检查 `signals` 列表判断信号命中情况
>
>    **步骤 B：计算时间区间**
>    - `start_sec` = 该集边界时间戳
>    - `end_sec` = 下一集 `start_sec`（最后一集 = `duration_sec`）
>    - 第一集 `start_sec` > 5 秒时修正为 0.0
>
>    **步骤 C：异常检测**
>    - 时长 < `min_episode_sec` → 标记 `low` 并警告
>    - 时长 > `max_episode_sec` → 标记 `low` 并警告
>    - 时间重叠 → 修正为 `end_sec = 上一集.end_sec`
>
>    **步骤 D：构造 TimeList**，记录统计日志（高/中/低各多少集）
>
> **测试要求**
>
> 在 `tests/test_confidence_scorer.py` 中：
> - 测试 OCR + audio_fingerprint 命中 → `high`
> - 测试仅 OCR 命中 → `medium`
> - 测试 `is_inferred=True` 且仅 1 个信号 → `low`
> - 测试第一集 `start_sec` > 5 被修正为 0
> - 测试时长异常被标记 `low`
> - 测试生成的 `TimeList` 能通过模型验证器

---

### 步骤 3.4：融合引擎整合与统一导出

**提示词**

> 你是一位资深 Python 工程师。在 `drama-splitter` 项目中创建 `src/drama_splitter/fusion/engine.py`，将聚类、对齐、评分三步整合为统一的融合决策引擎。
>
> **任务目标**
>
> 将 `CandidateMerger` → `EpisodeAligner` → `ConfidenceScorer` 三步串联为一条完整的融合流水线，接收四路候选点 JSON，输出最终时间清单。
>
> **前置依赖**
>
> - 前述三个融合模块
> - `src/drama_splitter/models/` 中的 `CandidateList`、`TimeList`、`AppConfig`
>
> **实现要求**
>
> 1. 定义 `FusionEngine` 类：
>    ```python
>    class FusionEngine:
>        def __init__(self, config: AppConfig):
>            ...
>        def fuse(self, candidate_lists: dict[str, CandidateList], duration_sec: float,
>                 video_name: str) -> TimeList:
>            """执行完整融合流水线"""
>        def fuse_from_files(self, candidate_dir: str, duration_sec: float,
>                            video_name: str) -> TimeList:
>            """从候选点 JSON 文件目录加载并融合"""
>    ```
>
> 2. `fuse()` 方法：依次调用 merger → aligner → scorer，每步记录日志。
>
> 3. `fuse_from_files()` 方法：从 `candidate_dir` 加载所有 `<signal_type>.json`，缺少某路时跳过不报错。
>
> 4. **断点续传**：融合完成后将 `TimeList` 写入 `work_dir/time_list.json`。下次调用时如果该文件存在且 `fresh=False`，直接加载返回。
>
> 5. 导出功能：
>    - `export_json(time_list, output_path)` — 调用 `time_list.to_json()`
>    - `export_csv(time_list, output_path)` — 调用 `time_list.to_csv()`
>    - `export_summary(time_list) -> str` — 生成人类可读文本摘要
>
> **测试要求**
>
> 在 `tests/test_fusion_engine.py` 中：
> - 构造 4 路 `CandidateList`（部分为空），测试 `fuse()` 返回结构正确
> - 测试 `fuse_from_files()` 能从 JSON 加载并融合
> - 测试缺少某路 JSON 时不报错
> - 测试 `export_json` / `export_csv` / `export_summary` 输出正确
> - 测试断点续传：第二次调用跳过融合直接加载 `time_list.json`

---

**本阶段交付物**

| 序号 | 交付物 | 验证方式 |
|------|--------|----------|
| 1 | `candidate_merger.py` — ±3 秒窗口聚类 | 单元测试通过 |
| 2 | `episode_aligner.py` — OCR 骨架对齐 + 缺口补全 | 单元测试通过 |
| 3 | `confidence_scorer.py` — 三级置信度（统一标准） | 单元测试通过 |
| 4 | `engine.py` — 融合流水线 + 断点续传 + 多格式导出 | 单元测试通过 |
| 5 | **阶段集成测试**：四路候选点 → 融合 → 时间清单 | 高置信度集占比 ≥ 50% |

---

## 阶段 4：体验闭环——CLI、Web UI 与打包

本阶段将前三阶段的核心能力封装为用户可操作的产品形态。步骤 4.1 须先于 4.2；步骤 4.2 和 4.3 须顺序执行；步骤 4.4 在前三步完成后进行。

---

### 步骤 4.1：CLI 入口整合

**提示词**

> 你是一位资深 Python 工程师。在 `drama-splitter` 项目中完善 `src/drama_splitter/cli.py`，实现完整的命令行入口。
>
> **任务目标**
>
> 将检测、融合、裁剪三层能力整合为统一的 CLI 工具，支持三个子命令：`analyze`、`cut`、`run`。
>
> **前置依赖**
>
> - 前述所有核心模块
> - `click` 和 `rich` 包
> - `src/drama_splitter/env_check.py`（需创建，见下文）
>
> **实现要求**
>
> 1. 创建 `src/drama_splitter/env_check.py`：
>    ```python
>    def check_environment() -> dict[str, bool]:
>        """检查 FFmpeg、ffprobe、fpcalc 是否可用"""
>        return {
>            "ffmpeg": shutil.which("ffmpeg") is not None,
>            "ffprobe": shutil.which("ffprobe") is not None,
>            "fpcalc": shutil.which("fpcalc") is not None,
>        }
>    def print_env_report():
>        """打印环境检测报告，缺失项给出安装提示"""
>    ```
>
> 2. 使用 Click 框架定义 CLI 组和三个子命令：
>    - `analyze`：仅执行检测和融合，输出时间清单 JSON
>    - `cut`：读取时间清单执行裁剪
>    - `run`：一键执行全流程
>
> 3. 全局选项：
>    - `--config` / `-c`：配置文件路径，默认 `config.yaml`
>    - `--output` / `-o`：输出目录，默认 `./output`
>    - `--fresh`：强制忽略缓存，重新执行全流程
>    - `--verbose` / `-v`：DEBUG 日志级别
>
> 4. `analyze` 子命令流程：
>    - 调用 `check_environment()`，ffmpeg/ffprobe 不可用时退出
>    - 加载配置：`AppConfig.from_yaml(config)`，合并 CLI 参数，调用 `validate()`
>    - 调用 `probe_video(video_path)` 获取 `VideoMeta`
>    - 创建工作目录 `output/<video_name>/`
>    - 调用 `DetectorScheduler(config).run(video_path, video_meta, work_dir, fresh=fresh)`
>    - 用 rich Progress 显示四路检测进度
>    - 调用 `FusionEngine(config).fuse_from_files(work_dir + "/candidates/", ...)`
>    - 将时间清单保存到 `work_dir/time_list.json`
>    - 用 rich Table 打印时间清单摘要
>    - 打印低置信度集的警告提示
>
> 5. `cut` 子命令流程：
>    - 加载时间清单：`TimeList.from_json(time_list_path)`
>    - 用 rich 打印清单摘要
>    - 无 `--yes` 时用 `click.confirm` 确认
>    - 调用 `Cutter(mode, naming_template).cut(video_path, time_list, output)`
>
> 6. `run` 子命令流程：
>    - 依次执行 `analyze` 和 `cut` 的逻辑
>    - analyze 完成后展示时间清单摘要
>    - 无 `--yes` 时确认"是否开始裁剪？"
>
> 7. 错误处理：
>    - 捕获所有 `DramaSplitterError` 子类，用 rich 打印友好错误信息
>    - 环境异常给出安装提示
>    - 所有异常以 `click.ClickException` 或 `sys.exit(1)` 退出，不暴露 traceback
>
> 8. 日志配置：
>    - `main()` 中调用 `setup_logging(output_dir, verbose=verbose)`
>
> **测试要求**
>
> 在 `tests/test_cli.py` 中：
> - 使用 `click.testing.CliRunner` 测试 CLI
> - 测试 `analyze` 命令产出 `time_list.json`
> - 测试 `cut` 命令产出裁剪文件
> - 测试 `run` 命令一键执行
> - 测试 `--yes` 跳过确认
> - 测试 `--fresh` 忽略缓存
> - 测试缺少 FFmpeg 时的错误提示
> - 测试 `--help` 输出包含所有子命令

---

### 步骤 4.2：Streamlit Web UI 基础框架与文件输入

**提示词**

> 你是一位资深 Python 工程师。在 `drama-splitter` 项目中创建 `src/drama_splitter/ui/app.py`，实现 Streamlit Web UI 的基础框架和模块一"文件输入与任务配置"。
>
> **任务目标**
>
> 搭建 Streamlit 应用骨架，实现 PRD 中模块一的全部交互。检测流程通过后台进程运行，避免阻塞 UI。
>
> **前置依赖**
>
> - `streamlit` 包
> - `src/drama_splitter/ffmpeg_wrapper.py` 中的 `probe_video`
> - `src/drama_splitter/models/config.py` 中的 `AppConfig`
>
> **实现要求**
>
> 1. 应用入口 `src/drama_splitter/ui/app.py`：
>    ```python
>    import streamlit as st
>    def main():
>        st.set_page_config(page_title="短剧分集工具", layout="wide")
>        st.title("短剧总集篇自动分集工具")
>        if "step" not in st.session_state:
>            st.session_state.step = "input"
>        # 根据 step 渲染对应模块
>    ```
>
> 2. 使用 `st.session_state` 管理多步骤流程状态：
>    - `step`: `"input"` / `"analyzing"` / `"review"` / `"cutting"` / `"done"`
>    - `video_path`、`video_meta`、`config`、`time_list`、`work_dir`
>
> 3. **后台进程方案（解决 Streamlit 阻塞问题）**：
>    - 检测流程通过 `subprocess.Popen` 在后台进程运行 CLI 命令：
>      ```python
>      proc = subprocess.Popen(
>          ["python", "-m", "drama_splitter", "analyze", video_path,
>           "-o", work_dir, "--fresh"],
>          stdout=subprocess.PIPE, stderr=subprocess.PIPE
>      )
>      ```
>    - 进程将进度写入 `work_dir/progress.json`（CLI 端通过 `progress_callback` 写入）
>    - Streamlit 页面通过 `st.rerun()` + `time.sleep(2)` 轮询 `progress.json` 更新 UI
>    - 进程完成后读取 `work_dir/time_list.json`
>    - 用 `st.warning("分析进行中，请勿关闭页面")` 提示用户
>
> 4. 模块一"文件输入与任务配置"的实现：
>
>    **文件选择区**：
>    - 改用文本输入框让用户输入本地视频文件路径（大文件上传体验差）
>    - 输入路径后点击"加载视频"，调用 `probe_video(path)`
>
>    **视频信息展示条**（加载成功后）：
>    - 用 `st.columns` 横向排列：文件名、格式、分辨率、时长、文件大小
>    - 提供"移除"按钮
>
>    **配置面板**：
>    - `st.selectbox("OCR 引擎", ...)` → `rapidocr` / `paddleocr`
>    - `st.select_slider("抽帧间隔", ...)` → 0.25s / 0.5s / 1.0s
>    - `st.selectbox("裁剪模式", ...)` → `lossless` / `reencode`
>    - `st.text_input("文件命名模板", value="第{ep:02d}集.mp4")`，下方显示预览
>    - `st.expander("高级配置")` 折叠音频指纹阈值等
>
>    **启动按钮**：
>    - `st.button("开始分析", type="primary")` → 设置 `step = "analyzing"` 并启动后台进程
>    - 点击前校验：路径非空、命名模板包含 `{ep}`
>
> 5. 界面美化：
>    - 页面顶部显示步骤导航条（输入 → 分析 → 复核 → 裁剪 → 完成），当前步骤高亮
>
> **测试要求**
>
> 在 `tests/test_ui.py` 中：
> - 测试底层逻辑函数（路径校验、配置构建），不依赖 Streamlit 测试 API
> - 测试 `validate_video_path(path)` 对有效/无效路径的返回
> - 测试 `build_config_from_ui(params)` 正确构建 `AppConfig`
> - 测试 `validate_naming_template(template)` 对含/不含 `{ep}` 的返回
> - 注意：`streamlit.testing.AppTest` 是实验性 API，版本差异大，优先测试底层逻辑

---

### 步骤 4.3：时间轴可视化与低置信复核交互

**提示词**

> 你是一位资深 Python 工程师。在 `drama-splitter` 项目中完善 `src/drama_splitter/ui/app.py`，实现模块三"时间清单查看与人工复核"和模块四"批量裁剪与输出"。
>
> **任务目标**
>
> 在 Streamlit 中实现时间轴可视化、集数清单表格、低置信项复核交互、以及裁剪输出控制。
>
> **前置依赖**
>
> - `src/drama_splitter/ui/app.py`（步骤 4.2 的产出）
> - `src/drama_splitter/models/` 中的 `TimeList`、`Episode`
> - `src/drama_splitter/output/cutter.py` 中的 `Cutter`
> - `src/drama_splitter/ffmpeg_wrapper.py` 中的 `extract_frames`
>
> **实现要求**
>
> 1. 模块三"时间清单查看与人工复核"（`step == "review"`）：
>
>    **时间轴可视化**：
>    - 用 matplotlib 绘制水平时间轴，每集用色块表示
>    - 颜色映射：高=绿色、中=黄色、低=红色、已确认=蓝色边框
>    - 缓存到 `st.session_state.timeline_fig`
>
>    **集数清单表格**：
>    - `st.dataframe` 展示集数、开始时间、结束时间、时长、置信度、操作
>    - 使用 `st.session_state.edited_episodes` 维护修改后的清单
>
>    **详情面板**：
>    - 边界帧预览：`extract_frames` 在 `start_sec` 前后 ±2 秒抽 3 帧，`st.image` 展示
>    - 时间微调：`st.number_input` 调整起止时间
>    - "确认此集"按钮
>
>    **复核摘要与全部确认**：
>    - 底部统计：已确认 X 集 / 共 Y 集，低置信未复核 Z 集
>    - "全部确认"按钮，有低置信项时弹出警告
>
> 2. 模块四"批量裁剪与输出"（`step == "cutting"`）：
>
>    **输出配置区**：
>    - `st.text_input("输出目录")`
>    - 显示文件命名预览
>    - `st.button("开始裁剪", type="primary")`
>
>    **裁剪进度**（同样使用后台进程方案）：
>    - 通过 `subprocess.Popen` 运行 CLI `cut` 命令
>    - 轮询进度更新 UI
>
>    **输出文件列表**：
>    - 裁剪完成后 `st.dataframe` 展示每集文件名、大小、时长、状态
>
>    **完成提示**：
>    - `st.success("裁剪完成！共输出 N 集")`
>    - "打开输出目录"按钮（`subprocess` 调用系统文件管理器）
>    - "处理下一个视频"按钮（重置 `st.session_state`）
>
> 3. 状态管理：
>    - 所有用户编辑存储在 `st.session_state.edited_episodes`
>    - 帧预览缓存在磁盘 `work_dir/previews/`
>    - 页面刷新不丢失编辑（所有状态走 `st.session_state`）
>
> **测试要求**
>
> 在 `tests/test_ui_review.py` 中：
> - 测试底层逻辑函数，不依赖 Streamlit 测试 API
> - 测试 `format_timestamp(sec)` 时间格式化
> - 测试 `validate_edited_episodes(episodes)` 检测时间冲突
> - 测试 `build_timeline_data(time_list)` 返回正确的绘图数据结构
> - 测试 `merge_edits(original, edits)` 正确合并用户修改

---

### 步骤 4.4：PyInstaller 打包、端到端测试与 CI 配置

**提示词**

> 你是一位资深 Python 工程师。在 `drama-splitter` 项目中完成打包配置、端到端测试和 CI 流水线配置。
>
> **任务目标**
>
> 使用 PyInstaller 将项目打包为单目录可执行文件。编写端到端测试验证完整流程。配置 CI 流水线。
>
> **前置依赖**
>
> - `pyinstaller` 包
> - 项目所有模块已完成
>
> **实现要求**
>
> 1. 创建 `drama-splitter.spec`（PyInstaller 打包配置）：
>    - 入口：`src/drama_splitter/cli.py`
>    - 打包模式：`--onedir`（单目录，启动更快）
>    - **ONNX Runtime 动态库处理（关键修正）**：
>      ```python
>      from PyInstaller.utils.hooks import collect_dynamic_libs, collect_data_files
>      binaries = collect_dynamic_libs("onnxruntime")
>      datas = collect_data_files("rapidocr_onnxruntime")
>      ```
>    - hidden imports：`rapidocr_onnxruntime`、`pyacoustid`、`scenedetect.detectors.content_detector`、`onnxruntime.capi._pybind_state`
>    - excludes：`matplotlib.tests`、`PySide6`、`tkinter`、`pytest`
>    - 控制台窗口：`console=True`
>
> 2. 环境检测 `env_check.py`（已在步骤 4.1 创建），确保：
>    - ffmpeg/ffprobe 不可用 → 报错退出
>    - fpcalc 不可用 → 警告但允许继续
>    - RapidOCR 模型文件缺失 → 报错退出
>
> 3. 打包脚本 `scripts/build.sh`：
>    ```bash
>    #!/bin/bash
>    set -e
>    pip install -e ".[dev]"
>    pyinstaller drama-splitter.spec --clean --noconfirm
>    echo "打包完成：dist/drama-splitter/"
>    ```
>
> 4. 端到端测试 `tests/test_e2e.py`：
>    - 测试视频合成脚本 `tests/fixtures/generate_test_video.py`：
>      - 用 FFmpeg 生成 3 段不同颜色纯色画面，每段开头叠加"第X集"标题卡（PIL 生成图片）
>      - 段间插入 1 秒黑屏静音
>      - 输出 `tests/fixtures/test_drama.mp4`（约 33 秒，3 集）
>    - 测试流程：
>      1. `probe_video` 验证视频可读取
>      2. `DetectorScheduler.run()` 运行四路检测
>      3. `FusionEngine.fuse()` 生成时间清单
>      4. 验证时间清单有 3 集
>      5. 验证至少 1 集为 high 置信度
>      6. `Cutter.cut()` 裁剪
>      7. 验证输出 3 个文件
>      8. 验证每个输出文件可被 `probe_video` 读取且时长合理
>    - 测试 `env_check.check_environment()` 返回正确结果
>    - 测试 CLI 的 `run` 命令端到端执行（`CliRunner`）
>
> 5. CI 配置 `.github/workflows/ci.yml`（如使用 GitHub Actions）：
>    - 触发条件：push 和 PR
>    - 步骤：
>      - 安装 Python 3.10+
>      - 安装 FFmpeg 和 fpcalc
>      - `pip install -e ".[dev]"`
>      - `ruff check src/`
>      - `pytest --cov=src/drama_splitter --cov-report=term-missing --cov-fail-under=80`
>    - 测试矩阵：Ubuntu、macOS（Windows 可选）
>
> 6. 创建 `README.md`：
>    - 项目简介
>    - 安装方式（pip 安装 / 可执行文件下载）
>    - 外部依赖说明（FFmpeg、fpcalc 安装方法）
>    - 快速开始（CLI 示例命令）
>    - Web UI 启动方式
>    - 配置项说明表格
>    - 常见问题（FAQ）
>
> **测试要求**
>
> - `pytest tests/test_e2e.py -v --tb=long` 全部通过
> - `pytest --cov=src/drama_splitter --cov-report=term-missing` 覆盖率 ≥ 80%
> - 打包后 `dist/drama-splitter/drama-splitter --help` 正常输出
> - 打包后 `dist/drama-splitter/drama-splitter run tests/fixtures/test_drama.mp4 -y` 能完整执行

---

**本阶段交付物**

| 序号 | 交付物 | 验证方式 |
|------|--------|----------|
| 1 | `cli.py` — 三子命令 CLI，含 `--fresh` 断点续传 | `drama-splitter --help` 显示子命令；`run` 命令端到端通过 |
| 2 | `ui/app.py` — Streamlit Web UI，后台进程避免阻塞 | `streamlit run ui/app.py` 走完全流程 |
| 3 | `env_check.py` — 外部依赖检测 | 缺少 ffmpeg 给出指引；缺少 fpcalc 警告但继续 |
| 4 | `drama-splitter.spec` — PyInstaller 配置，含 ONNX Runtime 动态库收集 | `bash scripts/build.sh` 成功产出 |
| 5 | `test_e2e.py` + `generate_test_video.py` — 端到端测试 | `pytest tests/test_e2e.py` 全通过 |
| 6 | `.github/workflows/ci.yml` — CI 流水线 | ruff + pytest + 覆盖率 ≥ 80% |
| 7 | `README.md` — 安装、使用、配置说明 | 人工审阅完整准确 |
| 8 | 代码覆盖率 ≥ 80% | `pytest --cov` 报告确认 |

---

## 附录：阶段依赖关系与并行度

```
阶段 0（基础架构）
  0.1 → 0.2 → 0.3
        │
        ▼
阶段 1（最短路径）
  1.1 → 1.2 → 1.3 → 1.4 → 1.5
        │
        ▼
阶段 2（信号补强）          ← 2.1 / 2.2 / 2.3 可并行
  2.1 ─┐
  2.2 ─┼→ 2.4
  2.3 ─┘
        │
        ▼
阶段 3（融合决策）
  3.1 → 3.2 → 3.3 → 3.4
        │
        ▼
阶段 4（体验闭环）
  4.1 → 4.2 → 4.3 → 4.4
```

| 阶段 | 步骤数 | 可并行步骤 | 预计耗时 |
|------|--------|------------|----------|
| 阶段 0 | 3 | 无 | 1–1.5 天 |
| 阶段 1 | 5 | 无 | 2–3 天 |
| 阶段 2 | 4 | 步骤 2.1 / 2.2 / 2.3 | 3–4 天 |
| 阶段 3 | 4 | 无 | 2–3 天 |
| 阶段 4 | 4 | 无 | 3–4 天 |
| 合计 | 20 | — | 11–15.5 天 |

每个阶段完成后运行该阶段的端到端冒烟测试。阶段 1 完成后即可用真实短剧总集篇验证 OCR 单信号效果；阶段 3 完成后可验证多信号融合效果；阶段 4 完成后工具完整可用。
