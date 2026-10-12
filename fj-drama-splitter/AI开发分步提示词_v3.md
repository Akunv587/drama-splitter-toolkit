# 短剧总集篇自动分集工具 — AI 开发分步提示词

> 版本：v3.0
> 日期：2026-08-10
> 变更记录：v3.0 统一置信度规则、修复接口签名、新增环境准备步骤和自检清单、日志前置、路径适配 Windows

> 使用说明：按 Step 0 → Step 13 的顺序，每步将提示词完整粘贴给 AI，等 AI 完成后检查产出，再进入下一步。
> 每步提示词已包含完整的上下文，无需额外补充。
> 所有代码文件输出到项目根目录 `drama-splitter/` 下。

---

## 全局规则（每步均适用）

以下规则在所有 Step 中均需遵守，无需重复说明：

1. **置信度规则**：2+信号吻合 = HIGH，1信号 = LOW，**不存在 MEDIUM**
2. **数据模型**：使用 `@dataclass` 定义，confidence/status/source 字段使用 `str` 枚举常量
3. **日志**：每个模块使用 `logging.getLogger(__name__)` 获取独立 logger
4. **配置**：所有可调参数从 `config.py` 的 `AppConfig` dataclass 获取
5. **类型标注**：所有公开函数和方法必须有完整的类型标注
6. **错误处理**：使用自定义异常类（定义在 `modules/exceptions.py`）
7. **工作目录**：所有路径使用 `os.path.join()` 拼接，兼容 Windows

---

## Step 0：环境准备

```
你是 Python 项目初始化工程师。请为"短剧总集篇自动分集工具"项目准备开发环境。

## 项目背景
这是一个本地运行的 Python Web 应用，用于将 YouTube 下载的短剧总集篇（1小时以上长视频）自动拆分成单集视频。
核心功能：通过视频闪白检测 + 音频静音检测 + 字幕间隙分析三种信号交叉验证，自动定位集间分割点，经人工确认后批量裁剪输出。

## 请完成以下工作

### 1. 检查环境依赖
- Python 版本 >= 3.10
- ffmpeg 已安装且在 PATH 中
- pip 可正常使用

### 2. 创建项目目录结构
```
drama-splitter/
├── main.py
├── config.py
├── requirements.txt
├── requirements-dev.txt
├── README.md
├── .gitignore
├── modules/
│   ├── __init__.py
│   ├── models.py
│   ├── exceptions.py
│   ├── file_manager.py
│   ├── flash_detector.py
│   ├── silence_detector.py
│   ├── subtitle_parser.py
│   ├── cross_validator.py
│   ├── cutter.py
│   └── task_queue.py
├── web/
│   ├── __init__.py
│   ├── app.py
│   └── components.py
├── utils/
│   ├── __init__.py
│   ├── time_utils.py
│   ├── ffmpeg_check.py
│   └── logger.py
├── tests/
│   ├── __init__.py
│   ├── conftest.py
│   ├── test_time_utils.py
│   ├── test_subtitle_parser.py
│   ├── test_cross_validator.py
│   ├── test_cutter.py
│   └── test_file_manager.py
├── outputs/
├── uploads/
└── logs/
```

### 3. 创建 .gitignore
```
__pycache__/
*.pyc
uploads/
outputs/
logs/
.venv/
*.egg-info/
.pytest_cache/
.mypy_cache/
```

### 4. 创建 requirements.txt
```
gradio==4.44.0
opencv-python==4.10.0.68
librosa==0.10.2
ffmpeg-python==0.2.0
pysrt==1.1.2
webvtt-py==0.5.1
python-multipart==0.0.9
pydantic==2.9.0
numpy==1.26.4
```

### 5. 创建 requirements-dev.txt
```
pytest==8.3.0
pytest-cov==5.0.0
mypy==1.11.0
```

### 6. 创建所有 __init__.py（空文件）

### 7. 创建空目录占位文件
- outputs/.gitkeep
- uploads/.gitkeep
- logs/.gitkeep

## 自检清单
- [ ] Python 版本 >= 3.10 已确认
- [ ] ffmpeg 可用（`ffmpeg -version` 有输出）
- [ ] 所有目录和文件已创建
- [ ] .gitignore 包含 uploads/outputs/logs/__pycache__
- [ ] requirements.txt 和 requirements-dev.txt 内容完整
- [ ] 所有 __init__.py 已创建
```

---

## Step 1：数据模型与基础设施

```
你是 Python 后端开发工程师。请为"短剧总集篇自动分集工具"创建核心数据模型、配置管理和日志系统。

## 项目路径
工作目录：drama-splitter/

## 依赖关系
本步骤无前置依赖，是所有后续步骤的基础。

## 请完成以下文件

### 1. modules/models.py

使用 @dataclass 定义以下数据模型，并定义枚举类型：

```python
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class ConfidenceLevel(str, Enum):
    """置信度等级：只有 HIGH 和 LOW，不存在 MEDIUM"""
    HIGH = "HIGH"
    LOW = "LOW"


class TaskStatusEnum(str, Enum):
    """任务状态"""
    PENDING = "pending"
    ANALYZING = "analyzing"
    AWAITING_CONFIRM = "awaiting_confirm"
    CUTTING = "cutting"
    COMPLETED = "completed"
    FAILED = "failed"


class SignalSource(str, Enum):
    """信号源类型"""
    FLASH = "flash"
    SILENCE = "silence"
    SUBTITLE = "subtitle"


# SubtitleItem：字幕条目
#   - index: int
#   - start: float (秒)
#   - end: float (秒)
#   - text: str

# WordTimestamp：逐词时间戳（VTT 专用）
#   - word: str
#   - start: float (秒)

# MergedPoint：交叉验证后的分割点
#   - timestamp: float (秒)
#   - confidence: str  # ConfidenceLevel.HIGH or ConfidenceLevel.LOW
#   - sources: list[str]  # ['flash', 'silence', 'subtitle']

# EpisodeSegment：分集清单条目
#   - episode_num: int
#   - start_time: float (秒)
#   - end_time: float (秒)
#   - confidence: str  # ConfidenceLevel
#   - has_subtitle_conflict: bool
#   - conflict_detail: str | None = None

# CutResult：裁剪结果
#   - episode_num: int
#   - output_path: str
#   - success: bool
#   - error: str | None = None

