# 短剧总集篇自动分集工具 · 技术方案

> **形态** 本地命令行工具 / 可选 Web UI
> **语言** Python 3.10+
> **核心** 多信号融合检测

把一小时以上的油管短剧总集篇，自动拆分成一集一集独立的单集片段。核心难点不在裁剪，而在如何从没有任何章节元数据的拼接视频里，自动推断出每一集的边界时间点，并分清是第几集。

---

## 1 问题剖析：为什么找分割点这么难

裁剪本身不难，FFmpeg 一行命令就能切。真正的难点在于"切哪里"——总集篇只是把多集视频首尾相接拼在一起，不带任何章节标记，工具必须从音视频内容本身推断边界。

短剧总集篇的结构看似简单（多集拼接），但自动识别集边界有三个绕不开的难点。

### 1.1 难点一：集边界不等于场景切换

最直觉的做法是用场景检测找画面突变点。但短剧每一集内部本身就有大量镜头切换——对话正反打、场景转换、特写切换，这些都会触发场景检测。如果直接把所有场景切换点当集边界，会切出几十上百个碎片。工具真正要识别的是"集与集之间的结构性分界"，而不是"镜头与镜头之间的叙事切换"，两者在视觉特征上经常无法区分。

### 1.2 难点二：拼接方式不统一

不同总集篇的拼接习惯不同：有的在集与集之间插入一两秒黑帧过渡；有的直接硬切，上一集结尾紧接下一集开头；有的每集前面带固定片头（标题卡 + 背景音乐）；有的什么标记都没有。没有任何单一信号能覆盖所有拼接方式，必须组合多种信号并容忍缺失。

### 1.3 难点三：不仅要找时间点，还要分清第几集

需求里明确要求输出 `[{集数, 开始时间, 结束时间}]`，也就是说工具不仅要定位边界，还得给每个片段标上"这是第几集"。大部分信号只能给出"这里有一个边界"，唯一能直接提供集数信息的是每集开头的标题文字（如"第3集""EP.3"）。这决定了 OCR 标题识别在方案中的核心地位。

> **结论**
> 单一信号不可靠：场景检测会过切、黑帧检测会漏检、纯时间聚类会错位。可行路线是**多信号分层融合**——用能直接给出集数的强信号做骨架，用其他信号做补充和校验，最后融合出带置信度的时间清单。

---

## 2 核心策略：多信号分层融合

把"集边界识别"建模成一个多源信号融合问题：四类检测器并行扫描视频，各自产出候选边界点，再由一个决策引擎按优先级合并、对齐集数、标注置信度。

核心洞察在于：集边界与普通镜头切换的本质区别，是边界处通常伴随**周期性重复的结构性信号**。每集开头的标题文字会重复出现、固定的片头音乐会重复出现、拼接处的黑帧静音会重复出现。抓住这些重复结构，就能把"集边界"从海量镜头切换里筛出来。

```
总集篇 MP4 (1h+ / 数百MB)
    │
    ├──→ OCR 标题识别 ─────→ 集数 + 起点
    ├──→ 音频指纹片头 ─────→ 重复起点
    ├──→ 黑帧 + 静音 ──────→ 拼接缝
    └──→ 场景突变 + 聚类 ──→ 候选边界
                    │
            ┌───────┴───────┐
            │  融合决策引擎  │
            │  · 候选点聚类  │
            │  · 按优先级合并 │
            │  · 集数序列对齐 │
            │  · 置信度标注   │
            └───────┬───────┘
                    │
          时间清单 JSON [{集数,开始,结束,置信度}]
                    │
            FFmpeg 批量裁剪 → 单集文件 x N
```

四类信号在方案中扮演不同角色，按"能提供的信息量"排定优先级：

| 角色 | 信号 | 说明 |
|------|------|------|
| **主信号 A · 骨架** | OCR 集数标题识别 | 每集开头的"第X集"标题。唯一能直接给出集数的信号，作为整张时间清单的骨架锚点 |
| **主信号 B** | 音频指纹片头检测 | 用 Chromaprint 提取音轨指纹，滑动窗口找重复出现的片头音乐。每次重复就是一集起点 |
| **辅助信号 C** | 黑帧 + 静音联合检测 | FFmpeg blackdetect + silencedetect 找拼接缝。两者同时出现的点可信度高 |
| **辅助信号 D** | 场景突变 + 时长聚类 | PySceneDetect 找所有切换点，结合每集预估时长聚类，补充校验其他信号 |

---

## 3 四类检测信号详解

| 信号 | 角色 | 原理 | 能否给集数 | 可靠性 | 主要局限 |
|------|------|------|-----------|--------|----------|
| **OCR 标题识别** | 主信号 | 抽帧 → OCR → 正则匹配"第X集" | 能 | 高 | 标题样式/位置不固定时需调参 |
| **音频指纹片头** | 主信号 | Chromaprint 指纹 + 滑窗自相关 | 否（需对齐） | 高（有片头时） | 无固定片头则失效 |
| **黑帧 + 静音** | 辅助 | FFmpeg blackdetect + silencedetect | 否 | 中 | 硬切无过渡的拼接会漏检 |
| **场景 + 时长聚类** | 辅助 | PySceneDetect + 时长直方图聚类 | 否 | 中低 | 集内镜头多时噪声大 |

