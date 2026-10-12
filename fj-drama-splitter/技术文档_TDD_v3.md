# 短剧总集篇自动分集工具 — 技术文档（TDD）

> 版本：v3.0
> 日期：2026-08-10
> 状态：待评审
> 变更记录：
> - v3.0：统一置信度规则（2+信号=HIGH，1信号=LOW，取消MEDIUM）；修复SRT时间戳解析Bug；修复build_episode_list越界Bug；统一CrossValidator/VideoCutter/TaskQueue接口签名；新增日志架构、配置管理、异常处理策略、安全设计、测试策略、数据模型完整定义；修正前端技术栈为Gradio唯一框架
> - v2.0：新增音频静音检测算法、VTT 字幕解析、三重交叉验证策略

---

## 1. 系统架构

### 1.1 整体架构

```
┌──────────────────────────────────────────────────────┐
│                     浏览器 (前端)                       │
│  ┌───────────┐ ┌───────────────┐ ┌───────────────┐  │
│  │  文件上传  │ │  分割点确认    │ │  裁剪进度展示  │  │
│  │  页面      │ │  /编辑页面     │ │  页面          │  │
│  └───────────┘ └───────────────┘ └───────────────┘  │
└────────────────────────┬─────────────────────────────┘
                         │ HTTP (Gradio 内置服务)
┌────────────────────────┴─────────────────────────────┐
│                后端服务 (Gradio + Python)               │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ │
│  │ 文件管理   │ │ 分析引擎  │ │ 裁剪引擎  │ │ 交叉验证  │ │
│  │ 模块      │ │ 模块     │ │ 模块     │ │ 模块      │ │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘ │
│  ┌────────────────────────────────────────────────┐   │
│  │              任务队列 / 并发调度                  │   │
│  └────────────────────────────────────────────────┘   │
│  ┌──────────┐ ┌──────────┐ ┌──────────────────────┐  │
│  │ 日志系统   │ │ 配置管理  │ │ 异常处理             │  │
│  └──────────┘ └──────────┘ └──────────────────────┘  │
└────────────────────────┬─────────────────────────────┘
                         │
      ┌──────────┬───────┼───────┬──────────┐
      ▼          ▼       ▼       ▼          ▼
┌──────────┐┌────────┐┌────────┐┌────────┐┌──────────┐
│ OpenCV   ││librosa ││ pysrt  ││webvtt  ││ ffmpeg   │
│(帧分析)  ││(音频)  ││(SRT)   ││(VTT)  ││(裁剪)    │
└──────────┘└────────┘└────────┘└────────┘└──────────┘
```

### 1.2 技术选型

| 层级 | 选型 | 理由 |
|------|------|------|
| Web 框架 | **Gradio** | 快速出界面，Python 纯栈无需写 JS；内置文件上传、进度条、事件回调；适合本地工具型应用。Gradio 同时承担前端渲染和后端服务 |
| 视频帧分析 | **OpenCV (cv2)** | 成熟稳定，CPU 友好，亮度计算高效 |
| 音频分析 | **librosa** | 学术级音频分析库，短时能量/RMS 计算高效，CPU 友好 |
| 视频裁剪 | **ffmpeg + ffmpeg-python** | 行业标准，`-c copy` 无损流拷贝 |
| SRT 字幕解析 | **pysrt** | 解析 SRT 格式。用户字幕后缀为 .txt 但内容为标准 SRT 格式，用 `pysrt.from_string()` 解析 |
| VTT 字幕解析 | **webvtt-py** | 解析 WebVTT 格式。YouTube VTT 含逐词时间戳，需额外清理提取 |
| 并发 | **asyncio + ThreadPoolExecutor** | IO 密集（ffmpeg 裁剪）用线程池，分析阶段用异步调度 |
| 任务队列 | **内存队列（dict）** | 本地单机工具，无需引入 Redis/Celery。任务状态保存在内存中，不跨会话持久化 |
| 配置管理 | **dataclass + 环境变量** | 用 dataclass 封装配置参数，支持环境变量覆盖默认值 |
| 日志 | **logging（标准库）** | 按模块分 logger，输出到文件和控制台 |

> **前端方案备选说明**：Gradio 能最快出可用界面，满足"本地工具"定位。如果对交互体验有更高要求（如拖拽时间轴、帧预览），后续可切换为 Vue3 + Element Plus + FastAPI 架构，后端 API 无需大改。

---

## 2. 核心算法设计

### 2.1 闪白检测算法（视频信号）

#### 2.1.1 算法思路

集间闪白的视觉特征：**画面亮度在极短时间内（零点几秒）骤升至接近白色，然后迅速回落**。算法通过采样帧计算画面平均亮度，检测亮度曲线中的"尖峰"来定位闪白点。

#### 2.1.2 详细流程

```
输入：mp4 文件路径
输出：[闪白时间戳列表]（秒）

Step 1: 采样帧亮度序列
  - 用 OpenCV 打开视频，获取 fps、总帧数
  - 采样策略：逐帧读取，每隔 N 帧采样一次（N = max(1, int(fps / 15))）
    目标采样率 15 帧/秒，保证能捕捉到零点几秒的闪白
  - 对采样帧计算平均亮度：frame 转灰度 → cv2.mean() → 亮度值 (0-255)
  - 得到亮度序列 brightness = [(timestamp_sec, brightness_value), ...]

Step 2: 闪白候选点检测
  - 计算亮度序列的一阶差分 delta[i] = brightness[i+1] - brightness[i]
  - 闪白特征：delta 突然大幅正向跳变（亮度骤升）
  - 候选条件：
    a) brightness[i] > WHITE_THRESHOLD（如 220，接近白色）
    b) delta[i] > SPIKE_THRESHOLD（如 80，亮度骤升幅度）
    c) 闪白持续时间 < MAX_FLASH_DURATION（如 1.0 秒）
  - 满足以上条件的帧时间戳即为闪白候选点

Step 3: 候选点去重与合并
  - 若多个候选点间距 < MERGE_DISTANCE（如 2 秒），合并为同一个分割点
  - 取该簇中亮度最高的帧时间戳作为分割点

Step 4: 误检过滤
  - 每集时长约束：相邻分割点间距应在 1-3 分钟范围（可配置）
    若间距 < MIN_EPISODE_DURATION（如 30 秒），大概率是误检，丢弃
```