# TaskStatus：任务状态
#   - task_id: str
#   - status: str  # TaskStatusEnum
#   - video_path: str | None = None
#   - audio_path: str | None = None
#   - subtitle_path: str | None = None
#   - drama_name: str | None = None
#   - episodes: list[EpisodeSegment] | None = None
#   - cut_results: list[CutResult] | None = None
#   - error_message: str | None = None
#   - created_at: datetime = field(default_factory=datetime.now)
#   - updated_at: datetime = field(default_factory=datetime.now)
```

### 2. modules/exceptions.py

定义自定义异常层次：
```python
class DramaSplitterError(Exception): ...      # 基类
class FileNotFoundError(DramaSplitterError): ...
class FileFormatError(DramaSplitterError): ...
class FFmpegError(DramaSplitterError): ...
class SubtitleParseError(DramaSplitterError): ...
class AnalysisError(DramaSplitterError): ...
class CutError(DramaSplitterError): ...
```

### 3. config.py

用 @dataclass 封装所有可配置参数，支持 from_env() 从环境变量加载：

```python
# 闪白检测参数
white_threshold: int = 220
spike_threshold: int = 80
max_flash_duration: float = 1.0
merge_distance: float = 2.0
min_episode_duration: float = 30.0
target_sample_fps: int = 15

# 音频静音检测参数
silence_threshold: float = 0.01
min_silence_duration: float = 0.3
max_silence_duration: float = 5.0
frame_length: int = 2048
hop_length: int = 512

# 交叉验证参数
match_window: float = 3.0

# 裁剪参数
cut_max_workers: int = max(1, (os.cpu_count() or 2) - 1)

# 路径配置
upload_dir: str = "uploads"
output_dir: str = "outputs"
log_dir: str = "logs"

# Gradio 配置
server_name: str = "0.0.0.0"
server_port: int = 7860
max_file_size: int = 2 * 1024 * 1024 * 1024  # 2GB
```

### 4. utils/logger.py

实现 setup_logging(log_dir, level) 函数：
- 控制台输出 INFO 及以上
- 文件输出 DEBUG 及以上，按天轮转（TimedRotatingFileHandler），保留 7 天
- 日志格式：'%(asctime)s [%(name)s] %(levelname)s: %(message)s'

## 自检清单
- [ ] models.py 中所有枚举和数据模型定义完整
- [ ] ConfidenceLevel 只有 HIGH 和 LOW（无 MEDIUM）
- [ ] exceptions.py 异常层次完整
- [ ] config.py 的 AppConfig dataclass 包含所有参数
- [ ] config.py 的 from_env() 可从环境变量覆盖默认值
- [ ] logger.py 的 setup_logging() 可正常初始化日志
- [ ] 所有文件有完整的类型标注
```

---

## Step 2：时间工具与环境检测

```
你是 Python 后端开发工程师。请为"短剧总集篇自动分集工具"编写两个工具模块。

## 项目路径
工作目录：drama-splitter/

## 依赖关系
依赖 Step 1 的 models.py。

## 请完成以下文件

### 1. utils/time_utils.py

实现以下函数：

```python
def seconds_to_timestamp(seconds: float) -> str:
    """将秒数转为 HH:MM:SS 格式字符串。如 142.3 → '00:02:22'"""

def timestamp_to_seconds(ts: str) -> float:
    """将时间戳字符串转为秒数。支持 HH:MM:SS 和 MM:SS 格式。
    如 '00:02:22' → 142.0, '02:30' → 150.0
    也支持 SRT 格式 '00:02:22,160' → 142.16
    也支持 VTT 格式 '00:02:22.160' → 142.16"""

def vtt_timestamp_to_seconds(ts: str) -> float:
    """将 VTT 时间戳 '00:01:23.456' 转为秒数。
    注意 VTT 用点号分隔毫秒，SRT 用逗号。"""

def parse_manual_timestamps(text: str) -> list[float]:
    """解析用户粘贴的起始时间列表。
    输入格式：'00:00:00, 00:02:30, 00:05:00'
    支持 HH:MM:SS 和 MM:SS 两种格式。
    返回 [0.0, 150.0, 300.0]"""

def format_duration(seconds: float) -> str:
    """将秒数格式化为可读时长。如 142.3 → '2\'22"'"""

def check_subtitle_conflict(cut_ts: float, subs: list) -> bool:
    """检查切割点是否落在某条字幕的 [start, end] 区间内（台词冲突）。
    subs 中每个元素有 .start 和 .end 属性（秒数）。"""

def find_nearest_silence_gap(
    cut_ts: float,
    gaps: list[tuple[float, float]],
    tolerance: float = 3.0
) -> tuple[float, bool]:
    """在 cut_ts 附近 ±tolerance 范围内找到最近的字幕静默区间中点。
    返回 (adjusted_ts, is_adjusted)"""
```

每个函数都要有完整的类型标注和 docstring。

### 2. utils/ffmpeg_check.py

```python
import subprocess
import shutil

def check_ffmpeg() -> tuple[bool, str]:
    """检查 ffmpeg 是否已安装且在 PATH 中。
    返回 (is_available, message)。
    如果可用，message 包含版本号。
    如果不可用，message 包含安装指引链接。"""

def get_video_info(video_path: str) -> dict:
    """用 ffprobe 获取视频基本信息。
    返回 {'duration': float, 'fps': float, 'width': int, 'height': int, 'total_frames': int}
    如果 ffprobe 失败，抛出 FFmpegError 异常。"""
```

### 3. tests/test_time_utils.py

为 time_utils.py 的每个函数编写 pytest 单元测试：
- 测试 seconds_to_timestamp / timestamp_to_seconds 的各种格式
- 测试 parse_manual_timestamps（HH:MM:SS 和 MM:SS）
- 测试 vtt_timestamp_to_seconds（点号分隔毫秒）
- 测试 check_subtitle_conflict
- 测试 find_nearest_silence_gap

## 自检清单
- [ ] time_utils.py 所有函数可正常运行
- [ ] parse_manual_timestamps 能正确解析 HH:MM:SS 和 MM:SS 格式
- [ ] vtt_timestamp_to_seconds 能正确处理点号分隔的毫秒
- [ ] ffmpeg_check.py 能检测 ffmpeg 是否可用
- [ ] get_video_info 能获取视频时长、fps 等信息
- [ ] 测试文件使用 pytest 格式，可独立运行
```