### 3.1 OCR 集数标题识别（主信号 · 骨架）

这是整个方案里性价比最高、也是唯一能直接产出"第几集"信息的信号。短剧几乎每集开头都会打出标题卡——"第1集""第3话""EP.3""第三集"等。只要识别出这些标题首次出现的时间点，就同时拿到了集数和该集的起始时间。

**实现步骤**

- **抽帧**：用 FFmpeg 按固定间隔（默认每 0.5 秒抽一帧）抽取缩略图，降低后续 OCR 计算量。标题卡通常停留 1–3 秒，0.5 秒间隔能保证至少捕获到一帧。
- **时间戳获取**：抽帧时使用 FFmpeg 的 `fps` 滤镜按固定频率采样，帧文件按序号命名（`frame_00000001.png`、`frame_00000002.png`...），时间戳通过 `帧序号 × 抽帧间隔` 计算。这种方式比 `-frame_pts` 更可靠——后者写入的 PTS 值是视频时间基（timebase）单位的原始值，直接除以 1000 会得到错误结果，必须先通过 ffprobe 获取 timebase 才能正确换算。按序号乘间隔的方式虽然存在微小偏差（FFmpeg 的 fps 滤镜可能丢弃个别无法对齐的帧），但对 0.5 秒间隔的标题卡识别来说精度足够。

```bash
# 抽帧：每 0.5 秒一帧（fps=2），缩放到 640 宽加速 OCR
# 帧文件按序号命名，时间戳 = 序号 / fps
ffmpeg -i input.mp4 -vf "fps=2,scale=640:-1" \
  frames/frame_%08d.png
```

```python
# Python：从帧序号计算时间戳 + OCR + 正则匹配集数
import re, glob
from rapidocr_onnxruntime import RapidOCR

ocr = RapidOCR()
FPS = 2  # 抽帧频率，与 FFmpeg 命令一致
# 匹配 第1集 / 第03话 / EP.3 / Episode 12 / 第三集 / 第一百二十三集 等
PAT = re.compile(
    r"(?:第\s*([0-9零一二三四五六七八九十百]+)\s*[集话話回]|EP\.?\s*(\d+)|Episode\s*(\d+))",
    re.I
)

hits = {}  # 集数 -> 首次出现时间(秒)
for f in sorted(glob.glob("frames/frame_*.png")):
    # 从文件名提取帧序号，计算时间戳
    frame_idx = int(re.search(r"(\d+)", f).group(1))
    sec = frame_idx / FPS  # 帧序号 / 抽帧频率 = 时间戳（秒）
    res = ocr(f)
    for box, text, score in (res.boxes or []):
        m = PAT.search(text)
        if m:
            ep = m.group(1) or m.group(2) or m.group(3)
            ep = cn2num(ep) if not ep.isdigit() else int(ep)
            hits.setdefault(ep, sec)  # 只记首次出现
            break
```

- **OCR 识别**：对每帧做文字识别，得到文本块及其在画面中的位置。短剧标题通常固定在画面顶部或中央，可先按区域过滤减少误识别。
- **正则匹配集数**：用一组模式匹配集数表达，覆盖中英文常见写法。中文数字转换函数 `cn2num` 需支持 0–999（含"百"位），以应对超过 100 集的短剧。
- **多帧投票**：单帧识别可能有错字，对连续若干帧的识别结果做投票，取出现频次最高的集数，过滤偶发误识别。
- **去重**：同一集数在连续多帧被识别到，只取该集数首次出现的时间点作为该集起点。

> **为何这是骨架信号**
> OCR 直接给出 `{集数, 起始时间}` 的对应关系，其他信号都只能给出"某个时间点有一个边界"。因此融合时以 OCR 识别到的集数序列为主干，其余信号向其对齐。即便其他信号全部失效，仅靠 OCR 通常也能完成大部分分集。

### 3.2 音频指纹片头检测（主信号）

很多短剧每集都带一段相同的片头——固定的背景音乐、固定的片头动画。这段音频在每个集首都重复出现。利用音频指纹可以精确地定位每一次重复，从而得到每集的起始时间点。这正是 Jellyfin、Plex 等媒体服务器实现"跳过片头"功能的核心思路 [1][2]。

**实现步骤**

