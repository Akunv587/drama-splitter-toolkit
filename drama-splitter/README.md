# 短剧分集工具 (drama-splitter)

自动识别油管短剧总集篇长视频中的集数边界，将长视频拆分为独立的单集文件。

## 功能特性

- **多信号融合检测**：OCR 标题识别 + 音频指纹片头检测 + 黑帧静音检测 + 场景突变聚类
- **三阶段流水线**：信号采集 → 融合决策 → 批量裁剪
- **置信度分级**：每集标注 high / medium / low 置信度，便于人工复核
- **Web UI**：Streamlit 交互界面，支持时间轴可视化、帧预览、人工微调
- **CLI**：命令行一键执行，`analyze` / `cut` / `run` 三个子命令
- **断点续传**：中间结果自动缓存，支持中断后继续
- **无损裁剪**：默认使用 FFmpeg stream copy，保持原始画质

## 系统要求

- Python 3.10+
- FFmpeg / ffprobe（必需）
- fpcalc（Chromaprint，音频指纹检测需要，可选）

## 安装

```bash
# 从源码安装
git clone <repo-url>
cd drama-splitter
pip install -e .

# 安装开发依赖（含测试工具）
pip install -e ".[dev]"
```

### 外部依赖安装

**FFmpeg**（必需）：
- Windows: `winget install ffmpeg` 或从 [ffmpeg.org](https://ffmpeg.org) 下载
- macOS: `brew install ffmpeg`
- Ubuntu: `sudo apt install ffmpeg`

**fpcalc**（可选，用于音频指纹检测）：
- Windows: 随 [Chromaprint](https://acoustid.org/chromaprint) 安装包提供
- macOS: `brew install chromaprint`
- Ubuntu: `sudo apt install libchromaprint-tools`

## 快速开始

### CLI

```bash
# 一键分析并裁剪
python -m drama_splitter run input.mp4 -y

# 仅分析（生成时间清单）
python -m drama_splitter analyze input.mp4 -o ./work

# 基于时间清单裁剪
python -m drama_splitter cut input.mp4 --time-list ./work/time_list.json -o ./output -y

# 环境检测
python -m drama_splitter env-check
```

### Web UI

```bash
streamlit run src/drama_splitter/ui/app.py
```

然后在浏览器中打开 http://localhost:8501，按步骤操作：
1. **输入**：选择视频文件，调整分析配置
2. **分析**：自动运行信号检测与融合
3. **复核**：查看时间轴，调整边界时间
4. **裁剪**：批量输出单集文件
5. **完成**：打开输出目录

## 配置项

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `ocr_engine` | str | `rapidocr` | OCR 引擎：`rapidocr` / `paddleocr` |
| `frame_interval_sec` | float | `0.5` | 抽帧间隔（秒） |
| `cut_mode` | str | `lossless` | 裁剪模式：`lossless` / `reencode` |
| `naming_template` | str | `第{ep:02d}集.mp4` | 输出文件命名模板 |
| `fingerprint_threshold` | float | `0.7` | 音频指纹相似度阈值 |
| `min_episode_sec` | float | `60` | 最小单集时长（秒） |
| `max_episode_sec` | float | `300` | 最大单集时长（秒） |
| `cluster_window_sec` | float | `3.0` | 候选点聚类窗口（秒） |

## 项目结构

```
drama-splitter/
├── src/drama_splitter/
│   ├── detectors/       # 信号检测层（OCR、音频指纹、黑帧静音、场景聚类）
│   ├── fusion/          # 融合决策引擎（聚类、对齐、评分）
│   ├── models/          # 数据模型（VideoMeta、Candidate、TimeList、Episode）
│   ├── output/          # 批量裁剪输出
│   ├── ui/              # Streamlit Web UI
│   ├── cli.py           # CLI 入口
│   ├── env_check.py     # 环境依赖检测
│   ├── exceptions.py    # 自定义异常
│   └── ffmpeg_wrapper.py # FFmpeg/ffprobe 封装
├── tests/               # 测试套件
│   └── fixtures/        # 测试视频生成脚本
├── scripts/             # 构建脚本
├── drama-splitter.spec  # PyInstaller 打包配置
└── pyproject.toml       # 项目元数据
```

## 开发

```bash
# 安装开发依赖
pip install -e ".[dev]"

# 运行测试
pytest

# 代码检查
ruff check src/

# 生成测试视频
python tests/fixtures/generate_test_video.py

# PyInstaller 打包
pyinstaller drama-splitter.spec --clean --noconfirm
```

## 常见问题

**Q: 为什么提示 "ffmpeg not found"？**  
A: 请确保 FFmpeg 已安装并添加到系统 PATH。运行 `ffmpeg -version` 验证。

**Q: fpcalc 是什么？需要安装吗？**  
A: fpcalc 是 Chromaprint 的音频指纹提取工具，用于片头曲检测。不安装也能使用其他检测方式，只是会跳过音频指纹检测。

**Q: 裁剪速度慢？**  
A: 使用 `lossless` 模式（默认），仅做 stream copy，速度很快。`reencode` 模式需要重新编码。

**Q: 检测结果不准确？**  
A: 使用 Streamlit Web UI 进入复核步骤，可以手动调整每集的起止时间。

**Q: 支持哪些视频格式？**  
A: 所有 FFmpeg 支持的格式，包括 MP4、MKV、TS、MOV、AVI 等。

## 许可证

MIT