---

## Step 3：字幕解析模块

```
你是 Python 后端开发工程师。请为"短剧总集篇自动分集工具"编写字幕解析模块。

## 项目路径
工作目录：drama-splitter/

## 依赖关系
依赖 Step 1 的 models.py，Step 2 的 time_utils.py。

## 已安装依赖
pysrt（解析 SRT）、webvtt-py（解析 VTT）

## 背景
用户从 YouTube 下载字幕文件，有两种格式：
1. .txt 文件：内容为标准 SRT 格式（序号 + 时间戳 + 文本），但后缀不是 .srt
2. .vtt 文件：WebVTT 格式，包含 YouTube 特有的逐词时间戳标记

### SRT (.txt) 示例
```
1
00:00:12,160 --> 00:00:18,640
Tonight, the moon goddess herself shall

2
00:00:15,200 --> 00:00:20,400
bear witness. The silver moon bloodline
```

### VTT (.vtt) 示例
```
WEBVTT
Kind: captions
Language: en

00:00:12.160 --> 00:00:15.190 align:start position:0%
Tonight,<00:00:13.360><c> the</c><00:00:13.599><c> moon</c><00:00:13.920><c> goddess</c>...

00:00:15.190 --> 00:00:15.200 align:start position:0%
Tonight, the moon goddess herself shall
```

VTT 特点：
- 每条字幕有两行：第一行带逐词标记，第二行是纯文本
- 逐词标记格式：<00:00:13.360><c> the</c>
- 有 align:start position:0% 等定位属性

## 重要提醒
- SRT 时间戳解析：pysrt 的 SubRipTime 对象包含 hours/minutes/seconds/milliseconds 属性。
  **不要直接用 .seconds**（只取秒部分，丢失小时和分钟），应使用 **.ordinal**（返回总毫秒数）再除以 1000。

## 请完成 modules/subtitle_parser.py

实现以下类：

```python
class SubtitleParser:
    """统一字幕解析器（SRT + VTT）"""

    @staticmethod
    def parse(subtitle_path: str) -> list[SubtitleItem]:
        """自动识别格式并解析字幕。
        .vtt → 调用 _parse_vtt
        .txt → 调用 _parse_srt
        其他后缀 → 抛出 ValueError"""

    @staticmethod
    def _parse_srt(subtitle_path: str) -> list[SubtitleItem]:
        """解析 .txt 文件（内容为 SRT 格式）。
        用 pysrt.from_string() 读取文件内容后解析。
        **时间戳转换用 s.start.ordinal / 1000**（不用 .seconds）。
        将 pysrt.SubRipItem 转为 SubtitleItem（从 models.py 导入）。"""

    @staticmethod
    def _parse_vtt(subtitle_path: str) -> list[SubtitleItem]:
        """解析 YouTube VTT 字幕文件。
        1. 用 webvtt.read() 读取
        2. 清理逐词标记：用正则移除 <HH:MM:SS.mmm> 和 </c><c> 标签
        3. 跳过空文本行
        4. 去重：YouTube VTT 中同一句话会出现两次（带标记行+纯文本行），
           两条字幕 start 相差 < 0.1s 且 text 相同则视为重复，只保留第一条
        5. 转为 SubtitleItem"""

    @staticmethod
    def extract_silence_gaps(subs: list[SubtitleItem]) -> list[tuple[float, float]]:
        """提取字幕中相邻两条之间的静默区间 (gap_start, gap_end)。
        间隙 > 0.1s 才算静默区间。"""

    @staticmethod
    def extract_word_timestamps(subtitle_path: str) -> list[WordTimestamp]:
        """从 VTT 逐词标记中提取每个词的精确时间戳。
        仅 VTT 格式可用，SRT 格式返回空列表。
        匹配 <HH:MM:SS.mmm> 后紧跟的文本。"""
```

### tests/test_subtitle_parser.py
- 构造一个小的 SRT 格式字符串，保存为 .txt 文件，测试解析
- 构造一个小的 VTT 格式字符串（包含逐词标记），保存为 .vtt 文件，测试解析
- 测试 extract_silence_gaps
- 测试 extract_word_timestamps
- 测试 SRT 时间戳解析正确性（验证小时和分钟不被丢失）

## 自检清单
- [ ] SRT 解析：能正确解析 .txt 文件中的 SRT 内容，时间戳精确到毫秒
- [ ] SRT 时间戳使用 .ordinal / 1000 转换（非 .seconds）
- [ ] VTT 解析：能正确清理逐词标记，去重，提取纯文本
- [ ] extract_silence_gaps：能正确计算相邻字幕间的间隙
- [ ] extract_word_timestamps：能从 VTT 逐词标记中提取词级时间戳
- [ ] 格式自动识别：根据文件后缀自动选择解析方式
- [ ] 测试覆盖 SRT 和 VTT 两种格式
```

---

## Step 4：闪白检测模块

```
你是 Python 计算机视觉开发工程师。请为"短剧总集篇自动分集工具"编写视频闪白检测模块。

## 项目路径
工作目录：drama-splitter/

## 依赖关系
依赖 Step 1 的 config.py、models.py、exceptions.py，Step 2 的 ffmpeg_check.py。

## 背景
短剧总集篇的集与集之间有"闪白"过渡特效：画面突然变白/变亮，持续零点几秒。每集1-3分钟，闪白每集之间都有。集内一般没有类似闪白。

## 算法要求
1. 用 OpenCV 打开视频，采样帧计算平均亮度（目标采样率 15fps）
2. 检测亮度骤升（> WHITE_THRESHOLD=220 且 delta > SPIKE_THRESHOLD=80）
3. 合并邻近候选点（间距 < MERGE_DISTANCE=2s 的合并为一个）
4. 过滤误检（相邻分割点间距 < MIN_EPISODE_DURATION=30s 的丢弃）

## 请完成 modules/flash_detector.py

```python
import logging
logger = logging.getLogger(__name__)