#### 2.1.3 关键参数（可配置）

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `WHITE_THRESHOLD` | 220 | 亮度阈值，高于此值视为"接近白色" |
| `SPIKE_THRESHOLD` | 80 | 亮度骤升幅度阈值 |
| `MAX_FLASH_DURATION` | 1.0s | 闪白最大持续时长 |
| `MERGE_DISTANCE` | 2.0s | 候选点合并距离 |
| `MIN_EPISODE_DURATION` | 30s | 最小集时长，小于此值的分割点视为误检 |
| `TARGET_SAMPLE_FPS` | 15 | 采样目标帧率 |

#### 2.1.4 伪代码

```python
def detect_flash_boundaries(video_path: str, config: Config) -> list[float]:
    cap = cv2.VideoCapture(video_path)
    fps = cap.get(cv2.CAP_PROP_FPS)
    total_frames = cap.get(cv2.CAP_PROP_FRAME_COUNT)
    sample_interval = max(1, int(fps / config.target_sample_fps))

    brightness_series = []
    frame_idx = 0
    while True:
        ret, frame = cap.read()
        if not ret:
            break
        if frame_idx % sample_interval == 0:
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            mean_brightness = cv2.mean(gray)[0]
            timestamp = frame_idx / fps
            brightness_series.append((timestamp, mean_brightness))
        frame_idx += 1
    cap.release()

    # 检测闪白候选点
    candidates = []
    for i in range(1, len(brightness_series) - 1):
        ts, bright = brightness_series[i]
        prev_bright = brightness_series[i - 1][1]
        delta = bright - prev_bright
        if bright > config.white_threshold and delta > config.spike_threshold:
            candidates.append(ts)

    # 合并邻近候选点
    merged = merge_nearby(candidates, config.merge_distance)

    # 误检过滤：最小集时长约束
    filtered = filter_by_min_duration(merged, config.min_episode_duration)

    return filtered
```

---

### 2.2 音频静音检测算法（音频信号）

#### 2.2.1 算法思路

集间停顿在音频上的特征：**短时能量骤降至接近静音，持续 0.5-3 秒后恢复**。相比画面闪白，音频静音信号**更稳定、误检率更低**——集内场景切换通常不会完全静音，而集间停顿几乎一定伴随静音。

#### 2.2.2 详细流程

```
输入：mp3 文件路径
输出：[静音区间中点时间戳列表]（秒）

Step 1: 加载音频
  - 用 librosa 加载 mp3，保持原始采样率 sr=None
  - 设定帧长 frame_length（如 2048），步长 hop_length（如 512）

Step 2: 计算短时能量曲线
  - rms = librosa.feature.rms(y=y, frame_length=2048, hop_length=512)[0]
  - 将帧索引转换为秒数：time = frame_idx * hop_length / sr
  - 得到能量序列 energy = [(timestamp_sec, rms_value), ...]

Step 3: 静音区间检测
  - 静音判定：rms < SILENCE_THRESHOLD（如 0.01，归一化后）
  - 将连续的静音帧合并为静音区间
  - 过滤条件：
    a) 静音持续时间 > MIN_SILENCE_DURATION（如 0.3s），排除对话中的正常停顿
    b) 静音持续时间 < MAX_SILENCE_DURATION（如 5.0s），排除片头/片尾长静音
  - 满足条件的静音区间即为分割候选

Step 4: 静音区间中点提取
  - 对每个有效静音区间，取中点作为分割候选时间戳
  - mid = (silence_start + silence_end) / 2

Step 5: 误检过滤
  - 相邻候选点间距 < MIN_EPISODE_DURATION（如 30s）的丢弃
```

#### 2.2.3 关键参数（可配置）

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `SILENCE_THRESHOLD` | 0.01 | RMS 能量阈值，低于此值视为静音（归一化后） |
| `MIN_SILENCE_DURATION` | 0.3s | 最小静音持续时长，排除对话中的正常停顿 |
| `MAX_SILENCE_DURATION` | 5.0s | 最大静音持续时长，排除片头/片尾长静音 |
| `FRAME_LENGTH` | 2048 | 短时能量计算帧长 |
| `HOP_LENGTH` | 512 | 短时能量计算步长 |

#### 2.2.4 伪代码

```python
import librosa
import numpy as np

def detect_silence_boundaries(audio_path: str, config: Config) -> list[float]:
    y, sr = librosa.load(audio_path, sr=None)

    # 计算短时 RMS 能量
    rms = librosa.feature.rms(
        y=y, frame_length=config.frame_length, hop_length=config.hop_length
    )[0]

    # 帧索引 → 时间戳
    times = librosa.frames_to_time(np.arange(len(rms)), sr=sr, hop_length=config.hop_length)

    # 静音帧检测
    is_silent = rms < config.silence_threshold

    # 将连续静音帧合并为静音区间
    silence_regions = []
    in_silence = False
    start = 0.0
    for i, silent in enumerate(is_silent):
        if silent and not in_silence:
            start = times[i]
            in_silence = True
        elif not silent and in_silence:
            end = times[i]
            duration = end - start
            if config.min_silence_duration <= duration <= config.max_silence_duration:
                silence_regions.append((start, end))
            in_silence = False

    # 处理音频末尾的静音
    if in_silence:
        end = times[-1]
        duration = end - start
        if config.min_silence_duration <= duration <= config.max_silence_duration:
            silence_regions.append((start, end))

    # 提取静音区间中点作为分割候选
    midpoints = [(s + e) / 2 for s, e in silence_regions]

    # 误检过滤：最小集时长约束
    filtered = filter_by_min_duration(midpoints, config.min_episode_duration)

    return filtered
```

---

### 2.3 字幕解析

#### 2.3.1 支持格式

| 格式 | 文件后缀 | 解析库 | 说明 |
|------|----------|--------|------|
| SRT | `.txt` | pysrt | 内容为标准 SRT 格式，用 `pysrt.from_string()` 解析 |
| WebVTT | `.vtt` | webvtt-py | YouTube 下载的 VTT，含逐词时间戳和定位属性，需额外清理 |

#### 2.3.2 SRT 格式解析（.txt 文件）

用户字幕文件后缀为 `.txt`，但内容是标准 SRT 格式。

> **注意**：pysrt 的 `SubRipTime` 对象包含 `hours`、`minutes`、`seconds`、`milliseconds` 属性。直接使用 `.seconds` 只会取到秒部分，会丢失小时和分钟。正确做法是使用 `.ordinal` 属性（返回总毫秒数）再除以 1000。

