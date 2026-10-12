# 短剧分集工具链 (drama-splitter-toolkit)

自动识别短剧「总集篇」长视频中的**集数边界**，并将其**无损拆分**为独立单集文件的工具。

## 目录

| 目录 | 说明 |
| --- | --- |
| `drama-splitter` | **主实现**。完整源码 + 单元测试 + 技术方案文档，可通过 `pip install -e .` 安装，提供 CLI 与 Streamlit Web UI |
| `fj-drama-splitter` | **早期版本（FJ）**。内含 `drama-splitter/` 子目录（与上一目录同源的副本）、需求文档 PRD、技术文档 TDD 与开发提示词 |

> 两个目录是同一工具在不同阶段的版本，按原始目录结构**原样保留**，未做合并。

## 核心能力

- **多信号融合检测** —— OCR 标题识别 + 音频指纹片头检测 + 黑帧/静音检测 + 场景突变聚类
- **三阶段流水线** —— 信号采集 → 融合决策 → 批量裁剪
- **置信度分级** —— 每集标注 `high` / `medium` / `low`，便于人工复核
- **无损裁剪** —— 默认使用 FFmpeg stream copy，保持原始画质
- **断点续传** —— 中间结果自动缓存，中断后可继续
- **双入口** —— CLI（`analyze` / `cut` / `run`）与 Streamlit 可视化界面

## 系统要求

- Python 3.10+
- **FFmpeg / ffprobe**（必需）
- **fpcalc**（Chromaprint，音频指纹检测需要，可选）

## 快速开始

```bash
cd drama-splitter
pip install -e .            # 或 pip install -e ".[dev]" 安装开发依赖
```

外部依赖：

```bash
# FFmpeg
winget install ffmpeg        # Windows
brew install ffmpeg          # macOS
sudo apt install ffmpeg      # Ubuntu
```

## 文档

- `drama-splitter/README.md` —— 主实现的详细说明
- `drama-splitter/drama-splitter-prd-final.md` —— 产品需求文档
- `drama-splitter/drama-splitter-tech-proposal-final.md` —— 技术方案
- `fj-drama-splitter/需求文档_PRD_v3.md` / `技术文档_TDD_v3.md` —— 早期版本设计文档

---

本仓库为**脱敏发布版**：已剔除测试视频、音频、字幕、裁剪输出、上传缓存、日志、
虚拟环境与个人本机路径。
