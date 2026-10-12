"""CLI 入口模块。

使用 Click 框架提供命令行接口，支持三个子命令：
  analyze  — 检测 + 融合，输出时间清单
  cut      — 读取时间清单执行裁剪
  run      — 一键执行全流程
"""

import os
import sys
from pathlib import Path

import click
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich.table import Table

from drama_splitter import __version__
from drama_splitter.env_check import check_environment, print_env_report
from drama_splitter.exceptions import DramaSplitterError
from drama_splitter.ffmpeg_wrapper import probe_video
from drama_splitter.fusion.engine import FusionEngine
from drama_splitter.logging_config import setup_logging
from drama_splitter.models import TimeList
from drama_splitter.models.config import AppConfig
from drama_splitter.output.cutter import Cutter
from drama_splitter.detectors.scheduler import SignalScheduler

# DRAMA_SPLITTER_FORCE_TERMINAL 环境变量为 1 时强制 Rich 进入终端模式
# 用于 Streamlit UI 子进程中保持实时输出
console = Console(
    force_terminal=os.environ.get("DRAMA_SPLITTER_FORCE_TERMINAL") == "1"
)


@click.group()
@click.version_option(version=__version__, prog_name="drama-splitter")
@click.option(
    "--verbose", "-v", is_flag=True, help="启用 DEBUG 日志级别。"
)
@click.pass_context
def main(ctx: click.Context, verbose: bool) -> None:
    """短剧总集篇自动分集工具。

    将油管短剧总集篇长视频自动拆分为单集独立文件。
    """
    ctx.ensure_object(dict)
    ctx.obj["verbose"] = verbose
    log_dir = str(Path.cwd() / "output")
    setup_logging(log_dir, verbose=verbose)


@main.command()
@click.argument("video", type=click.Path(exists=True))
@click.option(
    "--config", "-c", "config_path", default="config.yaml",
    type=click.Path(exists=True), help="配置文件路径 (默认 config.yaml)"
)
@click.option(
    "--output", "-o", "output_dir", default="./output",
    type=click.Path(), help="输出目录 (默认 ./output)"
)
@click.option("--fresh", is_flag=True, help="强制忽略缓存，重新执行全流程")
@click.option("--naming-template", default=None, help="输出文件命名模板，如 E{ep:02d}.mp4")
@click.pass_context
def analyze(
    ctx: click.Context,
    video: str,
    config_path: str,
    output_dir: str,
    fresh: bool,
    naming_template: str | None,
) -> None:
    """检测 + 融合——输出时间清单 (JSON)。

    VIDEO 为输入的短剧总集篇视频文件路径。
    """
    # ── 环境检查 ──
    env = check_environment()
    if not env["ffmpeg"]:
        print_env_report()
        raise click.ClickException("FFmpeg 未安装，无法继续。")
    if not env["ffprobe"]:
        print_env_report()
        raise click.ClickException("ffprobe 未安装，无法继续。")

    try:
        # ── 加载配置 ──
        config = AppConfig.from_yaml(config_path)
        if naming_template:
            config = config.model_copy(update={"naming_template": naming_template})
        config.validate()

        # ── 视频探测 ──
        console.print(f"\n[bold]输入视频:[/bold] {video}")
        meta = probe_video(video)
        console.print(
            f"[dim]{meta.width}x{meta.height}, {meta.duration_sec:.1f}s, "
            f"{meta.file_size_mb:.1f}MB[/dim]"
        )

        # ── 工作目录 ──
        video_name = os.path.splitext(os.path.basename(video))[0]
        work_dir = os.path.join(output_dir, video_name)
        os.makedirs(work_dir, exist_ok=True)

        # ── 信号采集 ──
        console.print()
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            TimeElapsedColumn(),
            console=console,
        ) as progress:
            task = progress.add_task("[cyan]信号采集中...", total=None)
            scheduler = SignalScheduler(config, video, work_dir)
            candidate_lists = scheduler.run()
            progress.update(task, description="[green]信号采集完成")

        # ── 融合 ──
        console.print()
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            TimeElapsedColumn(),
            console=console,
        ) as progress:
            task = progress.add_task("[cyan]融合决策中...", total=None)
            engine = FusionEngine(config)
            time_list = engine.fuse(
                candidate_lists, meta.duration_sec, video_name,
                work_dir=work_dir, fresh=fresh,
            )
            progress.update(task, description="[green]融合决策完成")

        # ── 输出 ──
        _print_time_list_summary(time_list)
        _print_low_confidence_warning(time_list)

        output_path = os.path.join(work_dir, "time_list.json")
        time_list.to_json(output_path)
        console.print(f"\n[green]✓ 时间清单已保存至: {output_path}[/green]")

    except DramaSplitterError as e:
        console.print(f"\n[red]错误: {e}[/red]")
        raise SystemExit(1) from e
    except Exception as e:
        if ctx.obj.get("verbose"):
            raise
        console.print(f"\n[red]未预期的错误: {e}[/red]")
        raise SystemExit(1) from e