```python
import pysrt

def parse_srt_subtitle(subtitle_path: str) -> list[SubtitleItem]:
    """解析 txt 字幕文件（内容为 SRT 格式）"""
    with open(subtitle_path, 'r', encoding='utf-8') as f:
        content = f.read()
    subs = pysrt.from_string(content)
    return [
        SubtitleItem(
            index=s.index,
            start=s.start.ordinal / 1000,   # 毫秒 → 秒
            end=s.end.ordinal / 1000,        # 毫秒 → 秒
            text=s.text
        )
        for s in subs
    ]
```

#### 2.3.3 VTT 格式解析（.vtt 文件）

YouTube 下载的 VTT 格式特点：

```
WEBVTT
Kind: captions
Language: en

00:00:12.160 --> 00:00:15.190 align:start position:0%
Tonight,<00:00:13.360><c> the</c><00:00:13.599><c> moon</c><00:00:13.920><c> goddess</c>...

00:00:15.190 --> 00:00:15.200 align:start position:0%
Tonight, the moon goddess herself shall
```

特征：
- 有 `WEBVTT` 头部 + `Kind` / `Language` 元信息
- 带有 `align:start position:0%` 等定位属性
- 逐词时间戳 `<00:00:13.360><c> the</c>`（YouTube karaoke 风格标记）
- 每条字幕有两行：第一行带逐词标记，第二行是纯文本

解析策略：

```python
import re
import webvtt

def parse_vtt_subtitle(subtitle_path: str) -> list[SubtitleItem]:
    """
    解析 YouTube VTT 字幕文件。
    使用 webvtt-py 解析，清理逐词标记和定位属性。
    """
    subs = webvtt.read(subtitle_path)
    result = []
    for sub in subs:
        # 清理文本：移除逐词时间戳标签 <00:00:13.360><c>...</c>
        clean_text = re.sub(r'<\d{2}:\d{2}:\d{2}\.\d{3}>', '', sub.text)
        clean_text = re.sub(r'</?c>', '', clean_text)
        clean_text = clean_text.strip()

        # 跳过空文本（纯标记行）
        if not clean_text:
            continue

        # 解析时间戳
        start_sec = vtt_timestamp_to_seconds(sub.start)
        end_sec = vtt_timestamp_to_seconds(sub.end)

        # 去重：YouTube VTT 中同一句话会出现两次（带标记行 + 纯文本行）
        if result and abs(result[-1].start - start_sec) < 0.1 and result[-1].text == clean_text:
            continue

        result.append(SubtitleItem(
            index=len(result) + 1,
            start=start_sec,
            end=end_sec,
            text=clean_text
        ))
    return result

def vtt_timestamp_to_seconds(ts: str) -> float:
    """将 VTT 时间戳 '00:01:23.456' 转为秒数"""
    parts = ts.split(':')
    h, m = int(parts[0]), int(parts[1])
    s = float(parts[2])
    return h * 3600 + m * 60 + s
```

#### 2.3.4 统一字幕接口

```python
def parse_subtitle(subtitle_path: str) -> list[SubtitleItem]:
    """统一字幕解析入口，自动识别格式"""
    if subtitle_path.endswith('.vtt'):
        return parse_vtt_subtitle(subtitle_path)
    elif subtitle_path.endswith('.txt'):
        return parse_srt_subtitle(subtitle_path)
    else:
        raise ValueError(f"不支持的字幕格式: {subtitle_path}")
```

#### 2.3.5 VTT 逐词时间戳的额外价值

YouTube VTT 中的逐词时间戳 `<00:00:13.360><c> the</c>` 可以提供比整句更精确的时间信息：

```python
def extract_word_timestamps(subtitle_path: str) -> list[WordTimestamp]:
    """
    从 VTT 逐词标记中提取每个词的精确时间戳。
    仅 VTT 格式可用，SRT 格式无此信息。
    """
    with open(subtitle_path, 'r', encoding='utf-8') as f:
        content = f.read()

    words = []
    # 匹配 <HH:MM:SS.mmm> 模式后紧跟的文本
    pattern = r'(<(\d{2}:\d{2}:\d{2}\.\d{3})>(?:<c>)?([^<]*?)(?:</c>)?)'
    for match in re.finditer(pattern, content):
        ts = vtt_timestamp_to_seconds(match.group(2))
        word = match.group(3).strip()
        if word:
            words.append(WordTimestamp(word=word, start=ts))

    return words
```

用途：当需要更精确的台词间隙定位时，可利用逐词时间戳找到词与词之间的间隙，比整句级别的间隙更细粒度。

---

### 2.4 三重交叉验证策略

#### 2.4.1 核心思路

三种信号源各有优缺点：

| 信号源 | 优点 | 缺点 |
|--------|------|------|
| 视频闪白 | 直接对应集间过渡特效 | 集内也可能有类似闪白（雷电、爆炸等） |
| 音频静音 | 信号稳定，集间几乎一定有静音 | 集内对话间隙也可能被误判 |
| 字幕间隙 | 最精确的台词边界信息 | 集间不一定有字幕间隙（紧接上一集） |

交叉验证策略：**多个信号在时间上吻合的分割点，置信度更高**。

#### 2.4.2 置信度评定规则

> **统一规则**（与 PRD 一致）：
> - 2 个及以上信号在匹配窗口（±3秒）内吻合 → **HIGH**
> - 仅 1 个信号检出 → **LOW**
> - **不存在 MEDIUM 级别**
>
> 任意两个信号的组合（闪白+静音、闪白+字幕、静音+字幕）均等同对待，不区分信号类型权重。

#### 2.4.3 信号融合算法

```
输入：
  flash_points: [闪白时间戳列表]
  silence_points: [静音中点时间戳列表]
  subtitle_gaps: [(gap_start, gap_end)] 字幕静默区间
  video_duration: 视频总时长（秒）

输出：
  merged_points: [(timestamp, confidence, sources)] 融合后的分割点

Step 1: 收集所有候选点
  - 合并三种信号的时间戳为一个有序列表（不去重，保留来源信息）
  - 按时间排序

Step 2: 对每个候选点，检查哪些信号在 ±MATCH_WINDOW 内吻合
  - 检查闪白：候选点 ± MATCH_WINDOW 内是否有 flash_point
  - 检查静音：候选点 ± MATCH_WINDOW 内是否有 silence_point
  - 检查字幕：候选点是否落在 gap_start - MATCH_WINDOW 到 gap_end + MATCH_WINDOW 范围内

Step 3: 置信度评定
  - 2+ 信号吻合 → confidence = HIGH
  - 1 信号 → confidence = LOW

Step 4: 合并同位置分割点
  - 若多个候选点在 MATCH_WINDOW 内互相吻合，合并为一个分割点
  - 合并后的时间戳优先取字幕间隙中点（若有字幕），否则取中位数

Step 5: 台词防串集微调
  - 有字幕时，将所有分割点微调到最近的字幕静默区间中点
  - 无字幕时，使用原始时间戳

Step 6: 误检过滤
  - 相邻分割点间距 < MIN_EPISODE_DURATION 的丢弃

Step 7: 构建分集清单
  - 第一集从 0 开始，最后一集到 video_duration
```