class FlashDetector:
    """视频闪白检测器"""

    def __init__(self, config: dict | None = None):
        """初始化，可覆盖默认参数。config 为 None 时使用 AppConfig 默认值。"""

    def detect(self, video_path: str) -> list[float]:
        """主入口：检测闪白时间戳，返回候选分割点列表（秒数）。
        添加日志：开始检测、采样帧数、检出候选点数量、检测完成。"""

    def _build_brightness_series(self, video_path: str) -> list[tuple[float, float]]:
        """采样帧并计算亮度序列。返回 [(timestamp_sec, brightness_value), ...]"""

    def _detect_candidates(self, brightness_series: list[tuple[float, float]]) -> list[float]:
        """从亮度序列中检测闪白候选点"""

    @staticmethod
    def _merge_nearby(points: list[float], merge_distance: float) -> list[float]:
        """合并距离 < merge_distance 的邻近点"""

    @staticmethod
    def _filter_by_min_duration(points: list[float], min_duration: float) -> list[float]:
        """过滤掉间距 < min_duration 的点（误检）"""
```

## 性能要求
- 1 小时视频（30fps ≈ 108000 帧）应在 5 分钟内完成
- 采样率 15fps 时只需处理约 54000 帧
- 逐帧读取时，非采样帧直接 cap.read() 跳过即可，不需要转灰度计算

## 自检清单
- [ ] detect() 方法返回按时间排序的浮点数列表
- [ ] 采样帧率正确（根据视频 fps 计算采样间隔）
- [ ] 合并和过滤逻辑正确
- [ ] config 参数可覆盖默认值
- [ ] 添加日志记录（分析进度、检测到的候选点数量等）
- [ ] 视频文件无法打开时抛出 FileFormatError
```

---

## Step 5：音频静音检测模块

```
你是 Python 音频处理开发工程师。请为"短剧总集篇自动分集工具"编写音频静音检测模块。

## 项目路径
工作目录：drama-splitter/

## 依赖关系
依赖 Step 1 的 config.py、models.py、exceptions.py。

## 背景
短剧总集篇的集与集之间有短暂停顿，在音频上表现为静音/能量骤降，持续 0.5-3 秒。音频是用户从 YouTube 单独下载的 mp3 文件，与视频时间轴对齐。

## 算法要求
1. 用 librosa 加载 mp3（sr=None 保持原始采样率），计算短时 RMS 能量
2. 检测 RMS < SILENCE_THRESHOLD(0.01) 的连续静音帧
3. 合并连续静音帧为静音区间，过滤：
   - 持续 > MIN_SILENCE_DURATION(0.3s)：有效
   - 持续 < MAX_SILENCE_DURATION(5.0s)：有效（排除片头片尾长静音）
4. 取静音区间中点作为分割候选
5. 过滤误检：相邻候选点间距 < MIN_EPISODE_DURATION(30s) 的丢弃
6. 注意：处理音频末尾的静音区间（循环结束后检查 in_silence 状态）

## 请完成 modules/silence_detector.py

```python
import logging
logger = logging.getLogger(__name__)

class SilenceDetector:
    """音频静音检测器"""

    def __init__(self, config: dict | None = None):
        """初始化，可覆盖默认参数"""

    def detect(self, audio_path: str) -> list[float]:
        """主入口：检测静音区间中点，返回候选分割点列表（秒数）。
        添加日志：开始检测、音频时长、检出静音区间数量、检测完成。"""

    def _compute_rms_series(self, audio_path: str) -> tuple[list[float], list[float]]:
        """计算音频 RMS 能量序列。
        返回 (times, rms_values)"""

    def _detect_silence_regions(self, times: list[float], rms: list[float]) -> list[tuple[float, float]]:
        """从 RMS 序列中检测静音区间。
        注意处理音频末尾的静音区间。
        返回 [(silence_start, silence_end), ...]"""

    @staticmethod
    def _merge_nearby(points: list[float], merge_distance: float) -> list[float]:
        """合并距离 < merge_distance 的邻近点"""

    @staticmethod
    def _filter_by_min_duration(points: list[float], min_duration: float) -> list[float]:
        """过滤掉间距 < min_duration 的点"""
```

## 性能要求
- 1 小时音频应在 2 分钟内完成
- librosa 加载时使用 sr=None 保持原始采样率

## 自检清单
- [ ] detect() 方法返回按时间排序的浮点数列表
- [ ] 静音区间检测正确：连续静音帧被正确合并
- [ ] 音频末尾的静音区间被正确处理
- [ ] 中间过滤条件正确：排除过短/过长的静音区间
- [ ] config 参数可覆盖默认值
- [ ] 添加日志记录
- [ ] 音频文件无法加载时抛出 FileFormatError
```

---

## Step 6：交叉验证模块

```
你是 Python 后端开发工程师。请为"短剧总集篇自动分集工具"编写三重交叉验证模块。

## 项目路径
工作目录：drama-splitter/

## 依赖关系
依赖 Step 1 的 models.py、config.py，Step 2 的 time_utils.py。

## 背景
系统有三种信号源检测集间分割点：
1. flash_points：闪白检测出的时间戳列表
2. silence_points：静音检测出的时间戳列表
3. subtitle_gaps：字幕静默区间 [(gap_start, gap_end), ...]

三种信号不一定同时存在。需要交叉验证，融合为带置信度的分割点。

## 置信度规则（重要！）
- 2 个及以上信号在 ±MATCH_WINDOW 内吻合 → HIGH
- 仅 1 个信号 → LOW
- **不存在 MEDIUM 级别**
- 任意两个信号的组合均等同对待，不区分信号类型权重

## 时间戳微调策略
- 有字幕时，将切割点微调到最近的字幕静默区间中点
- 无字幕时，使用原始时间戳

## 接口要求（重要！）
- validate() 返回 list[MergedPoint]（不返回 EpisodeSegment）
- validate() 参数包含 video_duration
- 分集清单由 build_episode_list() 单独构建

## 请完成 modules/cross_validator.py

```python
import logging
logger = logging.getLogger(__name__)