- **提取音轨**：FFmpeg 抽出 16kHz 单声道 PCM 或直接喂给 fpcalc。
- **生成指纹序列**：用 Chromaprint（命令行工具 `fpcalc`，或 Python 库 `pyacoustid`）逐秒生成音频指纹，得到一个时间序列。Chromaprint 的指纹是一个 32 位整数数组，每个整数是一个"子指纹"，编码了约 1.27 秒音频的频谱特征。
- **提取片头模板**：取视频开头一小段（如前 20 秒）作为片头模板指纹。增加模板有效性校验：如果模板与自身后续 20 秒的指纹相似度低于 0.5，说明开头并非固定片头（可能是广告或非标准内容），该信号自动降级。
- **滑动窗口匹配**：在整个音轨指纹序列上滑动比对模板，计算每个偏移位置的相似度。相似度出现峰值的偏移位置，就是片头重复出现的时间点。

**关键修正：指纹相似度计算**

Chromaprint 指纹是 32 位整数数组，不能简单地用 `np.mean(window == template)` 比较整个整数是否相等——两个子指纹只要有一位不同就会被判为完全不匹配，这会导致相似度几乎永远为 0。正确的比较方式是**位级 Hamming 距离**：对每对子指纹做 XOR，统计不同的位数（popcount），位错误率（BER）= 总错误位数 / (32 × 子指纹数)，相似度 = 1 - BER。

```python
import numpy as np

def fingerprint_similarity(window: np.ndarray, template: np.ndarray) -> float:
    """计算两段 Chromaprint 指纹的位级相似度。

    Chromaprint 指纹是 32 位整数数组，每个整数是一个子指纹。
    相似度 = 1 - 位错误率（BER），BER = 不同位数 / 总位数。
    """
    if len(window) != len(template):
        return 0.0
    # 逐元素 XOR
    xor_result = np.bitwise_xor(
        window.astype(np.uint32), template.astype(np.uint32)
    )
    # 统计每个 XOR 结果中 1 的个数（popcount）
    # np.bitwise_count 需要 NumPy 2.0+；兼容写法用查找表
    try:
        bit_counts = np.bitwise_count(xor_result)  # NumPy 2.0+
    except AttributeError:
        bit_counts = np.vectorize(
            lambda x: bin(int(x) & 0xFFFFFFFF).count('1')
        )(xor_result)
    total_bits = len(window) * 32
    total_errors = int(np.sum(bit_counts))
    return 1.0 - total_errors / total_bits
```

```python
# 滑窗匹配：取前 ~20s 作片头模板，在全长上滑窗求相似度
def find_repeats(fp_full, template_len, fingerprint_rate, threshold=0.85):
    """fp_full: 完整指纹数组; fingerprint_rate: 每秒指纹数"""
    template = fp_full[:template_len]
    sims = []
    for off in range(0, len(fp_full) - template_len):
        window = fp_full[off : off + template_len]
        sim = fingerprint_similarity(window, template)
        timestamp = off / fingerprint_rate
        sims.append((timestamp, sim))
    # 峰值检测：相似度 > 阈值 且为局部极大值 → 一集起点
    return peak_detect(sims, threshold=threshold)
```

- **峰值提取**：对相似度曲线做峰值检测，每个显著峰值对应一集的起点。第一个峰值（offset=0 附近，即片头自身）跳过。合并距离 < 30 秒的峰值（只保留相似度最高的），避免同一片头的多帧匹配产生重复候选。

这种方法的鲁棒性来自音频指纹本身 [3]——它对压缩差异、轻微音质变化和小幅时间偏移都不敏感。局限是：如果短剧没有固定片头（每集开头就是剧情正片），这个信号会失效，需要降级依赖 OCR 和其他信号。

### 3.3 黑帧 + 静音联合检测（辅助信号）

集与集的拼接处，常常会插入短暂的黑屏和静音作为过渡。FFmpeg 自带的 `blackdetect` 和 `silencedetect` 滤镜可以快速扫描出所有黑帧段和静音段 [4][5]。这两个信号单独看都不够可靠（黑帧可能出现在正片里、静音也可能出现在剧情停顿处），但**两者的交集**——同时出现黑帧和静音的时间点——作为拼接缝的可信度很高。

```bash
# 一次扫描同时检测黑帧与静音（输出到 stderr，需解析）
ffmpeg -i input.mp4 -vf "blackdetect=d=0.3:pix_th=0.10" \
  -af "silencedetect=noise=-40dB:d=0.3" \
  -f null - 2> detect.log

# 解析 black_start / black_end 与 silence_start / silence_end
# 取两类区间的时间交集 → 高置信拼接缝候选点
```

> **局限**
> 不少总集篇是硬拼接——上一集最后一句台词直接接下一集第一句，中间没有任何黑帧或静音。这种情况下本信号完全失效，必须靠 OCR 和音频指纹兜底。因此它只能做辅助校验，不能做唯一依据。

### 3.4 场景突变 + 时长聚类（辅助信号）

用 PySceneDetect 扫描出视频中所有的场景切换点 [6]。这些点里既包含集内镜头切换，也包含集边界。单看无法区分，但可以结合"每集大致时长"做筛选：短剧单集通常在 1–3 分钟，如果两个候选边界之间的间距远小于这个范围，大概率是集内切换而非集边界。