@main.command()
@click.argument("video", type=click.Path(exists=True))
@click.option(
    "--time-list", "-t", "time_list_path", required=True,
    type=click.Path(exists=True), help="时间清单 JSON 文件路径"
)
@click.option(
    "--output", "-o", "output_dir", default="./output",
    type=click.Path(), help="输出目录 (默认 ./output)"
)
@click.option(
    "--mode", default="lossless",
    type=click.Choice(["lossless", "reencode"]), help="裁剪模式"
)
@click.option(
    "--naming-template", default="E{ep:02d}.mp4",
    help="输出文件命名模板"
)
@click.option("--yes", "-y", is_flag=True, help="跳过确认提示")
@click.pass_context
def cut(
    ctx: click.Context,
    video: str,
    time_list_path: str,
    output_dir: str,
    mode: str,
    naming_template: str,
    yes: bool,
) -> None:
    """读取时间清单执行裁剪。

    VIDEO 为输入的短剧总集篇视频文件路径。
    """
    env = check_environment()
    if not env["ffmpeg"]:
        print_env_report()
        raise click.ClickException("FFmpeg 未安装，无法继续。")

    try:
        # ── 加载时间清单 ──
        time_list = TimeList.from_json(time_list_path)
        _print_time_list_summary(time_list)

        # ── 确认 ──
        if not yes:
            click.confirm(
                f"即将裁剪为 {len(time_list.episodes)} 个文件，是否继续？",
                abort=True,
            )

        # ── 裁剪 ──
        cutter = Cutter(mode=mode, naming_template=naming_template)
        console.print(f"\n[cyan]开始裁剪 ({mode})...[/cyan]")

        cutter.cut(video, time_list, output_dir)

        console.print(f"\n[green]✓ 裁剪完成，文件保存在: {output_dir}[/green]")

    except DramaSplitterError as e:
        console.print(f"\n[red]错误: {e}[/red]")
        raise SystemExit(1) from e
    except Exception as e:
        if ctx.obj.get("verbose"):
            raise
        console.print(f"\n[red]未预期的错误: {e}[/red]")
        raise SystemExit(1) from e


