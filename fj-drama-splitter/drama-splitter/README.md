# 短剧总集篇自动分集工具

将 YouTube 下载的短剧总集篇（1小时以上长视频）自动拆分成单集视频。

## 功能特性

- **三重信号交叉验证**：视频闪白检测 + 音频静音检测 + 字幕间隙分析
- **置信度标记**：2+ 信号吻合 = 🟢 HIGH，1 信号 = 🔴 LOW（无 MEDIUM）
- **台词防串集**：切割点自动避开台词中间
- **人工确认**：支持查看、编辑、增删分割点
- **无损裁剪**：ffmpeg `-c copy` 流拷贝模式，速度快、无画质损失
- **并发处理**：多集同时裁剪，充分利用 CPU

## 环境要求

- Python 3.10+
- ffmpeg（需单独安装并加入 PATH）

### 安装 ffmpeg

- 下载地址: https://ffmpeg.org/download.html
- Windows 教程: https://www.wikihow.com/Install-FFmpeg-on-Windows

## 安装步骤

```bash
cd drama-splitter
pip install -r requirements.txt
```

## 使用方法

```bash
python main.py
```

启动后自动打开浏览器访问 `http://127.0.0.1:7860`。

### 使用流程

1. **上传与分析**：上传 mp4 视频（必填）、mp3 音频（可选）、字幕文件 txt/vtt（可选），填写剧名后点击"开始自动分析"
2. **确认与编辑**：查看分集清单，置信度标记（🟢 HIGH / 🔴 LOW），台词冲突标记（❌），可编辑修改分割点
3. **裁剪与输出**：确认后一键裁剪，输出到 `outputs/{剧名}/` 目录

### 人工输入模式

在"上传与分析"页面直接粘贴起始时间列表（格式：`00:00:00, 00:02:30, 00:05:00`），系统自动生成分集清单并检测台词冲突。

## 配置参数

所有参数定义在 `config.py` 的 `AppConfig` dataclass 中，支持通过环境变量覆盖：

| 环境变量 | 默认值 | 说明 |
|---------|--------|------|
| `DRAMA_WHITE_THRESHOLD` | 220 | 闪白亮度阈值 |
| `DRAMA_SPIKE_THRESHOLD` | 80 | 亮度骤升阈值 |
| `DRAMA_SILENCE_THRESHOLD` | 0.01 | 静音 RMS 阈值 |
| `DRAMA_MATCH_WINDOW` | 3.0 | 信号匹配窗口（秒） |
| `DRAMA_MIN_EPISODE_DURATION` | 30.0 | 最小集时长（秒） |
| `DRAMA_SERVER_PORT` | 7860 | Web 服务端口 |

## 项目结构

```
drama-splitter/
├── main.py                  # 启动入口
├── config.py                # 配置管理
├── modules/                 # 核心模块
│   ├── models.py            # 数据模型
│   ├── exceptions.py        # 自定义异常
│   ├── flash_detector.py    # 闪白检测
│   ├── silence_detector.py  # 静音检测
│   ├── subtitle_parser.py   # 字幕解析
│   ├── cross_validator.py   # 交叉验证
│   ├── cutter.py            # 视频裁剪
│   ├── file_manager.py      # 文件管理
│   └── task_queue.py        # 任务队列
├── web/                     # 前端界面
│   ├── app.py               # Gradio 应用
│   └── components.py        # UI 组件
├── utils/                   # 工具模块
│   ├── time_utils.py        # 时间转换
│   ├── ffmpeg_check.py      # ffmpeg 检测
│   └── logger.py            # 日志配置
├── tests/                   # 测试
├── outputs/                 # 裁剪输出
├── uploads/                 # 上传暂存
└── logs/                    # 日志
```

## 运行测试

```bash
pytest tests/ -v
pytest tests/ --cov=modules --cov=utils
```

## 已知限制

1. 闪白检测不适用无闪白过渡的剧集
2. 裁剪精度受关键帧对齐影响（约 ±2-3 秒），但切割点在静默区间内不影响体验
3. 无 GPU 加速，1 小时视频分析约需 3-5 分钟
4. 任务状态保存在内存中，重启后丢失

## 后续优化方向

- 场景切换检测作为辅助信号
- 自适应阈值（根据视频/音频特征动态调整）
- 帧预览 + 音频波形可视化
- 任务持久化（SQLite）
- VTT 逐词时间戳深入利用