#### 2.4.4 伪代码

```python
@dataclass
class MergedPoint:
    timestamp: float
    confidence: str  # 'HIGH' or 'LOW'（使用 ConfidenceLevel 枚举）
    sources: list[str]  # ['flash', 'silence', 'subtitle']

MATCH_WINDOW = 3.0  # 秒

def cross_validate(
    flash_points: list[float],
    silence_points: list[float],
    subtitle_gaps: list[tuple[float, float]] | None,
    video_duration: float
) -> list[MergedPoint]:
    """
    三重交叉验证：融合闪白、静音、字幕三种信号。
    任一信号源为空列表时，自动降级为双重/单信号验证。
    返回 MergedPoint 列表（不含分集清单，分集清单由 build_episode_list 构建）。
    """
    # 收集所有候选点（使用有序列表而非 set，避免丢失信息）
    all_candidates = sorted(
        flash_points + silence_points +
        ([(s + e) / 2 for s, e in subtitle_gaps] if subtitle_gaps else [])
    )

    merged = []
    for ts in all_candidates:
        sources = []

        # 检查闪白信号
        has_flash = any(abs(ts - fp) < MATCH_WINDOW for fp in flash_points)
        if has_flash:
            sources.append('flash')

        # 检查静音信号
        has_silence = any(abs(ts - sp) < MATCH_WINDOW for sp in silence_points)
        if has_silence:
            sources.append('silence')

        # 检查字幕信号
        has_subtitle = False
        if subtitle_gaps:
            for gs, ge in subtitle_gaps:
                if gs - MATCH_WINDOW <= ts <= ge + MATCH_WINDOW:
                    has_subtitle = True
                    break
        if has_subtitle:
            sources.append('subtitle')

        # 置信度评定：2+ 信号 = HIGH，1 信号 = LOW
        n_sources = len(sources)
        confidence = 'HIGH' if n_sources >= 2 else 'LOW'

        # 微调时间戳：优先取字幕间隙中点
        adjusted_ts = ts
        if subtitle_gaps and has_subtitle:
            for gs, ge in subtitle_gaps:
                if gs - MATCH_WINDOW <= ts <= ge + MATCH_WINDOW:
                    adjusted_ts = (gs + ge) / 2
                    break

        merged.append(MergedPoint(
            timestamp=adjusted_ts,
            confidence=confidence,
            sources=sources
        ))

    # 合并 MATCH_WINDOW 内的邻近点（保留置信度最高的）
    merged = deduplicate_merged_points(merged, MATCH_WINDOW)

    # 误检过滤：最小集时长
    merged = filter_by_min_duration(merged, MIN_EPISODE_DURATION)

    return merged
```

---

### 2.5 台词防串集策略（核心保障）

这是整个系统的**硬性约束**：最终切割点绝不能切在台词中间。

```
策略：
  1. 自动模式：交叉验证后，有字幕时将切割点微调到字幕静默区间中点
  2. 人工输入模式：用户输入的切割点 → 逐个检查是否落在台词区间内 → 标红警告
  3. 人工确认环节：所有切割点最终都经过台词冲突检查 → 有冲突的标红，用户必须手动调整或确认忽略

台词冲突判定：
  对于切割点 cut_ts，若存在字幕条目 sub 满足：
    sub.start < cut_ts < sub.end
  则判定为台词冲突。

VTT 逐词时间戳增强：
  当使用 VTT 字幕时，可利用逐词时间戳做更精确的冲突检测：
  - 切割点不应落在同一句话的任意两个词之间
  - 可将切割点微调到词与词之间的间隙
```

---

### 2.6 分集清单生成

> **修复说明**：原版本在 `merged_points` 为空时会导致 `confidences` 列表构建越界。新版本增加了空列表保护。

```python
@dataclass
class EpisodeSegment:
    episode_num: int        # 集数，从 1 开始
    start_time: float       # 开始时间（秒）
    end_time: float         # 结束时间（秒）
    confidence: str         # 置信度：'HIGH' or 'LOW'
    has_subtitle_conflict: bool  # 是否有台词冲突
    conflict_detail: str | None = None  # 冲突详情

def build_episode_list(
    merged_points: list[MergedPoint],  # 交叉验证后的分割点
    video_duration: float,             # 视频总时长
    subs: list[SubtitleItem] | None = None  # 字幕（可选）
) -> list[EpisodeSegment]:
    """
    构建分集清单。
    - 第一集从 0 开始
    - 最后一集到 video_duration
    - 空分割点列表时返回单集（整片为第一集）
    """
    if not merged_points:
        # 无分割点：整片作为单集
        conflict = check_subtitle_conflict(0.0, subs) if subs else False
        return [EpisodeSegment(
            episode_num=1,
            start_time=0.0,
            end_time=video_duration,
            confidence='LOW',
            has_subtitle_conflict=conflict
        )]

    all_starts = [0.0] + [mp.timestamp for mp in merged_points]
    all_ends = [mp.timestamp for mp in merged_points] + [video_duration]
    # 每集的置信度 = 该集结束分割点的置信度（第一集取第一个分割点）
    confidences = [mp.confidence for mp in merged_points] + ['LOW']  # 最后一集无后续分割点

    episodes = []
    for i, (start, end, conf) in enumerate(zip(all_starts, all_ends, confidences), 1):
        conflict = False
        conflict_detail = None
        if subs:
            conflict = check_subtitle_conflict(start, subs)
            if i < len(all_starts):
                conflict = conflict or check_subtitle_conflict(end, subs)
            if conflict:
                conflict_detail = _get_conflict_detail(end, subs)
        episodes.append(EpisodeSegment(
            episode_num=i,
            start_time=start,
            end_time=end,
            confidence=conf,
            has_subtitle_conflict=conflict,
            conflict_detail=conflict_detail
        ))
    return episodes
```

---

### 2.7 人工输入模式处理

