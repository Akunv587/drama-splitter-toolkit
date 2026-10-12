"""信号调度引擎单元测试。"""

import pytest
from unittest.mock import MagicMock, patch

from drama_splitter.detectors.scheduler import SignalScheduler
from drama_splitter.exceptions import DetectionError
from drama_splitter.models.config import AppConfig


class TestSignalScheduler:
    """调度器测试。"""

    @pytest.fixture
    def mock_config(self) -> AppConfig:
        """返回一个最小有效配置。"""
        return AppConfig(
            ocr_engine="rapidocr",
            frame_interval_sec=0.5,
            cluster_window_sec=3.0,
            fingerprint_template_sec=20.0,
            fingerprint_threshold=0.85,
            black_duration=0.3,
            black_pix_threshold=0.10,
            silence_noise="-40dB",
            silence_duration=0.3,
            min_episode_sec=60.0,
            max_episode_sec=300.0,
        )

    def test_all_signals_fail_raises(self, mock_config, tmp_path) -> None:
        """所有信号失败 -> DetectionError。"""
        video = tmp_path / "test.mp4"
        video.write_text("fake")

        scheduler = SignalScheduler(mock_config, str(video), str(tmp_path))
        with patch(
            "drama_splitter.detectors.scheduler.probe_video",
            return_value=MagicMock(duration_sec=600.0, width=1920, height=1080),
        ):
            with patch.object(scheduler, "_run_ocr", return_value=None):
                with patch.object(scheduler, "_run_parallel", return_value={
                    "audio_fingerprint": None,
                    "black_silence": None,
                    "scene_cluster": None,
                }):
                    with pytest.raises(DetectionError):
                        scheduler.run()

    def test_ocr_only_succeeds(self, mock_config, tmp_path) -> None:
        """仅有 OCR 结果 -> 成功。"""
        video = tmp_path / "test.mp4"
        video.write_text("fake")

        from drama_splitter.models import Candidate, CandidateList

        ocr_cl = CandidateList(
            signal_type="ocr",
            video_name="test.mp4",
            duration_sec=600.0,
            candidates=[
                Candidate(
                    signal_type="ocr",
                    timestamp_sec=0.0,
                    episode=1,
                    confidence=0.9,
                ),
                Candidate(
                    signal_type="ocr",
                    timestamp_sec=120.0,
                    episode=2,
                    confidence=0.9,
                ),
            ],
        )

        scheduler = SignalScheduler(mock_config, str(video), str(tmp_path))
        with patch(
            "drama_splitter.detectors.scheduler.probe_video",
            return_value=MagicMock(duration_sec=600.0, width=1920, height=1080),
        ):
            with patch.object(scheduler, "_run_ocr", return_value=ocr_cl):
                with patch.object(scheduler, "_run_parallel", return_value={
                    "audio_fingerprint": None,
                    "black_silence": None,
                    "scene_cluster": None,
                }):
                    results = scheduler.run()
                    assert "ocr" in results
                    assert len(results["ocr"].candidates) == 2
