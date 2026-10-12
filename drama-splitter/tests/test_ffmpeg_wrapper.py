"""FFmpeg/ffprobe 命令封装层单元测试。"""

import os
import shutil
from pathlib import Path
from unittest import mock

import pytest

from drama_splitter.exceptions import (
    FFmpegCutError,
    FFmpegNotFoundError,
    FFprobeError,
    FrameExtractionError,
)
from drama_splitter.ffmpeg_wrapper import (
    cut_segment,
    detect_black_silence,
    extract_audio,
    extract_frames,
    probe_video,
)
from drama_splitter.models import VideoMeta


# ── probe_video ────────────────────────────────────────────────

def test_probe_video_returns_valid_meta(test_video_path: Path) -> None:
    meta = probe_video(str(test_video_path))
    assert isinstance(meta, VideoMeta)
    assert meta.file_name == "test.mp4"
    assert meta.duration_sec > 0
    assert meta.width == 320
    assert meta.height == 240
    assert meta.fps > 0
    assert meta.file_size_mb > 0


def test_probe_video_nonexistent_file() -> None:
    with pytest.raises(FFprobeError):
        probe_video("/nonexistent/video.mp4")


# ── extract_frames ─────────────────────────────────────────────

def test_extract_frames_count(test_video_path: Path, tmp_path: Path) -> None:
    interval = 1.0
    frames = extract_frames(str(test_video_path), str(tmp_path), interval_sec=interval)
    expected = test_video_path.stat().st_size  # dummy; use real calc
    # 10 秒视频, 1 秒间隔 → 约 10 帧（允许 ±2 误差）
    duration = probe_video(str(test_video_path)).duration_sec
    expected_count = int(duration / interval)
    assert abs(len(frames) - expected_count) <= 2


def test_extract_frames_timestamp_calculation(test_video_path: Path, tmp_path: Path) -> None:
    interval = 0.5
    frames = extract_frames(str(test_video_path), str(tmp_path), interval_sec=interval)
    for i, (path, ts) in enumerate(frames):
        expected_ts = i * interval
        assert abs(ts - expected_ts) <= interval, f"Frame {i}: ts={ts}, expected≈{expected_ts}"
        assert os.path.exists(path)


def test_extract_frames_nonexistent_file(tmp_path: Path) -> None:
    with pytest.raises(FrameExtractionError):
        extract_frames("/nonexistent/video.mp4", str(tmp_path))


# ── extract_audio ──────────────────────────────────────────────

def test_extract_audio(test_video_path: Path, tmp_path: Path) -> None:
    # 测试视频可能无音轨，先检查
    meta = probe_video(str(test_video_path))
    # testsrc 生成的视频无音频，跳过
    wav_path = tmp_path / "audio.wav"
    try:
        result = extract_audio(str(test_video_path), str(wav_path))
    except FFprobeError as e:
        if "音轨" in str(e):
            pytest.skip("测试视频无音轨")
        raise
    assert result == str(wav_path)
    assert wav_path.exists()
    assert wav_path.stat().st_size > 0


# ── detect_black_silence ───────────────────────────────────────

def test_detect_black_silence_returns_correct_structure(test_video_path: Path) -> None:
    result = detect_black_silence(str(test_video_path))
    assert "black_segments" in result
    assert "silence_segments" in result
    assert isinstance(result["black_segments"], list)
    assert isinstance(result["silence_segments"], list)


# ── cut_segment ────────────────────────────────────────────────

def test_cut_segment_lossless(test_video_path: Path, tmp_path: Path) -> None:
    output = tmp_path / "cut_lossless.mp4"
    result = cut_segment(str(test_video_path), 0.0, 5.0, str(output), mode="lossless")
    assert result == str(output)
    assert output.exists()
    assert output.stat().st_size > 0


def test_cut_segment_reencode(test_video_path: Path, tmp_path: Path) -> None:
    output = tmp_path / "cut_reencode.mp4"
    result = cut_segment(str(test_video_path), 2.0, 7.0, str(output), mode="reencode")
    assert output.exists()
    assert output.stat().st_size > 0


def test_cut_segment_invalid_mode(test_video_path: Path, tmp_path: Path) -> None:
    output = tmp_path / "bad.mp4"
    with pytest.raises(FFmpegCutError, match="未知裁剪模式"):
        cut_segment(str(test_video_path), 0.0, 1.0, str(output), mode="invalid")  # type: ignore[arg-type]


def test_cut_segment_invalid_time(test_video_path: Path, tmp_path: Path) -> None:
    output = tmp_path / "bad2.mp4"
    with pytest.raises(FFmpegCutError, match="无效时间区间"):
        cut_segment(str(test_video_path), 10.0, 5.0, str(output))


# ── 环境检测 ───────────────────────────────────────────────────

def test_ffmpeg_not_found_probe() -> None:
    with mock.patch("drama_splitter.ffmpeg_wrapper._FFPROBE", None):
        with pytest.raises(FFmpegNotFoundError):
            probe_video("any.mp4")


def test_ffmpeg_not_found_cut() -> None:
    with mock.patch("drama_splitter.ffmpeg_wrapper._FFMPEG", None):
        with pytest.raises(FFmpegNotFoundError):
            cut_segment("any.mp4", 0.0, 1.0, "out.mp4")