```python
def parse_manual_timestamps(text: str) -> list[float]:
    """
    解析用户粘贴的起始时间列表。
    输入格式：'00:00:00, 00:02:30, 00:05:00'
    支持 HH:MM:SS 和 MM:SS 两种格式。
    """
    parts = [p.strip() for p in text.split(',')]
    timestamps = []
    for part in parts:
        segments = part.split(':')
        if len(segments) == 3:
            h, m, s = segments
            total = int(h) * 3600 + int(m) * 60 + float(s)
        elif len(segments) == 2:
            m, s = segments
            total = int(m) * 60 + float(s)
        else:
            raise ValueError(f"无法解析时间格式: {part}")
        timestamps.append(total)
    return timestamps
```

---

## 3. 数据模型

### 3.1 模型定义

```python
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class ConfidenceLevel(str, Enum):
    """置信度等级"""
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


@dataclass
class SubtitleItem:
    """字幕条目"""
    index: int
    start: float          # 秒
    end: float            # 秒
    text: str


@dataclass
class WordTimestamp:
    """逐词时间戳（VTT 专用）"""
    word: str
    start: float          # 秒


@dataclass
class MergedPoint:
    """交叉验证后的分割点"""
    timestamp: float
    confidence: str       # ConfidenceLevel.HIGH or ConfidenceLevel.LOW
    sources: list[str]    # ['flash', 'silence', 'subtitle']


@dataclass
class EpisodeSegment:
    """分集清单条目"""
    episode_num: int
    start_time: float     # 秒
    end_time: float       # 秒
    confidence: str       # ConfidenceLevel
    has_subtitle_conflict: bool
    conflict_detail: str | None = None


@dataclass
class CutResult:
    """裁剪结果"""
    episode_num: int
    output_path: str
    success: bool
    error: str | None = None


@dataclass
class TaskStatus:
    """任务状态"""
    task_id: str
    status: str           # TaskStatusEnum
    video_path: str | None = None
    audio_path: str | None = None
    subtitle_path: str | None = None
    drama_name: str | None = None
    episodes: list[EpisodeSegment] | None = None
    cut_results: list[CutResult] | None = None
    error_message: str | None = None
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
```

### 3.2 模型关系图

```
SubtitleItem 1──* SubtitleParser.parse() ──→ list[SubtitleItem]
                                                    │
                                                    ▼
                                         extract_silence_gaps()
                                                    │
                                                    ▼
                                         list[tuple[float, float]]
                                                    │
FlashDetector.detect() → list[float] ──────────────┤
SilenceDetector.detect() → list[float] ────────────┤
                                                    ▼
                                         CrossValidator.validate()
                                                    │
                                                    ▼
                                         list[MergedPoint]
                                                    │
                                                    ▼
                                         build_episode_list()
                                                    │
                                                    ▼
                                         list[EpisodeSegment]
                                                    │
                                                    ▼
                                         VideoCutter.cut_batch()
                                                    │
                                                    ▼
                                         list[CutResult]

TaskStatus ──contains──→ list[EpisodeSegment]
           ──contains──→ list[CutResult]
```

---

## 4. 模块设计

### 4.1 模块划分与依赖关系

```
drama-splitter/
├── main.py                  # Gradio 启动入口
├── config.py                # 配置管理（dataclass 封装）
├── requirements.txt
├── README.md
├── .gitignore
├── modules/
│   ├── __init__.py
│   ├── models.py            # 数据模型 + 枚举
│   ├── file_manager.py      # 文件上传、存储、输出目录管理
│   ├── flash_detector.py    # 闪白检测（OpenCV）
│   ├── silence_detector.py  # 音频静音检测（librosa）
│   ├── subtitle_parser.py   # 统一字幕解析（SRT + VTT）
│   ├── cross_validator.py   # 三重交叉验证引擎
│   ├── cutter.py            # ffmpeg 裁剪执行
│   └── task_queue.py        # 任务队列与并发调度
├── web/
│   ├── app.py               # Gradio 界面定义
│   └── components.py        # 可复用 UI 组件
├── utils/
│   ├── time_utils.py        # 时间格式转换工具
│   ├── ffmpeg_check.py      # ffmpeg 环境检测
│   └── logger.py            # 统一日志配置
├── tests/
│   ├── __init__.py
│   ├── test_time_utils.py
│   ├── test_subtitle_parser.py
│   ├── test_cross_validator.py
│   ├── test_cutter.py
│   └── test_file_manager.py
├── outputs/                 # 裁剪输出目录
├── uploads/                 # 上传文件暂存目录
└── logs/                    # 日志目录
```

**模块依赖关系（自底向上）：**

```
Layer 0 (无依赖):     models.py, config.py, utils/logger.py
Layer 1 (依赖 L0):    utils/time_utils.py, utils/ffmpeg_check.py
Layer 2 (依赖 L0-1):  modules/subtitle_parser.py, modules/flash_detector.py,
                      modules/silence_detector.py
Layer 3 (依赖 L0-2):  modules/cross_validator.py, modules/cutter.py,
                      modules/file_manager.py
Layer 4 (依赖 L0-3):  modules/task_queue.py
Layer 5 (依赖 L0-4):  web/app.py, main.py
```

### 4.2 核心模块接口

#### flash_detector.py

```python
class FlashDetector:
    """视频闪白检测器"""

    def __init__(self, config: dict | None = None):
        """初始化，可覆盖默认参数"""

    def detect(self, video_path: str) -> list[float]:
        """检测闪白时间戳，返回候选分割点列表"""
```

#### silence_detector.py

```python
class SilenceDetector:
    """音频静音检测器"""

    def __init__(self, config: dict | None = None):
        """初始化，可覆盖默认参数"""

    def detect(self, audio_path: str) -> list[float]:
        """检测静音区间中点，返回候选分割点列表"""
```

#### subtitle_parser.py

```python
class SubtitleParser:
    """统一字幕解析器（SRT + VTT）"""

    @staticmethod
    def parse(subtitle_path: str) -> list[SubtitleItem]:
        """自动识别格式并解析字幕"""

    @staticmethod
    def extract_silence_gaps(subs: list[SubtitleItem]) -> list[tuple[float, float]]:
        """提取字幕中相邻两条之间的静默区间"""

    @staticmethod
    def extract_word_timestamps(subtitle_path: str) -> list[WordTimestamp]:
        """从 VTT 逐词标记中提取精确词级时间戳（仅 VTT 可用）"""
```

#### cross_validator.py