class CrossValidator:
    """三重交叉验证引擎"""

    def __init__(self, match_window: float = 3.0, min_episode_duration: float = 30.0):
        """初始化"""

    def validate(
        self,
        flash_points: list[float],
        silence_points: list[float],
        subtitle_gaps: list[tuple[float, float]] | None,
        video_duration: float
    ) -> list[MergedPoint]:
        """
        主入口：融合三种信号，返回分割点列表（MergedPoint）。

        流程：
        1. 收集所有候选点（来自三种信号，使用有序列表而非 set）
        2. 对每个候选点，检查哪些信号在 ±match_window 内吻合
        3. 根据吻合数量评定置信度：2+ → HIGH，1 → LOW
        4. 有字幕时，微调到最近的静默区间中点
        5. 合并 match_window 内的邻近点（保留置信度最高的）
        6. 过滤最小集时长
        """

    def _check_flash_match(self, ts: float, flash_points: list[float]) -> bool:
        """检查 ts 附近是否有闪白信号"""

    def _check_silence_match(self, ts: float, silence_points: list[float]) -> bool:
        """检查 ts 附近是否有静音信号"""

    def _check_subtitle_match(self, ts: float, subtitle_gaps: list[tuple[float, float]] | None) -> bool:
        """检查 ts 附近是否有字幕间隙"""

    def _adjust_to_gap(self, ts: float, subtitle_gaps: list[tuple[float, float]] | None) -> float:
        """将时间戳微调到最近的字幕静默区间中点"""

    @staticmethod
    def _deduplicate_merged_points(points: list[MergedPoint], window: float) -> list[MergedPoint]:
        """合并 window 内的邻近 MergedPoint，保留置信度最高的"""

    @staticmethod
    def _filter_by_min_duration(points: list[MergedPoint], min_duration: float) -> list[MergedPoint]:
        """过滤掉间距 < min_duration 的点"""

    def build_episode_list(
        self, merged_points: list[MergedPoint], video_duration: float,
        subs: list | None = None
    ) -> list[EpisodeSegment]:
        """构建分集清单。检查每个切割点的台词冲突。
        - 第一集从 0 开始，最后一集到 video_duration
        - merged_points 为空时返回单集（整片为第一集，confidence=LOW）
        """
```

### tests/test_cross_validator.py
- 构造模拟的 flash_points / silence_points / subtitle_gaps 数据
- 测试三重验证：三个信号吻合 → HIGH
- 测试双重验证：任意两个信号吻合 → HIGH
- 测试单信号：LOW
- 测试降级：某个信号为空列表时自动降级
- 测试分集清单生成正确性
- 测试空分割点列表返回单集

## 自检清单
- [ ] validate() 返回 list[MergedPoint]（不是 EpisodeSegment）
- [ ] validate() 参数包含 video_duration
- [ ] 三个信号源全部为空列表时，返回空列表
- [ ] 任意一个信号源为空时，自动降级为双重/单信号验证
- [ ] 置信度评定正确：2+ 信号 = HIGH，1 信号 = LOW（无 MEDIUM）
- [ ] 时间戳微调正确：有字幕时调整到静默区间中点
- [ ] 分集清单正确：第一集从 0 开始，最后一集到 video_duration
- [ ] 空分割点列表返回单集
- [ ] 台词冲突检测正确
```

---

## Step 7：视频裁剪模块

```
你是 Python 后端开发工程师。请为"短剧总集篇自动分集工具"编写视频裁剪模块。

## 项目路径
工作目录：drama-splitter/

## 依赖关系
依赖 Step 1 的 models.py、exceptions.py，Step 2 的 ffmpeg_check.py。

## 裁剪要求
- 使用 ffmpeg -c copy 无损流拷贝模式，不重编码
- 命令模板：ffmpeg -y -i input.mp4 -ss {start} -to {end} -c copy -avoid_negative_ts make_zero output.mp4
- 输出文件命名：{剧名}_S01E{集数:02d}.mp4
- 输出目录：outputs/{剧名}/

## 接口要求（重要！）
- cut_single() 返回 CutResult（不是 bool）
- 包含 cut_batch_concurrent() 并发裁剪方法

## 请完成 modules/cutter.py

```python
import logging
logger = logging.getLogger(__name__)

class VideoCutter:
    """视频裁剪引擎"""

    def __init__(self, max_workers: int | None = None):
        """初始化。max_workers 默认为 CPU核心数 - 1"""

    def cut_single(self, video_path: str, segment: EpisodeSegment, output_path: str) -> CutResult:
        """裁剪单集视频。使用 subprocess.run 调用 ffmpeg。
        捕获 stdout/stderr，返回 CutResult。
        失败时 CutResult.success=False 且包含错误信息。"""

    def cut_batch(
        self,
        video_path: str,
        segments: list[EpisodeSegment],
        output_dir: str,
        drama_name: str,
        progress_callback: Callable[[int, int], None] | None = None
    ) -> list[CutResult]:
        """顺序批量裁剪。
        - 创建 output_dir（如果不存在）
        - 逐集裁剪，每集完成后调用 progress_callback(current, total)
        - 生成输出文件路径：{output_dir}/{drama_name}_S01E{episode_num:02d}.mp4
        - 返回所有 CutResult"""

    def cut_batch_concurrent(
        self,
        video_path: str,
        segments: list[EpisodeSegment],
        output_dir: str,
        drama_name: str,
        progress_callback: Callable[[int, int], None] | None = None
    ) -> list[CutResult]:
        """并发批量裁剪。使用 ThreadPoolExecutor 并发执行。
        - 并发数 = max_workers
        - 每个任务完成后调用 progress_callback
        - 使用 as_completed 收集结果
        - 结果按 episode_num 排序后返回
        - 单集失败不影响其他集"""
```

### tests/test_cutter.py
- 测试输出文件路径生成逻辑
- 测试 CutResult 模型
- （ffmpeg 裁剪测试需要真实视频文件，标记为 @pytest.mark.skip）

## 自检清单
- [ ] cut_single 返回 CutResult（不是 bool）
- [ ] cut_single 正确调用 ffmpeg，-c copy 模式
- [ ] 输出文件路径命名正确
- [ ] cut_batch 顺序执行，进度回调正确
- [ ] cut_batch_concurrent 并发执行，结果按集数排序
- [ ] ffmpeg 命令失败时，CutResult.success=False 且包含错误信息
- [ ] 磁盘空间不足等异常被正确捕获
- [ ] 单集失败不影响其他集
```

---

## Step 8：文件管理模块

```
你是 Python 后端开发工程师。请为"短剧总集篇自动分集工具"编写文件管理模块。

## 项目路径
工作目录：drama-splitter/

## 依赖关系
依赖 Step 1 的 config.py、exceptions.py。

## 功能要求
- 管理上传文件的保存（mp4、mp3、txt、vtt）
- 管理输出目录的创建
- 管理任务与文件的关联关系
- 剧名特殊字符清理（安全防护）

## 请完成 modules/file_manager.py

```python
import logging
logger = logging.getLogger(__name__)