**聚类算法细化**：对所有相邻场景切换点的间隔做直方图统计，bin 宽度为 10 秒，范围覆盖 0 到 `max_episode_sec × 2`（默认 600 秒）。在 `[min_episode_sec, max_episode_sec]`（默认 60–300 秒）范围内找到频次最高的 bin，该 bin 的中心值即为"典型单集时长"估计值。如果该范围内出现多个相近的峰（双峰间距 < 30 秒），取频次更高的那个；如果频次相同，取更接近 120 秒（短剧最常见时长）的那个。如果该范围内没有任何间隔，说明场景切换点稀疏，将 120 秒作为默认值。

对每个场景切换点 `t_i`，检查是否存在另一个切换点 `t_j` 使得 `t_j - t_i` 落在 `[typical × 0.8, typical × 1.2]` 范围内。如果存在，`t_i` 标记为候选集边界，置信度为 0.5。这个信号噪声较大，主要用来在其他信号缺失时提供候选、或对其他信号的结果做交叉验证。

---

## 4 融合决策引擎

四类信号各自产出候选边界点后，需要一个决策引擎把它们合并成一份干净的时间清单。

融合的核心思路是：以 OCR 集数序列为骨架，其他信号向骨架对齐；相近候选点合并并提升置信度；无法对齐的候选点降级处理或标记人工复核。

### 4.1 决策流程

```
OCR 候选 {集数, t} ★骨架
         │
         ├── 骨架是否成立? (≥2 个集数)
         │     ├── 是 → 按集数序列构建 [{ep, start}] 骨架
         │     └── 否 → 用音频指纹/黑帧候选排序建临时骨架
         │
其他信号候选 {t} ──→ 候选点时空聚类 (±3s 内合并)
                          │
                    其他信号向骨架对齐
                    命中则提升置信度
                          │
                    补全集数 / 修补缺口
                          │
                    标注置信度 高/中/低
                          │
                    输出清单 + 低置信项待复核
```

### 4.2 关键机制

**候选点时空聚类**

不同信号检测到的边界点很少完全重合，通常会有几秒偏差。把所有候选点按时间排序，±3 秒窗口内的点归为同一簇，取簇内中位时间作为该边界的最终时间。簇内信号种类越多，该边界置信度越高。特殊处理：如果同一簇内有两个不同集数的 OCR 候选点（如第 2 集和第 3 集时间差 ≤ 3 秒），不合并而是拆分为两个独立边界。

**集数序列对齐**

OCR 给出的集数序列（如 1, 2, 3, 4...）是骨架。如果某个集数缺失（OCR 漏识别），但音频指纹在该位置附近检测到一个重复片头，就可以推断出这里存在一集并用前后集数推算缺失的集数，标记为推断补全（`is_inferred=True`）。反过来，如果 OCR 检测到集数但附近没有任何其他信号佐证，标记为中置信度供复核。

如果最小集数 > 1（如 OCR 只识别到第 5 集开始），且最小集数 ≤ 5，在前面按时间均分插入缺失集；如果最小集数 > 5，可能 OCR 只识别到了中段，不做前插，记录警告。

**置信度分级**

以下分级规则为三份文档（PRD、技术方案、开发提示词）的统一标准：

| 置信度 | 判定条件 | 处理方式 |
|--------|----------|----------|
| **高** | OCR 集数命中 + 至少 1 个其他信号在 ±3 秒窗口内同时命中 | 直接裁剪，无需复核 |
| **中** | 仅 OCR 命中（无其他信号佐证），或推断补全的集但有 ≥ 2 个其他信号佐证 | 裁剪但建议快速复核 |
| **低** | 推断补全且其他信号佐证 < 2 个，或仅单一弱信号命中，或集数序列有断点 | 必须人工复核后确认 |

---

## 5 裁剪输出层

拿到时间清单后，裁剪是相对确定的部分。关键是根据精度需求在"无损快速"和"精确重编码"之间做权衡。

### 5.1 两种裁剪模式

| 模式 | 命令特点 | 速度 | 精度 | 适用场景 |
|------|----------|------|------|----------|
| **无损快速** | `-c copy` 流复制，不重编码 | 极快（分钟级） | 对齐到最近关键帧，切口可能有 ±1–2s 偏差 | 默认推荐，短剧对切口精度要求不高 |
| **精确重编码** | `-c:v libx264` 重编码 | 慢（取决于时长/CPU） | 帧级精确 | 对开头/结尾帧精度有要求时 |

### 5.2 关键帧对齐技巧

无损模式下，FFmpeg 的 `-ss` seek 行为决定切口精度。把 `-ss` 放在 `-i` 之前是快速 seek（跳到最近关键帧，快但可能不精确）；放在之后是精确 seek（逐帧解码到目标点，精确但慢）。无损裁剪推荐用"前 `-ss` 快速定位到关键帧附近 + 配合 `-to` 控制结尾"的写法。

