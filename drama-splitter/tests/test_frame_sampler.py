"""视频抽帧模块单元测试。"""

from pathlib import Path

import pytest

from drama_splitter.detectors.frame_sampler import Frame, FrameSampler
from drama_splitter.exceptions import FFprobeError, FrameExtractionError
from drama_splitter.ffmpeg_wrapper import probe_video


def test_sample_returns_frames(test_video_path: Path, tmp_path: Path) -> None:
    sampler = FrameSampler(interval_sec=1.0, frame_width=320)
    frames = sampler.sample(str(test_video_path), str(tmp_path))
    assert len(frames) > 0
    assert all(isinstance(f, Frame) for f in frames)
    assert all(f.timestamp_sec >= 0 for f in frames)


def test_frames_sorted_by_timestamp(test_video_path: Path, tmp_path: Path) -> None:
    sampler = FrameSampler(interval_sec=0.5, frame_width=320)
    frames = sampler.sample(str(test_video_path), str(tmp_path))
    timestamps = [f.timestamp_sec for f in frames]
    assert timestamps == sorted(timestamps)


def test_frame_count_approx_duration_over_interval(test_video_path: Path, tmp_path: Path) -> None:
    interval = 1.0
    sampler = FrameSampler(interval_sec=interval, frame_width=320)
    frames = sampler.sample(str(test_video_path), str(tmp_path))
    meta = probe_video(str(test_video_path))
    expected = int(meta.duration_sec / interval)
    assert abs(len(frames) - expected) <= 2


def test_timestamp_approx_index_times_interval(test_video_path: Path, tmp_path: Path) -> None:
    interval = 0.5
    sampler = FrameSampler(interval_sec=interval, frame_width=320)
    frames = sampler.sample(str(test_video_path), str(tmp_path))
    for i, f in enumerate(frames):
        expected_ts = i * interval
        assert abs(f.timestamp_sec - expected_ts) <= interval


def test_frame_paths_exist(test_video_path: Path, tmp_path: Path) -> None:
    sampler = FrameSampler(interval_sec=1.0, frame_width=320)
    frames = sampler.sample(str(test_video_path), str(tmp_path))
    for f in frames:
        assert Path(f.path).exists()


def test_nonexistent_video(tmp_path: Path) -> None:
    sampler = FrameSampler()
    # probe_video 对不存在文件先抛 FFprobeError
    with pytest.raises(FFprobeError):
        sampler.sample("/nonexistent/video.mp4", str(tmp_path))