class FileManager:
    """文件管理器"""

    def __init__(self, upload_dir: str = "uploads", output_dir: str = "outputs"):
        """初始化，创建目录"""

    def save_upload(self, file_path: str, task_id: str) -> str:
        """将上传的文件保存到 {upload_dir}/{task_id}/ 目录下。
        返回保存后的绝对路径。
        保留原始文件名，若重名则添加后缀。"""

    def create_output_dir(self, drama_name: str) -> str:
        """为剧集创建输出目录 {output_dir}/{drama_name}/。
        清理剧名中的特殊字符：替换 / \\ : * ? " < > | 为 _
        返回输出目录路径。"""

    def get_output_path(self, output_dir: str, episode_num: int, drama_name: str) -> str:
        """生成输出文件路径：{output_dir}/{drama_name}_S01E{episode_num:02d}.mp4"""

    def cleanup_task_files(self, task_id: str) -> None:
        """清理指定任务的上传临时文件"""

    def get_task_files(self, task_id: str) -> dict:
        """获取指定任务的所有上传文件信息。
        返回 {'video': path | None, 'audio': path | None, 'subtitle': path | None}"""

    def check_disk_space(self, output_dir: str, estimated_size: int) -> bool:
        """检查磁盘可用空间是否充足（P1 优化）。
        estimated_size 为预估输出总大小（字节）。"""
```

### tests/test_file_manager.py
- 测试文件保存到正确的 task_id 子目录
- 测试输出目录路径正确
- 测试剧名特殊字符清理（/ \ : * ? " < > | → _）
- 测试文件名冲突处理

## 自检清单
- [ ] 文件保存到正确的 task_id 子目录
- [ ] 输出目录路径正确
- [ ] 剧名特殊字符清理（替换 / \ : * ? " < > | 为 _）
- [ ] 文件名冲突处理（重名添加后缀）
- [ ] 日志记录文件操作
```

---

## Step 9：任务队列模块

```
你是 Python 后端开发工程师。请为"短剧总集篇自动分集工具"编写任务队列模块。

## 项目路径
工作目录：drama-splitter/

## 依赖关系
依赖 Step 1 的 models.py、config.py、exceptions.py，
Step 4 的 FlashDetector，Step 5 的 SilenceDetector，
Step 6 的 CrossValidator，Step 7 的 VideoCutter。

## 功能要求
- 管理任务状态（创建、查询、更新）
- 协调分析任务和裁剪任务的执行
- 分析任务：串行执行（CPU 密集），但同一视频的闪白+静音检测可并行
- 裁剪任务：并发执行（IO 密集）

## 异步/同步混用说明
- FlashDetector.detect() 和 SilenceDetector.detect() 是同步阻塞函数
- TaskQueue.run_analysis() 是 async 方法
- 使用 asyncio.gather + loop.run_in_executor 将同步函数调度到线程池执行：

```python
loop = asyncio.get_event_loop()
flash_task = loop.run_in_executor(None, flash_detector.detect, video_path)
silence_task = loop.run_in_executor(None, silence_detector.detect, audio_path)
flash_points, silence_points = await asyncio.gather(flash_task, silence_task)
```

## 请完成 modules/task_queue.py

```python
import logging
import asyncio
import uuid
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger(__name__)

class TaskQueue:
    """任务队列与并发调度"""

    def __init__(self, max_workers: int | None = None):
        """初始化。max_workers 默认为 CPU核心数 - 1。
        创建 ThreadPoolExecutor 和 tasks dict。"""

    def create_task(self, video_path: str | None = None, audio_path: str | None = None,
                    subtitle_path: str | None = None, drama_name: str | None = None) -> str:
        """创建新任务，返回 task_id (UUID)。
        初始状态为 'pending'。"""

    def get_task(self, task_id: str) -> TaskStatus | None:
        """获取任务状态"""

    def list_tasks(self) -> list[TaskStatus]:
        """列出所有任务"""

    async def run_analysis(self, task_id: str) -> TaskStatus:
        """执行分析任务。
        1. 更新状态为 'analyzing'
        2. 并行执行闪白检测和静音检测（asyncio.gather + run_in_executor）
        3. 解析字幕（如果有）
        4. 交叉验证（CrossValidator.validate → list[MergedPoint]）
        5. 构建分集清单（CrossValidator.build_episode_list → list[EpisodeSegment]）
        6. 更新状态为 'awaiting_confirm'
        7. 返回更新后的 TaskStatus
        如果任何步骤失败，状态设为 'failed' 并记录错误信息。
        音频/字幕失败时降级处理，不直接失败。"""

    async def run_cut(self, task_id: str, episodes: list[EpisodeSegment]) -> list[CutResult]:
        """执行裁剪任务。
        1. 更新状态为 'cutting'
        2. 使用 VideoCutter.cut_batch_concurrent 并发裁剪
        3. 更新状态为 'completed'（全部成功）或 'failed'（有失败）
        4. 返回 CutResult 列表"""

    def update_task_episodes(self, task_id: str, episodes: list[EpisodeSegment]) -> None:
        """更新任务的分集清单（用户编辑后）"""

    def delete_task(self, task_id: str) -> None:
        """删除任务"""
```

## 自检清单
- [ ] 任务的完整生命周期：pending → analyzing → awaiting_confirm → cutting → completed
- [ ] 闪白检测和静音检测通过 asyncio.gather + run_in_executor 并行执行
- [ ] run_analysis 调用 CrossValidator.validate() 获取 MergedPoint 列表
- [ ] run_analysis 调用 build_episode_list() 获取 EpisodeSegment 列表
- [ ] 任务状态查询正确
- [ ] 异常处理：分析/裁剪失败时状态变为 failed
- [ ] 音频/字幕失败时降级处理
- [ ] 并发裁剪正确
- [ ] 日志记录任务状态变更
```

---

## Step 10：Gradio 前端界面