```bash
# 无损快速裁剪单集（推荐默认）
ffmpeg -ss 00:03:12.5 -i input.mp4 -to 00:05:48.0 \
  -c copy -avoid_negative_ts make_zero "第01集.mp4"

# 精确重编码裁剪（需要帧级精确时）
ffmpeg -i input.mp4 -ss 00:03:12.5 -to 00:05:48.0 \
  -c:v libx264 -preset fast -crf 20 -c:a aac "第01集.mp4"
```

> **推荐默认**
> 短剧分集对开头/结尾一两帧的精度几乎无感，**默认走无损快速模式**即可，一小时视频通常几分钟内完成全部分集。仅当用户在配置里开启"精确模式"时才重编码。文件命名统一为 `第01集.mp4`、`第02集.mp4`，零填充保证排序正确。

---

## 6 系统架构与流水线

整个工具按"采集—融合—裁剪"三层组织，模块之间通过中间产物（候选点 JSON、时间清单 JSON）解耦，便于单独调试和替换。

```
入口层
  CLI 参数 / Web UI（输入文件 + 配置）
        │
信号采集层（可并行）
  ├── frame_sampler（FFmpeg 抽帧）
  ├── ocr_detector（RapidOCR + 正则）
  ├── audio_fingerprint（Chromaprint 滑窗）
  ├── av_detector（blackdetect + silence）
  └── scene_cluster（PySceneDetect）
        │
融合决策层
  ├── candidate_merger（候选点聚类）
  ├── episode_aligner（集数序列对齐）
  └── confidence_scorer（置信度标注）
        │
输出层
  ├── 清单导出（JSON / CSV / 时间轴预览）
  ├── cutter（FFmpeg 批量裁剪）
  └── review_ui（低置信项复核，可选）
```

**模块职责**

- **frame_sampler**：封装 FFmpeg 抽帧逻辑，输出带时间戳的帧序列，供 OCR 和场景检测共用，避免重复解码。时间戳通过帧序号 × 抽帧间隔计算。
- **ocr_detector**：对帧做 OCR + 正则匹配，输出 `[{集数, 时间}]` 候选。
- **audio_fingerprint**：独立提取整条音轨指纹并滑窗匹配（位级 Hamming 距离），输出 `[{时间}]` 候选。
- **av_detector**：跑 blackdetect + silencedetect 并取交集，输出 `[{时间}]` 候选。
- **scene_cluster**：PySceneDetect 结果 + 时长聚类，输出 `[{时间}]` 候选。
- **candidate_merger / episode_aligner / confidence_scorer**：融合三步，产出最终时间清单。
- **cutter**：读清单调用 FFmpeg 批量裁剪。
- **review_ui**：低置信项的可视化复核界面（可选），让用户快速确认或微调边界。

每个信号采集模块独立产出标准格式的候选点 JSON，任何单个模块都可以单独禁用或替换而不影响其他部分。这种解耦让调试变得容易——出问题时可以只跑某个信号看它的候选点分布。

**并行调度说明**

四路检测器使用 `concurrent.futures.ThreadPoolExecutor` 并行运行。其中 FFmpeg subprocess 调用（blackdetect、silencedetect、extract_audio）、NumPy 数组运算和 OpenCV 图像处理都会释放 GIL，因此线程池对大部分检测器有效。唯一的瓶颈是 OCR 链路——RapidOCR 的 Python 层是 CPU 密集的纯 Python 代码，受 GIL 限制无法与其他 CPU 密集任务真正并行。但由于 OCR 链路内部调用的 ONNX Runtime C++ 后端也会释放 GIL，实际并行效率取决于 OCR 中 Python 预处理/后处理的占比。如果 OCR 成为严重瓶颈，可将其改为 `ProcessPoolExecutor` 单独运行。

---

## 7 技术栈选型

全部基于 Python 生态，保证各环节都有成熟库可用，且可在本地一台普通机器上跑通。

| 环节 | 选型 | 选择理由 |
|------|------|----------|
| 语言 | Python 3.10+ | 视频/OCR/音频指纹生态最全；FFmpeg 通过 subprocess 调用 |
| 视频 I/O 与裁剪 | FFmpeg / ffprobe | 抽帧、blackdetect、silencedetect、裁剪一体化；业界标准 |
| 场景检测 | PySceneDetect | 专为场景边界检测设计，基于 OpenCV，支持多种检测算法 [6] |
| 音频指纹 | Chromaprint (fpcalc) + pyacoustid | 音频重复段检测的事实标准；Jellyfin/Plex 跳片头均基于此 [1] |
| OCR 引擎 | RapidOCR（默认）/ PaddleOCR（高精度） | RapidOCR 纯 CPU 轻量、中文识别好；PaddleOCR 精度更高可作可选 |
| 帧处理 | OpenCV (headless) | 抽帧后处理、区域裁剪、图像预处理；headless 版无 GUI 依赖 |
| 配置与清单 | pydantic v2 + JSON | 候选点/时间清单用结构化模型约束，便于校验和序列化 |
| Web UI（可选） | Streamlit | 几十行代码搭出带时间轴预览和复核的本地 Web 界面 |
| 打包分发 | PyInstaller | 打包成单目录可执行文件，免环境配置 |

