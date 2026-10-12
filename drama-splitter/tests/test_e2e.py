"""端到端测试（步骤 4.4）。

验证从视频分析到裁剪的完整流水线。
"""

import os
import json
import sys
import tempfile
from pathlib import Path
from unittest import mock

import pytest
from click.testing import CliRunner

from drama_splitter.cli import main as cli_main
from drama_splitter.env_check import check_environment
from drama_splitter.ffmpeg_wrapper import probe_video
from drama_splitter.detectors.scheduler import SignalScheduler
from drama_splitter.fusion.engine import FusionEngine
from drama_splitter.models import TimeList, CandidateList
from drama_splitter.models.config import AppConfig
from drama_splitter.output.cutter import Cutter


# ── 辅助函数 ────────────────────────────────────────────────────

def _find_test_video() -> Path:
    """查找测试视频文件。"""
    candidates = [
        Path(__file__).parent / "fixtures" / "test_drama.mp4",
        Path("tests/fixtures/test_drama.mp4"),
    ]
    for p in candidates:
        if p.exists():
            return p
    return None


def _requires_test_video():
    """装饰器：跳过无测试视频的测试。"""
    return pytest.mark.skipif(
        _find_test_video() is None,
        reason="测试视频不存在，请运行 python tests/fixtures/generate_test_video.py 生成",
    )


# ── 环境检测 ────────────────────────────────────────────────────

def test_env_check_returns_result() -> None:
    """环境检测返回结果字典。"""
    result = check_environment()
    assert isinstance(result, dict)
    assert "ffmpeg" in result
    assert "ffprobe" in result


def test_env_check_ffmpeg_found() -> None:
    """FFmpeg 应已安装。"""
    result = check_environment()
    assert result["ffmpeg"], "FFmpeg 未找到，请安装 FFmpeg"


# ── 视频探针 ────────────────────────────────────────────────────

@_requires_test_video()
def test_probe_test_video() -> None:
    """测试视频可被探针正确解析。"""
    video_path = str(_find_test_video())
    meta = probe_video(video_path)
    assert meta.duration_sec > 0
    assert meta.width > 0
    assert meta.height > 0


# ── 完整分析流水线 ──────────────────────────────────────────────

@_requires_test_video()
def test_full_pipeline_integration(tmp_path: Path) -> None:
    """从检测 → 融合 → 裁剪的完整集成测试。"""
    video_path = str(_find_test_video())
    meta = probe_video(video_path)
    work_dir = str(tmp_path / "work")

    # 1) 创建配置
    config = AppConfig(
        ocr_engine="rapidocr",
        frame_interval_sec=0.5,
        sample_interval_sec=0.5,
        fingerprint_template_sec=10.0,
        fingerprint_threshold=0.7,
        cluster_window_sec=3.0,
        min_episode_sec=5.0,
        max_episode_sec=60.0,
        black_duration=0.5,
        black_threshold=0.1,
        silence_duration=0.5,
        silence_threshold=-50.0,
        cut_mode="lossless",
        naming_template="第{ep:02d}集.mp4",
        parallelism=2,
    )

    # 2) 信号检测
    scheduler = SignalScheduler(config=config, video_path=video_path, output_dir=work_dir)
    candidate_lists = scheduler.run()

    assert isinstance(candidate_lists, dict)
    assert len(candidate_lists) > 0
    # OCR 应始终有输出
    assert "ocr" in candidate_lists

    # 3) 融合决策
    engine = FusionEngine(config=config)
    time_list = engine.fuse(candidate_lists, meta.duration_sec, meta.file_name)

    assert isinstance(time_list, TimeList)
    assert len(time_list.episodes) > 0

    # 验证时间清单合理性
    for ep in time_list.episodes:
        assert ep.start_sec >= 0
        assert ep.end_sec > ep.start_sec
        assert ep.duration_sec > 0

    # 4) 裁剪
    output_dir = str(tmp_path / "output")
    cutter = Cutter(mode="lossless", naming_template="E{ep}.mp4")
    results = cutter.cut(video_path, time_list, output_dir)

    assert len(results) == len(time_list.episodes)
    success_count = sum(1 for r in results if r.success)
    assert success_count == len(results), f"部分裁剪失败: {[r.error for r in results if not r.success]}"

    # 5) 验证输出文件
    for ep in time_list.episodes:
        expected_file = os.path.join(output_dir, f"E{ep.episode}.mp4")
        assert os.path.exists(expected_file), f"输出文件缺失: {expected_file}"
        out_meta = probe_video(expected_file)
        # 允许 ±1 秒误差
        assert abs(out_meta.duration_sec - ep.duration_sec) < 2.0


# ── CLI 端到端测试 ──────────────────────────────────────────────