```python
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
        融合三种信号，返回带置信度的分割点列表（MergedPoint）。
        - 不返回 EpisodeSegment，分集清单由 build_episode_list() 构建
        - 任一信号源为空列表时自动降级
        - 2+信号吻合 = HIGH，1信号 = LOW
        """

    def build_episode_list(
        self, merged_points: list[MergedPoint], video_duration: float,
        subs: list[SubtitleItem] | None = None
    ) -> list[EpisodeSegment]:
        """构建分集清单。检查每个切割点的台词冲突。"""
```

#### cutter.py

```python
class VideoCutter:
    """视频裁剪引擎：基于 ffmpeg -c copy 无损裁剪"""

    def __init__(self, max_workers: int | None = None):
        """初始化。max_workers 默认为 CPU核心数 - 1"""

    def cut_single(self, video_path: str, segment: EpisodeSegment, output_path: str) -> CutResult:
        """裁剪单集视频。返回 CutResult（含 success/error 信息）"""

    def cut_batch(
        self, video_path: str, segments: list[EpisodeSegment],
        output_dir: str, drama_name: str,
        progress_callback: Callable[[int, int], None] | None = None
    ) -> list[CutResult]:
        """顺序批量裁剪，支持进度回调"""

    def cut_batch_concurrent(
        self, video_path: str, segments: list[EpisodeSegment],
        output_dir: str, drama_name: str,
        progress_callback: Callable[[int, int], None] | None = None
    ) -> list[CutResult]:
        """并发批量裁剪。结果按 episode_num 排序后返回"""
```

#### task_queue.py

```python
class TaskQueue:
    """任务队列与并发调度"""

    def __init__(self, max_workers: int | None = None):
        """初始化。max_workers 默认为 CPU核心数 - 1"""

    def create_task(self, video_path: str | None = None, audio_path: str | None = None,
                    subtitle_path: str | None = None, drama_name: str | None = None) -> str:
        """创建新任务，返回 task_id (UUID)"""

    def get_task(self, task_id: str) -> TaskStatus | None:
        """获取任务状态"""

    def list_tasks(self) -> list[TaskStatus]:
        """列出所有任务"""

    async def run_analysis(self, task_id: str) -> TaskStatus:
        """执行分析任务。
        1. 更新状态为 'analyzing'
        2. 并行执行闪白检测和静音检测（asyncio.gather + run_in_executor）
        3. 解析字幕（如果有）
        4. 交叉验证
        5. 构建分集清单
        6. 更新状态为 'awaiting_confirm'
        失败时状态设为 'failed' 并记录错误信息。"""

    async def run_cut(self, task_id: str, episodes: list[EpisodeSegment]) -> list[CutResult]:
        """执行裁剪任务。使用 VideoCutter.cut_batch_concurrent 并发裁剪。"""

    def update_task_episodes(self, task_id: str, episodes: list[EpisodeSegment]) -> None:
        """更新任务的分集清单（用户编辑后）"""

    def delete_task(self, task_id: str) -> None:
        """删除任务"""
```

---

## 5. 数据流

### 5.1 自动模式完整数据流（三重验证）

```
用户上传 mp4 + mp3 + txt/vtt 字幕
      │
      ▼
FileManager.save_upload()
      │  保存到 uploads/{task_id}/
      ▼
TaskQueue.run_analysis()
      │
      ▼ ┌────────────────── 并行 ──────────────────┐
      │  │                                            │
      ▼  ▼                                            ▼
FlashDetector.detect()          SilenceDetector.detect()
  │  → [闪白时间戳]                │  → [静音中点时间戳]
      │                                            │
      │         ┌──────────────────────────┐        │
      │         │ SubtitleParser.parse()   │        │
      │         │  → [字幕条目]            │        │
      │         │ extract_silence_gaps()   │        │
      │         │  → [(gap_start, gap_end)]│        │
      │         └──────────────────────────┘        │
      │                  │                           │
      ▼ ─────────────────┼──────────────────────────┘
                         │
                         ▼
               CrossValidator.validate()
               flash_points + silence_points + subtitle_gaps + video_duration
                         │
                         ▼
               [MergedPoint(timestamp, confidence, sources)]
                         │
                         ▼
               CrossValidator.build_episode_list()
                         │
                         ▼
               [EpisodeSegment(集数, 开始, 结束, 置信度, 台词冲突)]
                         │
                         ▼
               返回前端展示（置信度可视化）
                         │
                         ▼
               用户确认/编辑分割点
                         │
                         ▼
TaskQueue.run_cut()
                         │
                         ▼
VideoCutter.cut_batch_concurrent()
  │  并发 ffmpeg -c copy 裁剪
  ▼
outputs/{剧名}/{剧名}_S01E01.mp4
outputs/{剧名}/{剧名}_S01E02.mp4
...
```

### 5.2 人工输入模式数据流

```
用户上传 mp4 + txt/vtt 字幕（可选）
用户粘贴起始时间文本
      │
      ▼
parse_manual_timestamps()  → [起始时间列表]
      │
      ▼
build_episode_list()       → [EpisodeSegment]
      │
      ▼
check_subtitle_conflict()  → 标记台词冲突
      │
      ▼
返回前端展示（冲突项标红）
      │
      ▼
用户调整后确认
      │
      ▼
VideoCutter.cut_batch()    → 输出文件
```

---

## 6. Gradio 接口设计

### 6.1 界面事件接口

> 本项目使用 Gradio 作为唯一 Web 框架，以下为 Gradio 事件回调接口（非 REST API）。

| Tab | 事件回调 | 功能 | 输入 | 输出 |
|-----|---------|------|------|------|
| Tab 1 | `handle_upload_and_analyze` | 上传文件并触发分析 | video, audio, subtitle, drama_name | 分析结果表格 |
| Tab 1 | `handle_manual_input` | 人工输入起始时间 | timestamps_text, drama_name | 分集清单表格 |
| Tab 2 | `handle_confirm_and_cut` | 确认并触发裁剪 | episodes_df, drama_name | 裁剪任务状态 |
| Tab 2 | `handle_edit_episode` | 编辑分割点 | 编辑后的表格 | 重新校验冲突后的表格 |
| Tab 3 | `handle_open_output_dir` | 打开输出目录 | 无 | 打开文件管理器 |
| Tab 3 | `handle_download` | 下载裁剪文件 | file_path | 文件下载 |

### 6.2 页面流程