@main.command()
@click.argument("video", type=click.Path(exists=True))
@click.option(
    "--config", "-c", "config_path", default="config.yaml",
    type=click.Path(exists=True), help="配置文件路径 (默认 config.yaml)"
)
@click.option(
    "--output", "-o", "output_dir", default="./output",
    type=click.Path(), help="输出目录 (默认 ./output)"
)
@click.option("--fresh", is_flag=True, help="强制忽略缓存，重新执行全流程")
@click.option(
    "--naming-template", default=None, help="输出文件命名模板"
)
@click.option("--yes", "-y", is_flag=True, help="跳过确认提示")
@click.pass_context
def run(
    ctx: click.Context,
    video: str,
    config_path: str,
    output_dir: str,
    fresh: bool,
    naming_template: str | None,
    yes: bool,
) -> None:
    """一键执行全流程（检测 + 融合 + 裁剪）。

    VIDEO 为输入的短剧总集篇视频文件路径。
    """
    env = check_environment()
    if not env["ffmpeg"]:
        print_env_report()
        raise click.ClickException("FFmpeg 未安装，无法继续。")
    if not env["ffprobe"]:
        print_env_report()
        raise click.ClickException("ffprobe 未安装，无法继续。")

    try:
        # ── 加载配置 ──
        config = AppConfig.from_yaml(config_path)
        if naming_template:
            config = config.model_copy(update={"naming_template": naming_template})
        config.validate()

        # ── 视频探测 ──
        console.print(f"\n[bold]输入视频:[/bold] {video}")
        meta = probe_video(video)
        console.print(
            f"[dim]{meta.width}x{meta.height}, {meta.duration_sec:.1f}s, "
            f"{meta.file_size_mb:.1f}MB[/dim]"
        )

        video_name = os.path.splitext(os.path.basename(video))[0]
        work_dir = os.path.join(output_dir, video_name)
        os.makedirs(work_dir, exist_ok=True)

        # ── 信号采集 ──
        console.print()
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            TimeElapsedColumn(),
            console=console,
        ) as progress:
            task = progress.add_task("[cyan]信号采集中...", total=None)
            scheduler = SignalScheduler(config, video, work_dir)
            candidate_lists = scheduler.run()
            progress.update(task, description="[green]信号采集完成")

        # ── 融合 ──
        console.print()
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            TimeElapsedColumn(),
            console=console,
        ) as progress:
            task = progress.add_task("[cyan]融合决策中...", total=None)
            engine = FusionEngine(config)
            time_list = engine.fuse(
                candidate_lists, meta.duration_sec, video_name,
                work_dir=work_dir, fresh=fresh,
            )
            progress.update(task, description="[green]融合决策完成")

        # ── 展示摘要 ──
        _print_time_list_summary(time_list)
        _print_low_confidence_warning(time_list)

        # ── 确认裁剪 ──
        if not yes:
            click.confirm(
                f"\n即将裁剪为 {len(time_list.episodes)} 个文件，是否继续？",
                abort=True,
            )

        # ── 裁剪 ──
        cutter = Cutter(
            mode=config.cut_mode,
            naming_template=config.naming_template,
        )
        console.print(f"\n[cyan]开始裁剪 ({config.cut_mode})...[/cyan]")

        cutter.cut(video, time_list, output_dir)

        console.print(f"\n[green]✓ 全部完成！文件保存在: {output_dir}[/green]")

    except DramaSplitterError as e:
        console.print(f"\n[red]错误: {e}[/red]")
        raise SystemExit(1) from e
    except Exception as e:
        if ctx.obj.get("verbose"):
            raise
        console.print(f"\n[red]未预期的错误: {e}[/red]")
        raise SystemExit(1) from e


# ── 辅助函数 ──────────────────────────────────────────────

def _print_time_list_summary(time_list: TimeList) -> None:
    """用 rich Table 打印时间清单摘要。"""
    high = sum(1 for e in time_list.episodes if e.confidence == "high")
    medium = sum(1 for e in time_list.episodes if e.confidence == "medium")
    low = sum(1 for e in time_list.episodes if e.confidence == "low")

    confidence_style = {
        "high": "[green]● high[/green]",
        "medium": "[yellow]○ medium[/yellow]",
        "low": "[red]△ low[/red]",
    }

    table = Table(title=f"时间清单 — {len(time_list.episodes)} 集")
    table.add_column("集数", justify="right", style="cyan")
    table.add_column("开始", justify="right")
    table.add_column("结束", justify="right")
    table.add_column("时长", justify="right")
    table.add_column("置信度")
    table.add_column("信号")

    for ep in time_list.episodes:
        start_str = f"{int(ep.start_sec // 60):d}:{int(ep.start_sec % 60):02d}"
        end_str = f"{int(ep.end_sec // 60):d}:{int(ep.end_sec % 60):02d}"
        dur_str = f"{int(ep.duration_sec // 60):d}:{int(ep.duration_sec % 60):02d}"
        conf_str = confidence_style.get(ep.confidence, ep.confidence)
        signals_str = ", ".join(ep.signals_hit[:3])

        table.add_row(
            str(ep.episode), start_str, end_str, dur_str,
            conf_str, signals_str,
        )

    console.print()
    console.print(table)

    # 统计摘要
    console.print(
        f"[bold]置信度分布:[/bold] "
        f"[green]高 {high}[/green] / "
        f"[yellow]中 {medium}[/yellow] / "
        f"[red]低 {low}[/red]"
    )


def _print_low_confidence_warning(time_list: TimeList) -> None:
    """打印低置信度集的警告提示。"""
    low_eps = [e for e in time_list.episodes if e.confidence == "low"]
    if low_eps:
        episodes_str = ", ".join(str(e.episode) for e in low_eps)
        console.print(
            f"\n[yellow]⚠ 以下集数置信度较低，建议复核: 第 {episodes_str} 集[/yellow]"
        )


if __name__ == "__main__":
    main()