```
你是 Python 前端开发工程师。请为"短剧总集篇自动分集工具"编写 Gradio Web 界面。

## 项目路径
工作目录：drama-splitter/

## 依赖关系
依赖 Step 9 的 TaskQueue，Step 8 的 FileManager，
Step 3 的 SubtitleParser，Step 1 的所有数据模型。

## 界面要求

### Tab 1：上传与分析
- mp4 视频文件上传（必填）
- mp3 音频文件上传（可选）
- 字幕文件上传（可选，支持 txt 和 vtt）
- 剧名输入框
- "开始自动分析"按钮
- 分析进度展示区（闪白检测进度、静音检测进度、字幕解析状态）
- 人工输入模式：粘贴起始时间的文本框 + "提交"按钮
- 分析结果展示区

### Tab 2：确认与编辑
- 分集清单表格（Dataframe）：集数、开始时间、结束时间、时长、置信度、台词冲突
- 置信度用 emoji 标记：🟢 HIGH 🔴 LOW（注意：没有 🟡 MEDIUM）
- 台词冲突用 ❌ 标记
- 编辑功能：用户可直接修改表格中的时间值
- 编辑后自动重新检测台词冲突
- "确认并开始裁剪"按钮

### Tab 3：裁剪进度与输出
- 裁剪进度条
- 已完成文件列表
- "打开输出目录"按钮
- "下载"按钮

## 请完成 web/app.py

```python
import gradio as gr
from modules.task_queue import TaskQueue
from modules.file_manager import FileManager
# ... 其他导入

class DramaSplitterApp:
    """Gradio 应用主类"""

    def __init__(self):
        self.task_queue = TaskQueue()
        self.file_manager = FileManager()

    def create_ui(self) -> gr.Blocks:
        """创建 Gradio 界面"""

    def _create_upload_tab(self) -> gr.Column:
        """创建上传与分析 Tab"""

    def _create_confirm_tab(self) -> gr.Column:
        """创建确认与编辑 Tab"""

    def _create_output_tab(self) -> gr.Column:
        """创建裁剪进度与输出 Tab"""

    def handle_upload_and_analyze(self, video, audio, subtitle, drama_name):
        """处理上传并触发分析。
        - 保存上传文件
        - 创建任务
        - 调用 run_analysis（异步）
        - 返回分析结果表格"""

    def handle_manual_input(self, timestamps_text, drama_name):
        """处理人工输入。
        - 解析时间戳
        - 构建分集清单
        - 检查台词冲突
        - 返回分集表格"""

    def handle_confirm_and_cut(self, episodes_df, drama_name):
        """处理确认并触发裁剪。
        - 解析编辑后的表格
        - 调用 run_cut
        - 返回裁剪进度"""

    def handle_edit_episode(self, episodes_df):
        """编辑分割点后重新检测台词冲突。
        - 解析编辑后的时间值
        - 重新检测冲突
        - 返回更新后的表格"""

    def launch(self, **kwargs):
        """启动应用"""
        app = self.create_ui()
        app.launch(**kwargs)
```

## 自检清单
- [ ] 三个 Tab 页面正常显示
- [ ] 文件上传功能正常
- [ ] 分析结果正确展示在表格中
- [ ] 置信度 emoji 标记正确：🟢 HIGH 🔴 LOW（无 🟡 MEDIUM）
- [ ] 台词冲突标记正确（❌）
- [ ] 人工输入模式可正常工作
- [ ] 编辑分割点后自动重新检测冲突
- [ ] 裁剪进度可展示
- [ ] 界面中文提示
- [ ] 错误状态有友好提示
```

---

## Step 11：主入口与启动脚本

```
你是 Python 后端开发工程师。请为"短剧总集篇自动分集工具"编写主入口文件。

## 项目路径
工作目录：drama-splitter/

## 依赖关系
依赖 Step 10 的 DramaSplitterApp，Step 1 的 logger.py 和 config.py，
Step 2 的 ffmpeg_check.py。

## 请完成 main.py

```python
"""
短剧总集篇自动分集工具 - 启动入口
"""
import sys
import os
import webbrowser
import threading

# 将项目根目录加入 sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from utils.logger import setup_logging
from utils.ffmpeg_check import check_ffmpeg
from config import AppConfig

def check_environment() -> bool:
    """检查运行环境：
    1. Python 版本 >= 3.10
    2. ffmpeg 已安装
    3. 返回 True 表示环境OK，False 表示有问题
    环境问题时打印明确的中文提示。"""

def open_browser(url: str, delay: float = 1.5):
    """延迟打开浏览器"""

def main():
    """主入口：
    1. 初始化日志系统（setup_logging）
    2. 检查环境（check_environment）
    3. 加载配置（AppConfig.from_env）
    4. 创建必要目录
    5. 启动 Gradio 应用（server_name, server_port 从 config 读取）
    6. 自动打开浏览器"""

if __name__ == '__main__':
    main()
```

同时创建 README.md，包含：
- 项目简介
- 环境要求（Python 3.10+、ffmpeg）
- 安装步骤（pip install -r requirements.txt、安装 ffmpeg）
- 使用方法（python main.py）
- 使用流程说明（上传 → 分析 → 确认 → 裁剪）
- 配置参数说明（AppConfig 各参数及环境变量覆盖方式）
- 已知限制
- 后续优化方向

## 自检清单
- [ ] python main.py 能正常启动
- [ ] 日志系统在启动时初始化
- [ ] 环境检查：ffmpeg 未安装时给出明确中文提示
- [ ] 自动打开浏览器
- [ ] 配置从 AppConfig 加载
- [ ] README.md 内容完整
```

---

## Step 12：集成测试