```
Tab 1: 上传与分析
  ┌─────────────────────────────────────────────┐
  │  [选择 mp4 视频文件]  (必填)                  │
  │  [选择 mp3 音频文件]  (可选，提高分集准确率)    │
  │  [选择字幕文件 txt/vtt]  (可选)               │
  │  剧名输入框: [___________]                    │
  │  [开始自动分析] 按钮                          │
  │                                              │
  │  分析进度:                                    │
  │  🎬 闪白检测: ████████░░ 80%                 │
  │  🔊 静音检测: ██████████ 完成                 │
  │  📝 字幕解析: ██████████ 完成                 │
  │                                              │
  │  ── 或 ──                                    │
  │                                              │
  │  人工输入模式:                                │
  │  [粘贴起始时间文本框]                          │
  │  [提交人工时间] 按钮                          │
  └─────────────────────────────────────────────┘

Tab 2: 确认与编辑
  ┌─────────────────────────────────────────────┐
  │  分集清单表格:                                │
  │  ┌────┬──────┬──────┬──────┬──────┬─────┐   │
  │  │集数│开始  │结束  │时长  │置信度│冲突 │   │
  │  ├────┼──────┼──────┼──────┼──────┼─────┤   │
  │  │ 1  │00:00 │02:22 │2'22" │🟢高  │ ✓  │   │
  │  │ 2  │02:22 │04:46 │2'24" │🔴低  │ ✗  │   │  ← 标红
  │  │ 3  │04:46 │07:10 │2'24" │🟢高  │ ✓  │   │
  │  └────┴──────┴──────┴──────┴──────┴─────┘   │
  │                                              │
  │  [编辑] [删除] [新增分割点]                    │
  │  [确认并开始裁剪] 按钮                         │
  └─────────────────────────────────────────────┘

Tab 3: 裁剪进度与输出
  ┌─────────────────────────────────────────────┐
  │  裁剪进度: ████████░░ 8/12                   │
  │                                              │
  │  已完成:                                     │
  │  ✓ 我的短剧_S01E01.mp4  (2.3 MB)            │
  │  ✓ 我的短剧_S01E02.mp4  (2.1 MB)            │
  │  ⏳ 我的短剧_S01E03.mp4  (裁剪中...)         │
  │                                              │
  │  [打开输出目录] [全部下载]                     │
  └─────────────────────────────────────────────┘
```

---

## 7. 配置管理

### 7.1 配置封装

使用 dataclass 封装所有可配置参数，支持环境变量覆盖：

```python
import os
from dataclasses import dataclass

@dataclass
class AppConfig:
    """应用配置（所有可调参数集中管理）"""

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

    @classmethod
    def from_env(cls) -> "AppConfig":
        """从环境变量加载配置（覆盖默认值）"""
        import inspect
        params = {}
        sig = inspect.signature(cls)
        for name, param in sig.parameters.items():
            env_val = os.environ.get(f"DRAMA_{name.upper()}")
            if env_val is not None:
                params[name] = type(param.default)(env_val)
        return cls(**params)
```

### 7.2 配置使用方式

各模块通过构造函数接收 config 参数，默认值为 None 时使用 AppConfig 默认值：

```python
# 全局单例
config = AppConfig.from_env()

# 各模块使用
detector = FlashDetector(config.__dict__)
validator = CrossValidator(
    match_window=config.match_window,
    min_episode_duration=config.min_episode_duration
)
```

---

## 8. 日志架构

### 8.1 日志配置

```python
# utils/logger.py
import logging
import os
from logging.handlers import TimedRotatingFileHandler

def setup_logging(log_dir: str = "logs", level: int = logging.DEBUG) -> None:
    """初始化统一日志配置。
    - 控制台输出 INFO 及以上级别
    - 文件输出 DEBUG 及以上级别，按天轮转，保留 7 天
    """
    os.makedirs(log_dir, exist_ok=True)

    formatter = logging.Formatter(
        '%(asctime)s [%(name)s] %(levelname)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    # 控制台 handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)

    # 文件 handler（按天轮转）
    file_handler = TimedRotatingFileHandler(
        os.path.join(log_dir, "drama_splitter.log"),
        when='midnight',
        backupCount=7,
        encoding='utf-8'
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)

    # 根 logger
    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)
```

### 8.2 模块日志使用

每个模块使用独立的 logger（以模块名为标识）：

```python
# modules/flash_detector.py
import logging
logger = logging.getLogger(__name__)

class FlashDetector:
    def detect(self, video_path: str) -> list[float]:
        logger.info(f"开始闪白检测: {video_path}")
        # ...
        logger.debug(f"采样帧数: {len(brightness_series)}, 亮度范围: {min_bright}-{max_bright}")
        logger.info(f"闪白检测完成: 检出 {len(candidates)} 个候选点")
```

---

## 9. 异常处理策略

### 9.1 自定义异常层次

```python
# modules/exceptions.py

class DramaSplitterError(Exception):
    """所有自定义异常的基类"""
    pass

class FileNotFoundError(DramaSplitterError):
    """文件不存在"""
    pass

class FileFormatError(DramaSplitterError):
    """文件格式错误"""
    pass

class FFmpegError(DramaSplitterError):
    """ffmpeg 相关错误"""
    pass

class SubtitleParseError(DramaSplitterError):
    """字幕解析错误"""
    pass

class AnalysisError(DramaSplitterError):
    """分析过程错误"""
    pass

class CutError(DramaSplitterError):
    """裁剪过程错误"""
    pass
```

### 9.2 异常处理策略

| 场景 | 异常类型 | 处理方式 |
|------|---------|---------|
| 视频文件无法打开 | FileFormatError | 记录日志，任务状态设为 failed，返回错误提示 |
| 音频文件无法加载 | FileFormatError | 记录日志，降级为无音频模式继续分析 |
| 字幕文件格式错误 | SubtitleParseError | 记录日志，降级为无字幕模式继续分析 |
| ffmpeg 未安装 | FFmpegError | 启动时检测，提示用户安装 |
| 裁剪失败 | CutError | 记录日志，标记该集失败，继续处理其他集 |
| 时间输入格式非法 | ValueError | 前端校验 + 后端校验，返回具体错误信息 |
| 磁盘空间不足 | CutError | 裁剪前预检，不足时提前警告 |

---

## 10. 并发与性能设计

### 10.1 分析阶段

- 闪白检测和静音检测可**并行执行**（分别读视频和音频，互不阻塞）
- 使用 `asyncio.gather` + `loop.run_in_executor` 将同步的检测函数调度到线程池
- 字幕解析速度极快（< 1 秒），不构成瓶颈
- 1 小时视频闪白检测：约 3-5 分钟（CPU 密集，采样率 15fps）
- 1 小时音频静音检测：约 1-2 分钟（CPU 密集但比视频快）
- 交叉验证计算：< 1 秒
- **总分析时间 ≈ max(闪白检测, 静音检测) ≈ 3-5 分钟**（并行后取最长）

