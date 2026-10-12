"""环境依赖检查。

检查 FFmpeg、ffprobe、fpcalc 等命令行工具是否在 PATH 中可用。
"""

import shutil
from typing import Dict

from rich.console import Console
from rich.panel import Panel


def check_environment() -> Dict[str, bool]:
    """检查 FFmpeg、ffprobe、fpcalc 是否可用。

    Returns:
        字典，key 为工具名，value 为是否可用的布尔值。
    """
    tools = ["ffmpeg", "ffprobe", "fpcalc"]
    return {tool: shutil.which(tool) is not None for tool in tools}


_MISSING_HINTS = {
    "ffmpeg": (
        "FFmpeg 是视频处理必需依赖。\n"
        "  macOS:  brew install ffmpeg\n"
        "  Ubuntu: sudo apt install ffmpeg\n"
        "  Windows: 从 https://ffmpeg.org/download.html 下载并添加到 PATH"
    ),
    "ffprobe": (
        "ffprobe 随 FFmpeg 一起安装，用于读取视频信息。\n"
        "  请安装 FFmpeg: https://ffmpeg.org/download.html"
    ),
    "fpcalc": (
        "fpcalc (Chromaprint) 用于音频指纹识别，非必需。\n"
        "  缺失时音频指纹检测会自动跳过。\n"
        "  macOS:  brew install chromaprint\n"
        "  Ubuntu: sudo apt install libchromaprint-tools\n"
        "  Windows: 从 https://acoustid.org/chromaprint 下载"
    ),
}


def print_env_report() -> None:
    """打印环境检测报告，缺失项给出安装提示。"""
    console = Console()
    status = check_environment()

    ok_count = sum(1 for v in status.values() if v)
    total = len(status)

    console.print()
    if ok_count == total:
        console.print(Panel.fit(
            "[green]✓ 所有环境依赖就绪[/green]",
            title="环境检测",
        ))
        return

    lines: list[str] = []
    for tool, available in status.items():
        if available:
            lines.append(f"[green]  ✓ {tool}[/green]")
        else:
            lines.append(f"[red]  ✗ {tool} — 未找到[/red]")
            lines.append(f"    [dim]{_MISSING_HINTS.get(tool, '')}[/dim]")

    console.print(Panel.fit(
        "\n".join(lines),
        title=f"环境检测 ({ok_count}/{total} 通过)",
    ))