```
你是 Python 测试工程师。请为"短剧总集篇自动分集工具"编写集成测试。

## 项目路径
工作目录：drama-splitter/

## 依赖关系
依赖 Step 1-11 的所有模块。

## 测试要求
由于没有真实的长视频文件，请编写以下测试：

### 1. tests/conftest.py
- 定义公共 fixture：tmp_dir、sample_srt_content、sample_vtt_content
- 定义 mock fixture：mock_video_path、mock_audio_path

### 2. tests/test_time_utils.py（补充完善）
- 测试 seconds_to_timestamp / timestamp_to_seconds 的各种格式
- 测试 parse_manual_timestamps
- 测试 vtt_timestamp_to_seconds
- 测试 check_subtitle_conflict（切割点在台词中间、不在台词中间、无字幕）
- 测试 find_nearest_silence_gap

### 3. tests/test_subtitle_parser.py（补充完善）
- 构造一个小的 SRT 格式字符串，保存为 .txt 文件，测试解析
- 构造一个小的 VTT 格式字符串（包含逐词标记），保存为 .vtt 文件，测试解析
- 测试 extract_silence_gaps
- 测试 extract_word_timestamps
- **测试 SRT 时间戳包含小时的情况**（验证不被丢失）

### 4. tests/test_cross_validator.py（补充完善）
- 构造模拟的 flash_points / silence_points / subtitle_gaps 数据
- 测试三重验证：三个信号吻合 → HIGH
- 测试双重验证：任意两个信号吻合 → HIGH（测试所有三种组合）
- 测试单信号：LOW（测试所有三种单信号情况）
- 测试降级：某个信号为空列表时自动降级
- 测试空分割点列表返回单集
- 测试分集清单生成正确性
- **测试不存在 MEDIUM 置信度**

### 5. tests/test_cutter.py（补充完善）
- 测试输出文件路径生成逻辑
- 测试 CutResult 模型
- （ffmpeg 裁剪测试需要真实视频文件，标记为 @pytest.mark.skip）

### 6. tests/test_file_manager.py（补充完善）
- 测试文件保存
- 测试输出目录创建
- 测试剧名特殊字符清理（/ \ : * ? " < > |）
- 测试文件名冲突处理

## 验收标准
- 所有测试可在无真实视频/音频文件的情况下运行
- 字幕解析测试使用构造的示例数据
- 交叉验证测试覆盖所有信号组合
- 运行 pytest tests/ -v 全部通过
- 运行 pytest tests/ --cov=modules --cov=utils 查看覆盖率

## 自检清单
- [ ] conftest.py 定义了公共 fixture
- [ ] 所有测试文件使用 pytest 格式
- [ ] 交叉验证测试覆盖所有信号组合（3种双重 + 3种单信号）
- [ ] 测试验证不存在 MEDIUM 置信度
- [ ] SRT 时间戳测试包含小时场景
- [ ] ffmpeg 相关测试标记为 skip
- [ ] pytest tests/ 全部通过
```

---

## Step 13：Bug 修复与优化

```
你是一个 Python 全栈工程师。请对"短剧总集篇自动分集工具"项目进行最终检查和优化。

## 项目路径
工作目录：drama-splitter/

## 请完成以下工作

### 1. 代码审查
- 检查所有模块的导入是否正确
- 检查数据模型的使用是否一致（models.py 中的类型 vs 各模块中的使用）
- 检查 config.py 的参数是否在各模块中正确引用
- 检查异常处理是否完善（自定义异常类是否被正确使用）
- 检查置信度规则是否统一（2+=HIGH，1=LOW，无 MEDIUM）
- 检查 CrossValidator.validate() 返回 MergedPoint（不是 EpisodeSegment）
- 检查 VideoCutter.cut_single() 返回 CutResult（不是 bool）

### 2. 运行测试
- 运行 pytest tests/ -v 确保所有测试通过
- 运行 pytest tests/ --cov=modules --cov=utils 查看覆盖率
- 修复发现的任何问题

### 3. 端到端验证
- 尝试 python main.py 启动应用
- 确认 Gradio 界面正常加载
- 确认三个 Tab 页面正常显示
- 修复发现的任何启动错误

### 4. 代码优化
- 确保日志系统在各模块中正确使用（logging.getLogger(__name__)）
- 确保 config.py 的 AppConfig 在所有模块中一致使用
- 添加类型标注的完整性检查（可用 mypy）
- 确保所有文件路径使用 os.path.join() 拼接（Windows 兼容）

### 5. 生成最终 README.md
更新 README.md，包含：
- 项目简介
- 环境要求（Python 3.10+、ffmpeg）
- 安装步骤
- 使用方法
- 配置参数说明
- 已知限制
- 后续优化方向

## 自检清单
- [ ] 所有测试通过（pytest tests/ -v）
- [ ] 测试覆盖率 >= 80%（核心模块）
- [ ] 应用可正常启动（python main.py）
- [ ] 无导入错误
- [ ] 日志系统正常工作
- [ ] 置信度规则统一（无 MEDIUM）
- [ ] 所有接口签名与 TDD 文档一致
- [ ] README.md 内容完整
```

---

## 使用建议

| 建议 | 说明 |
|------|------|
| **每步执行后检查** | 不要急着进入下一步，先对照自检清单验证产出 |
| **Step 0-1 是基础** | 环境、数据模型、配置、日志是后续所有模块的基础，务必确保正确 |
| **Step 2-3 是工具层** | 时间工具和字幕解析是纯逻辑，可以充分单元测试 |
| **Step 4-6 是核心** | 闪白检测、静音检测、交叉验证是算法核心，可以单独测试 |
| **Step 6 是关键** | 交叉验证模块是整个系统的灵魂，需要仔细验证各种信号组合 |
| **Step 7-9 是执行层** | 裁剪、文件管理、任务队列相对简单 |
| **Step 10-11 是界面** | Gradio 界面需要反复调试 |
| **Step 12-13 是收尾** | 测试和修复 |
| **调试技巧** | 如果某个模块有问题，可以单独给 AI 提供该模块的代码和相关错误信息，让它修复 |

---

## 常见问题与排错

| 问题 | 原因 | 解决方案 |
|------|------|---------|
| `ModuleNotFoundError: No module named 'cv2'` | opencv-python 未安装 | `pip install -r requirements.txt` |
| `FileNotFoundError: ffmpeg` | ffmpeg 未安装或不在 PATH | 安装 ffmpeg 并添加到系统 PATH |
| SRT 时间戳解析偏移 | 使用了 `.seconds` 而非 `.ordinal` | 改用 `s.start.ordinal / 1000` |
| 置信度出现 MEDIUM | 代码中残留 MEDIUM 逻辑 | 删除所有 MEDIUM 相关代码，统一为 HIGH/LOW |
| `RuntimeError: asyncio.run() cannot be called from a running event loop` | Gradio 事件回调中嵌套调用 async | 使用 `async def` 定义回调，或用 `asyncio.ensure_future` |
| Gradio 上传大文件失败 | 默认上传限制 | 在 AppConfig 中设置 `max_file_size = 2GB`，Gradio Blocks 设置 `max_file_size` |
| librosa 加载 mp3 报错 | 缺少音频解码器 | 安装 ffmpeg（librosa 依赖 ffmpeg 解码 mp3） |
| 裁剪后视频开头有黑屏 | 关键帧对齐偏差 | 使用 `-avoid_negative_ts make_zero` 参数 |