### 10.2 裁剪阶段

- 裁剪阶段为 **IO 密集型**（ffmpeg -c copy 主要是读写磁盘）
- 可并发执行多个裁剪任务
- 并发数 = `min(CPU核心数 - 1, 4)`，防止磁盘 IO 瓶颈

### 10.3 批量任务调度策略

```
分析任务：同一视频的闪白+静音并行，不同视频的分析串行
裁剪任务：并发（最多 N 个同时裁剪，N = CPU核心数 - 1）
跨视频：视频A在裁剪时，视频B可以同时进行分析
```

---

## 11. ffmpeg 裁剪细节

### 11.1 无损裁剪命令

```bash
ffmpeg -y -i input.mp4 \
  -ss {start_time} \
  -to {end_time} \
  -c copy \
  -avoid_negative_ts make_zero \
  output.mp4
```

### 11.2 关键帧对齐问题

`-c copy` 模式下，`-ss` 放在 `-i` 之后是精确 seek，但切割点只能对齐到最近的关键帧（I-frame），可能导致切割点前后有几秒偏差。

**应对策略：**

1. 切割点已通过字幕交叉验证微调到静默区间，几秒偏差仍在静默区间内，不会串台词
2. 用户已接受"差几秒无所谓"，此精度满足需求
3. 若需更快速度，可将 `-ss` 放在 `-i` 之前（快速 seek），但精度略低

### 11.3 备选：精确切割（如需更高精度）

若未来需要帧级精确切割，可改用重编码模式：

```bash
ffmpeg -y -ss {start_time} -i input.mp4 \
  -to {duration} \
  -c:v libx264 -preset fast -crf 18 \
  -c:a aac \
  output.mp4
```

> 当前需求下不使用此方案（慢、有画质损失）。

---

## 12. 安全设计

| 场景 | 措施 |
|------|------|
| 文件上传校验 | 按文件头魔数校验文件类型（非仅后缀），拒绝伪造文件 |
| 路径遍历防护 | 剧名中的特殊字符（`/ \ : * ? " < > \|`）替换为下划线 |
| 临时文件安全 | uploads/ 目录按 task_id 隔离，任务完成后清理 |
| 本地运行 | 不监听公网地址，仅 0.0.0.0 用于本机访问（Gradio 默认安全） |
| 日志脱敏 | 日志中不记录文件完整路径中的用户名信息（可选） |

---

## 13. 测试策略

### 13.1 测试分层

| 层级 | 范围 | 工具 | 覆盖率目标 |
|------|------|------|-----------|
| 单元测试 | utils/, modules/ 的纯逻辑函数 | pytest | ≥ 80% |
| 模块测试 | 各 Detector/Parser/Validator 类 | pytest + mock | ≥ 70% |
| 集成测试 | 跨模块数据流（分析→验证→裁剪） | pytest | 关键路径覆盖 |
| 端到端测试 | Gradio 界面完整流程 | 手动测试 | MVP 功能覆盖 |

### 13.2 测试文件组织

```
tests/
├── conftest.py              # pytest 公共 fixture
├── test_time_utils.py       # 时间工具测试
├── test_subtitle_parser.py  # 字幕解析测试（构造 SRT/VTT 示例数据）
├── test_cross_validator.py  # 交叉验证测试（覆盖所有信号组合）
├── test_cutter.py           # 裁剪测试（路径生成、CutResult 模型）
└── test_file_manager.py     # 文件管理测试
```

### 13.3 测试数据策略

- 字幕解析测试：构造小的 SRT/VTT 格式字符串，保存为临时文件测试
- 交叉验证测试：构造模拟的 flash_points / silence_points / subtitle_gaps 数据
- 裁剪测试：ffmpeg 裁剪需要真实视频文件，标记为 skip（`@pytest.mark.skip`）
- 不依赖真实长视频/音频文件

---

## 14. 依赖清单

```
# requirements.txt
gradio==4.44.0
opencv-python==4.10.0.68
librosa==0.10.2
ffmpeg-python==0.2.0
pysrt==1.1.2
webvtt-py==0.5.1
python-multipart==0.0.9
pydantic==2.9.0
numpy==1.26.4

# 开发依赖（requirements-dev.txt）
pytest==8.3.0
pytest-cov==5.0.0
mypy==1.11.0
```

**系统依赖：**
- ffmpeg（需单独安装，加入 PATH）
- Python 3.10+

---

## 15. 错误处理

| 场景 | 处理方式 |
|------|----------|
| ffmpeg 未安装/不在 PATH | 启动时检测，提示用户安装并给出下载链接 |
| 视频文件损坏/无法打开 | 返回错误提示，跳过该任务 |
| 音频文件损坏/无法加载 | 返回错误提示，降级为无音频模式继续分析 |
| 字幕文件格式错误 | 提示格式错误，降级为无字幕模式继续分析 |
| 闪白检测未找到任何分割点 | 检查静音检测结果，若静音也无结果，引导用户切换到人工输入模式 |
| 静音检测未找到任何分割点 | 检查闪白检测结果，若闪白也无结果，引导用户切换到人工输入模式 |
| 所有信号源均未检出 | 返回单集（整片），提示用户切换人工输入模式 |
| 裁剪失败（磁盘空间不足等） | 记录错误日志，标记该集裁剪失败，继续处理其他集 |
| 时间输入格式非法 | 前端校验 + 后端校验，返回具体错误信息 |

---

## 16. 后续优化方向（v2+）

| 方向 | 说明 |
|------|------|
| 场景切换检测 | 除闪白外，增加场景切换检测（OpenCV 场景分割算法）作为辅助分割信号 |
| 帧预览 | 人工确认页面展示每个切割点附近的帧截图 + 音频波形 |
| 拖拽时间轴 | 前端升级为 Vue，支持可视化时间轴拖拽调整切割点 |
| 自适应阈值 | 根据视频整体亮度/音频能量分布自动调整检测阈值，适配不同视频 |
| VTT 逐词时间戳深入利用 | 利用逐词时间戳做更精细的台词间隙定位和冲突检测 |
| 音频指纹去重 | 检测片头/片尾重复出现的音频片段，辅助识别集间边界 |
| 信号权重 | 不同信号源引入可靠性权重，替代简单的计数机制 |
| 任务持久化 | 将任务状态序列化到 SQLite，支持跨会话恢复 |