> **OCR 引擎取舍**
> RapidOCR 基于 ONNX Runtime，**纯 CPU 即可运行**，体积小、启动快，中文短剧标题识别足够用，作为默认引擎。PaddleOCR 精度略高但依赖更重，作为可选的高精度后端。两者 API 相近，可在配置里切换。不建议用 Tesseract——中文识别效果明显不如前两者。

> **多音轨处理**
> 部分视频可能包含多条音轨。提取音轨时默认选择第一条音轨（`-map 0:a:0`），并在 ffprobe 结果中记录音轨数量。如果检测到多条音轨，在日志中记录警告，但不改变默认行为。用户可在后续版本中通过配置指定音轨索引。

---

## 8 关键决策点与权衡

| 决策点 | 选项 | 推荐 | 权衡 |
|--------|------|------|------|
| **运行形态** | 纯命令行 | MVP 首选 | 最简单，开发快，适合自己批量用 |
| | 本地 Web UI（Streamlit） | 正式版推荐 | 带时间轴预览和低置信复核，体验最好，开发量适中 |
| | 桌面 GUI（PySide6） | — | 体验好但开发成本高，非必要不选 |
| **OCR 引擎** | RapidOCR | 默认 | 轻量纯 CPU，中文够用 |
| | PaddleOCR | 可选 | 精度更高，依赖重，有 GPU 可加速 |
| **裁剪精度** | 无损快速（-c copy） | 默认 | 极快，切口对齐关键帧，±1–2s |
| | 精确重编码 | 可选 | 帧级精确，慢 |
| **自动化程度** | 半自动 | 默认 | 低置信项弹给用户快速确认，兼顾效率与准确 |
| | 全自动 | — | 不人工干预，适合量大且能容忍少量错切 |
| **中文标题** | 是（主要场景） | 默认 | 正则覆盖"第X集/第X话/第三集"等中文写法，cn2num 支持 0–999 |
| | 否（英文短剧） | — | 正则覆盖 EP.X / Episode X |
| **并行调度** | ThreadPoolExecutor | 默认 | 对 subprocess/NumPy/OpenCV 有效；OCR 链路受 GIL 限制 |
| | ProcessPoolExecutor | 可选 | OCR 瓶颈严重时改用，但进程间通信开销增大 |

> **关于半自动**
> 追求 100% 全自动在工程上不现实——总有边缘案例（标题被遮挡、片头音乐变化、硬切无任何标记）会让自动检测出错。半自动的精髓是让人工只做"最后 5%"的确认：工具把低置信的边界点列出来，配上下文截图，用户几秒钟点一下确认或微调，远比从头手动打点高效。

---

## 9 工程化实践

### 9.1 统一异常体系

所有自定义异常继承自统一的基类 `DramaSplitterError`，按层级组织：

```
DramaSplitterError
├── EnvironmentError
│   ├── FFmpegNotFoundError
│   ├── ChromaprintNotFoundError
│   └── FFprobeError
├── DetectionError
│   ├── AllDetectorsFailedError
│   ├── FrameExtractionError
│   └── InsufficientCandidatesError
├── FusionError
│   └── NoBoundariesError
├── OutputError
│   ├── FFmpegCutError
│   └── InsufficientDiskSpaceError
└── ConfigError
    └── ConfigValidationError
```

每个异常携带上下文信息（文件路径、参数值、原始错误消息），便于日志定位。CLI 层捕获所有 `DramaSplitterError` 子类，转换为用户友好的提示信息，不暴露 traceback。

### 9.2 配置校验

启动时对 `config.yaml` 做以下校验，不合理项直接报错退出：

- `frame_interval_sec > 0`
- `min_episode_sec < max_episode_sec`
- `fingerprint_threshold ∈ [0, 1]`
- `black_pix_threshold ∈ [0, 1]`
- `naming_template` 包含 `{ep}` 占位符
- `ocr_engine ∈ {"rapidocr", "paddleocr"}`
- `cut_mode ∈ {"lossless", "reencode"}`

### 9.3 日志策略

- 使用标准库 `logging`，模块级 logger（`logger = logging.getLogger(__name__)`）
- 日志同时输出到终端（rich 格式化）和文件 `work_dir/run.log`（JSON 格式，便于程序化分析）
- 日志级别：默认 INFO，`-v` 切换为 DEBUG
- 关键事件记录：每路检测器启动/完成（含耗时和候选点数量）、融合各步骤输入输出数量、裁剪每集完成状态
- 不使用 `print` 做日志输出

### 9.4 断点续传