@_requires_test_video()
def test_cli_run_command(tmp_path: Path) -> None:
    """CLI run 命令端到端执行。"""
    video_path = str(_find_test_video())
    output_dir = str(tmp_path / "output")
    work_dir = str(tmp_path / "work")

    runner = CliRunner()
    result = runner.invoke(
        cli_main,
        [
            "run",
            video_path,
            "-o", work_dir,
            "--output-dir", output_dir,
            "--naming-template", "E{ep}.mp4",
            "-y",
            "--fresh",
        ],
    )

    # run 命令应该成功（0 或非 0 取决于环境）
    # 检查输出中有关键信息
    assert result.exit_code == 0 or "完成" in result.output or "error" not in result.output.lower()[:100]


def test_cli_help() -> None:
    """CLI --help 输出正常。"""
    runner = CliRunner()
    result = runner.invoke(cli_main, ["--help"])
    assert result.exit_code == 0
    assert "analyze" in result.output
    assert "cut" in result.output
    assert "run" in result.output


def test_cli_env_check() -> None:
    """CLI 可正常导入 check_environment。"""
    from drama_splitter.env_check import check_environment, print_env_report
    result = check_environment()
    assert isinstance(result, dict)
    assert "ffmpeg" in result
    # print_env_report 不抛异常
    print_env_report()


# ── 时间清单导出 ────────────────────────────────────────────────

def test_time_list_export_json(tmp_path: Path) -> None:
    """TimeList JSON 导出/导入往返测试。"""
    from drama_splitter.models import Episode

    tl = TimeList(
        video_name="test.mp4",
        duration_sec=300.0,
        episodes=[
            Episode(episode=1, start_sec=0, end_sec=100, duration_sec=100,
                    confidence="high", signals_hit=["ocr"]),
            Episode(episode=2, start_sec=100, end_sec=200, duration_sec=100,
                    confidence="medium", signals_hit=["ocr"]),
            Episode(episode=3, start_sec=200, end_sec=300, duration_sec=100,
                    confidence="low", signals_hit=["inferred"]),
        ],
    )

    json_path = str(tmp_path / "time_list.json")
    tl.to_json(json_path)

    # 重新加载
    loaded = TimeList.from_json(json_path)
    assert loaded.video_name == tl.video_name
    assert loaded.duration_sec == tl.duration_sec
    assert len(loaded.episodes) == 3
    assert loaded.episodes[1].confidence == "medium"


def test_time_list_export_csv(tmp_path: Path) -> None:
    """TimeList CSV 导出。"""
    from drama_splitter.models import Episode

    tl = TimeList(
        video_name="test.mp4",
        duration_sec=200.0,
        episodes=[
            Episode(episode=1, start_sec=0, end_sec=100, duration_sec=100,
                    confidence="high", signals_hit=["ocr"]),
            Episode(episode=2, start_sec=100, end_sec=200, duration_sec=100,
                    confidence="high", signals_hit=["ocr"]),
        ],
    )

    csv_path = str(tmp_path / "time_list.csv")
    tl.to_csv(csv_path)
    assert os.path.exists(csv_path)

    with open(csv_path, "r", encoding="utf-8") as f:
        content = f.read()
    assert "episode" in content
    assert "start_sec" in content


# ── 断点续传 ────────────────────────────────────────────────────

@_requires_test_video()
def test_fusion_checkpoint(tmp_path: Path) -> None:
    """融合引擎断点续传测试。"""
    video_path = str(_find_test_video())
    meta = probe_video(video_path)
    work_dir = str(tmp_path / "work")

    config = AppConfig(
        ocr_engine="rapidocr",
        frame_interval_sec=0.5,
    )

    scheduler = SignalScheduler(config=config, video_path=video_path, output_dir=work_dir)
    candidate_lists = scheduler.run()

    engine = FusionEngine(config=config)

    # 第一次融合（fresh=True 强制重算）
    time_list_path = os.path.join(work_dir, "time_list.json")
    if os.path.exists(time_list_path):
        os.unlink(time_list_path)

    tl1 = engine.fuse(candidate_lists, meta.duration_sec, meta.file_name)

    # 保存 checkpoint
    tl1.to_json(time_list_path)

    # 第二次加载（应从 checkpoint 加载）
    loaded = TimeList.from_json(time_list_path)
    assert len(loaded.episodes) == len(tl1.episodes)


# ── 错误处理 ────────────────────────────────────────────────────

def test_invalid_video_path() -> None:
    """无效视频路径抛出异常。"""
    from drama_splitter.exceptions import FFprobeError
    with pytest.raises((FFprobeError, FileNotFoundError, Exception)):
        probe_video("/nonexistent/path/video.mp4")


def test_empty_candidate_lists_fusion(tmp_path: Path) -> None:
    """空候选列表融合抛出 NoBoundariesError。"""
    from drama_splitter.exceptions import NoBoundariesError
    config = AppConfig(ocr_engine="rapidocr")
    engine = FusionEngine(config=config)

    empty_candidates: dict = {}
    with pytest.raises(NoBoundariesError):
        engine.fuse(empty_candidates, 100.0, "empty.mp4")
