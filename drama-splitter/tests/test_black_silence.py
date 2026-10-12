"""黑帧静音检测器单元测试。"""

import pytest
from unittest.mock import patch, MagicMock

from drama_splitter.detectors.black_silence import BlackSilenceDetector
from drama_splitter.models import CandidateList


class TestBlackSilenceDetector:
    """黑帧静音检测器测试。"""

    def test_init_defaults(self) -> None:
        """默认参数。"""
        d = BlackSilenceDetector()
        assert d.black_duration == 0.3
        assert d.black_pix_threshold == 0.10
        assert d.silence_noise == "-40dB"
        assert d.silence_duration == 0.3

    def test_init_custom(self) -> None:
        """自定义参数。"""
        d = BlackSilenceDetector(
            black_duration=0.5,
            black_pix_threshold=0.05,
            silence_noise="-50dB",
            silence_duration=0.5,
        )
        assert d.black_duration == 0.5
        assert d.black_pix_threshold == 0.05
        assert d.silence_noise == "-50dB"
        assert d.silence_duration == 0.5

    def test_detect_no_segments(self) -> None:
        """无黑帧静音段 -> 空候选列表。"""
        d = BlackSilenceDetector()
        with patch(
            "drama_splitter.detectors.black_silence.detect_black_silence",
            return_value={"black_segments": [], "silence_segments": []},
        ):
            result = d.detect("test.mp4", 600.0, "test.mp4")
            assert isinstance(result, CandidateList)
            assert result.signal_type == "black_silence"
            assert len(result.candidates) == 0

    def test_detect_intersection(self) -> None:
        """黑帧和静音有交集 -> 生成候选点。"""
        d = BlackSilenceDetector()
        with patch(
            "drama_splitter.detectors.black_silence.detect_black_silence",
            return_value={
                "black_segments": [(60.0, 62.0)],
                "silence_segments": [(61.0, 63.0)],
            },
        ):
            result = d.detect("test.mp4", 600.0, "test.mp4")
            assert len(result.candidates) >= 1
            c = result.candidates[0]
            assert c.signal_type == "black_silence"
            assert 61.0 <= c.timestamp_sec <= 61.5
            assert "black_start" in c.metadata
            assert "silence_start" in c.metadata

    def test_detect_degraded_mode_black_only(self) -> None:
        """无交集但有黑帧 -> 降级模式。"""
        d = BlackSilenceDetector()
        with patch(
            "drama_splitter.detectors.black_silence.detect_black_silence",
            return_value={
                "black_segments": [(120.0, 121.0)],
                "silence_segments": [],
            },
        ):
            result = d.detect("test.mp4", 600.0, "test.mp4")
            assert len(result.candidates) >= 1
            c = result.candidates[0]
            assert c.metadata.get("degraded") is True
            assert c.metadata.get("source") == "black_only"

    def test_detect_degraded_mode_silence_only(self) -> None:
        """无交集但有静音 -> 降级模式。"""
        d = BlackSilenceDetector()
        with patch(
            "drama_splitter.detectors.black_silence.detect_black_silence",
            return_value={
                "black_segments": [],
                "silence_segments": [(120.0, 121.0)],
            },
        ):
            result = d.detect("test.mp4", 600.0, "test.mp4")
            assert len(result.candidates) >= 1
            c = result.candidates[0]
            assert c.metadata.get("degraded") is True
            assert c.metadata.get("source") == "silence_only"

    def test_detect_filters_boundary(self) -> None:
        """过滤位于视频首尾 1s 内的候选点。"""
        d = BlackSilenceDetector()
        with patch(
            "drama_splitter.detectors.black_silence.detect_black_silence",
            return_value={
                "black_segments": [(0.0, 1.0), (300.0, 301.0), (599.0, 600.0)],
                "silence_segments": [(0.0, 1.0), (300.0, 301.0), (599.0, 600.0)],
            },
        ):
            result = d.detect("test.mp4", 600.0, "test.mp4")
            # 首尾的被过滤，只保留 300.5 附近的
            ts_list = [c.timestamp_sec for c in result.candidates]
            assert not any(ts < 1.0 for ts in ts_list)
            assert not any(ts > 599.0 for ts in ts_list)

    def test_detect_merge_nearby(self) -> None:
        """合并距离 < 3s 的候选点。"""
        d = BlackSilenceDetector()
        with patch(
            "drama_splitter.detectors.black_silence.detect_black_silence",
            return_value={
                "black_segments": [(120.0, 121.0), (122.0, 123.0)],
                "silence_segments": [(120.0, 121.0), (122.0, 123.0)],
            },
        ):
            result = d.detect("test.mp4", 600.0, "test.mp4")
            # 两个接近的候选点应该合并为一个
            assert len(result.candidates) == 1