每路检测器完成后立即将结果写入 `work_dir/candidates/<signal_type>.json`，融合结果写入 `work_dir/time_list.json`。工具启动时检查这些文件是否存在：
- 如果某路候选点 JSON 已存在且未损坏，跳过该路检测
- 如果 `time_list.json` 已存在，跳过融合直接进入复核或裁剪
- 通过 CLI 参数 `--fresh` 可强制忽略缓存，重新执行全流程

### 9.5 内存管理

OCR 抽帧的中间产物（1 小时视频 × 0.5 秒间隔 = 7200 帧 PNG）可能占用数 GB 磁盘空间和大量内存。处理策略：
- 帧文件写入磁盘而非全量加载到内存
- OCR 分批处理，单批 ≤ 500 帧，处理完的帧可选择从磁盘删除（通过配置项 `cleanup_frames: true` 控制）
- 音频指纹数组（约 3000 个 32 位整数 ≈ 12KB）体积小，可全量加载

---

## 10 测试策略

### 10.1 测试金字塔

| 层级 | 范围 | 工具 | 覆盖率目标 |
|------|------|------|-----------|
| 单元测试 | 各模块函数/类级别 | pytest | ≥ 80% |
| 集成测试 | 模块间接口（如检测器→融合引擎→裁剪） | pytest | 关键路径全覆盖 |
| 端到端测试 | 完整流程（视频输入→单集输出） | pytest + 合成测试视频 | 核心场景通过 |

### 10.2 测试数据构造

使用 FFmpeg 合成测试视频，避免依赖真实短剧文件：
- 生成 3 段不同颜色的纯色画面（每段 10 秒），段间插入 1 秒黑屏静音
- 用 PIL 生成"第1集""第2集""第3集"标题卡图片，用 FFmpeg `filter_complex` 叠加到每段开头
- 输出约 33 秒的测试视频（`tests/fixtures/test_drama.mp4`）
- 合成脚本放在 `tests/fixtures/generate_test_video.py`，CI 流水线首次运行时自动生成

### 10.3 测试关注点

- **PTS/时间戳**：验证帧时间戳计算正确（帧序号 × 间隔，误差 ≤ 1 帧间隔）
- **指纹相似度**：验证相同指纹相似度为 1.0，完全不同指纹相似度接近 0，部分匹配指纹相似度在 0.7–0.9 之间
- **cn2num**：覆盖 0–999 的边界值（"零"→0，"九十九"→99，"一百"→100，"一百零一"→101，"九百九十九"→999）
- **融合逻辑**：验证多信号命中时置信度提升、单信号命中时置信度降级、缺口补全逻辑正确
- **裁剪输出**：验证输出文件可被 ffprobe 读取，时长与时间清单一致（误差 ≤ 2 秒）
- **错误隔离**：验证某路检测器失败不影响其他路，四路全失败时正确抛出异常

### 10.4 CI 流水线

- 每次 push 运行 `pytest --cov=src/drama_splitter --cov-report=term-missing`
- PR 合并前要求覆盖率 ≥ 80% 且全部测试通过
- ruff 做代码风格检查，不通过则阻止合并
- 定期运行端到端测试（依赖 FFmpeg/fpcalc 环境）

---

## 11 部署与分发

### 11.1 开发环境安装

```bash
git clone <repo_url> drama-splitter
cd drama-splitter
pip install -e ".[dev]"
```

外部依赖：
- FFmpeg 4.4+（含 ffprobe）：macOS `brew install ffmpeg`，Ubuntu `apt install ffmpeg`，Windows 从 ffmpeg.org 下载
- Chromaprint fpcalc（可选）：macOS `brew install chromaprint`，Ubuntu `apt install libchromaprint-tools`

### 11.2 PyInstaller 打包

使用 `--onedir` 模式（单目录，启动更快）。关键配置：

- 入口：`src/drama_splitter/cli.py`
- hidden imports：`rapidocr_onnxruntime`、`pyacoustid`、`scenedetect.detectors.content_detector`、`onnxruntime.capi._pybind_state`
- 动态库收集：使用 `collect_dynamic_libs("onnxruntime")` 确保 ONNX Runtime 的 `.so`/`.dll`/`.dylib` 被正确打包
- 数据文件收集：使用 `collect_data_files("rapidocr_onnxruntime")` 收集 ONNX 模型文件
- excludes：`matplotlib.tests`、`PySide6`、`tkinter`、`pytest`
- 控制台窗口：CLI 模式保留控制台（`console=True`）

打包脚本 `scripts/build.sh`：

```bash
#!/bin/bash
set -e
pip install -e ".[dev]"
pyinstaller drama-splitter.spec --clean --noconfirm
echo "打包完成：dist/drama-splitter/"
```

### 11.3 环境检测

启动时通过 `shutil.which` 检测 FFmpeg、ffprobe、fpcalc 是否可用：
- ffmpeg/ffprobe 不可用 → 报错退出，给出安装提示
- fpcalc 不可用 → 警告但允许继续（音频指纹检测自动跳过）
- RapidOCR 模型文件缺失 → 报错退出，提示重新安装或重新打包

### 11.4 Web UI 部署

Streamlit 应用作为本地服务运行：

```bash
streamlit run src/drama_splitter/ui/app.py --server.port 8501
```

由于 Streamlit 的同步执行模型，长时间运行的检测任务会阻塞页面。解决方案：检测流程通过 `subprocess.Popen` 在后台进程运行，进度写入 `work_dir/progress.json`，Streamlit 页面每 2 秒轮询该文件更新 UI。检测完成后读取结果文件展示。

---

## 12 分阶段实施路径

不要一上来就做全套。先走通最短路径解决 80% 的场景，再逐步加信号、加融合、加界面。每个阶段都能独立可用。

| 阶段 | 目标 | 预计耗时 |
|------|------|----------|
| **阶段 0 · 基础架构** | 项目脚手架、数据模型、FFmpeg 封装 | 1–1.5 天 |
| **阶段 1 · 最短路径** | OCR 标题 + 无损裁剪（抽帧→OCR→匹配→清单→裁剪全链路） | 2–3 天 |
| **阶段 2 · 信号补强** | 音频指纹 + 黑帧静音 + 场景聚类 + 并行调度 | 3–4 天 |
| **阶段 3 · 融合决策** | 候选点聚类 + 集数对齐 + 置信度分级 + 导出 | 2–3 天 |
| **阶段 4 · 体验闭环** | CLI 整合 + Web UI + 打包 + 端到端测试 | 3–4 天 |
| **合计** | | 11–15.5 天 |

阶段 1 的价值在于**用最小成本验证假设**：如果短剧总集篇每集都有清晰的集数标题，单靠 OCR 就能完成大部分分集。如果阶段 1 发现 OCR 漏识别严重，说明需要尽早把音频指纹信号提上来。每个阶段完成后运行端到端冒烟测试，确保前序产出仍然可用。

---

## 13 风险与应对

| 风险 | 表现 | 应对 |
|------|------|------|
| OCR 漏识别/误识别 | 集数标题被特效遮挡、字体花哨、识别成错字 | 多帧投票去噪；模糊匹配集数序列（容忍个别跳号）；降级到音频指纹兜底 |
| 无固定片头 | 音频指纹信号完全无峰值 | 该信号自动跳过，依赖 OCR + 黑帧静音 + 场景聚类 |
| 硬切无过渡 | 黑帧静音信号全部漏检 | 依赖 OCR 集数标题作骨架；场景聚类按时长间距补充候选 |
| 集数标题缺失/无集数 | 标题只写剧名不写集数 | 按顺序自动编号 01/02/03，文件名标注"自动编号" |
| 关键帧对齐误差 | 无损裁剪切口多/少了一两秒 | 默认可接受；高精度需求切换重编码模式 |
| 处理耗时 | 1h+ 视频全量 OCR 较慢 | 抽帧降采样到 0.5–1fps；OCR 用轻量引擎；信号采集层并行化 |
| 内存不足 | 大视频抽帧产物占用过多内存 | 分批处理，单批 ≤ 500 帧；帧文件写磁盘而非全量加载内存 |
| Streamlit 页面冻结 | 检测任务阻塞 UI | 后台进程 + 轮询方案（见部署章节） |
| PyInstaller 打包缺失依赖 | ONNX Runtime 动态库未打包，运行时崩溃 | spec 中显式 collect_dynamic_libs + collect_data_files |
| 非中文短剧 | 标题是英文/其他语言 | 正则同时覆盖 EP.X / Episode X；OCR 引擎本身多语言 |

> **最坏情况兜底**
> 当所有自动信号都不理想时，工具仍应提供一个"手动微调"出口：在时间轴上拖动边界点修正，再触发裁剪。自动化的目标不是消灭人工，而是把人工从"全程打点"压缩到"少量复核"。

---

## 14 参考资料

1. GitHub, skip-intro-credits — 基于 Chromaprint 音频指纹的片头片尾检测，含滑窗相似度算法。 https://github.com/cvija1/skip-intro-credits
2. CSDN, Jellyfin 智能片头片尾跳过系统：基于 Chromaprint 音频指纹识别的自动化方案。 https://blog.csdn.net/gitblog_00217/article/details/151446701
3. GitHub, Chromaprint — 音频指纹库，用于全曲识别、重复音频检测和长流监控。 https://github.com/holeg/chromaprint
4. FFmpeg Cookbook, Detect Black Frames — 用 blackdetect 滤镜定位黑帧段并据此分割视频。 https://ffmpeg-cookbook.com/en/articles/black-detect/
5. GitHub, video-silence-detection — 支持 FFmpeg/Whisper/混合模式的视频静音检测。 https://github.com/hasanhalacli/video-silence-detection
6. PySceneDetect — 基于 Python/OpenCV 的视频场景切换检测工具。 https://www.scenedetect.com/docs/cli.html
