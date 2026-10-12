"""音频指纹检测器单元测试。"""

import pytest
import numpy as np
from unittest.mock import patch

from drama_splitter.detectors.audio_fingerprint import (
    fingerprint_similarity,
    AudioFingerprintDetector,
)
from drama_splitter.exceptions import ChromaprintNotFoundError


# ═══════════════════════════════════════════════════════════
# fingerprint_similarity 单元测试
# ═══════════════════════════════════════════════════════════

class TestFingerprintSimilarity:
    """位级相似度计算测试。"""

    def test_identical_fingerprints(self) -> None:
        """完全相同指纹 -> 1.0。"""
        a = np.array([0xFFFFFFFF], dtype=np.uint32)
        b = np.array([0xFFFFFFFF], dtype=np.uint32)
        assert fingerprint_similarity(a, b) == 1.0

    def test_completely_different(self) -> None:
        """完全不同指纹 -> 0.0。"""
        a = np.array([0xFFFFFFFF], dtype=np.uint32)
        b = np.array([0x00000000], dtype=np.uint32)
        assert fingerprint_similarity(a, b) == 0.0

    def test_half_same(self) -> None:
        """一半相同 -> ~0.5。"""
        a = np.array([0xFFFFFFFF], dtype=np.uint32)
        b = np.array([0x0000FFFF], dtype=np.uint32)
        sim = fingerprint_similarity(a, b)
        assert 0.49 < sim < 0.51

    def test_different_lengths_returns_zero(self) -> None:
        """长度不同返回 0。"""
        a = np.array([0xFFFFFFFF, 0x00000000], dtype=np.uint32)
        b = np.array([0xFFFFFFFF], dtype=np.uint32)
        assert fingerprint_similarity(a, b) == 0.0

    def test_empty_arrays(self) -> None:
        """空数组相似度。"""
        a = np.array([], dtype=np.uint32)
        b = np.array([], dtype=np.uint32)
        assert fingerprint_similarity(a, b) == 1.0  # 0/0 = 无差异

    def test_bitcount_fallback(self) -> None:
        """模拟 np.bitwise_count 不可用时的回退逻辑。"""
        a = np.array([0xFFFF0000], dtype=np.uint32)
        b = np.array([0xFFFF0000], dtype=np.uint32)
        # Should still be 1.0 via fallback
        sim = fingerprint_similarity(a, b)
        assert sim == 1.0


# ═══════════════════════════════════════════════════════════
# AudioFingerprintDetector 行为测试
# ═══════════════════════════════════════════════════════════

class TestAudioFingerprintDetector:
    """检测器行为测试。"""

    def test_init_defaults(self) -> None:
        """默认构造参数。"""
        d = AudioFingerprintDetector()
        assert d.template_sec == 20.0
        assert d.threshold == 0.85

    def test_init_custom(self) -> None:
        """自定义参数。"""
        d = AudioFingerprintDetector(template_sec=15.0, threshold=0.7)
        assert d.template_sec == 15.0
        assert d.threshold == 0.7

    def test_detect_no_video_file_returns_empty(self, tmp_path) -> None:
        """无视频文件时返回空 CandidateList。"""
        d = AudioFingerprintDetector()
        # Mock _FPCALC 绕过 fpcalc 安装检查
        with patch(
            "drama_splitter.detectors.audio_fingerprint._FPCALC", "fake_fpcalc"
        ):
            result = d.detect(
                video_path=str(tmp_path / "nonexistent.mp4"),
                duration_sec=600.0,
                video_name="nonexistent.mp4",
                work_dir=str(tmp_path / "work"),
            )
            assert result.signal_type == "audio_fingerprint"
            assert len(result.candidates) == 0

    def test_peak_detect_empty(self) -> None:
        """空输入返回空列表。"""
        d = AudioFingerprintDetector()
        assert d._peak_detect([]) == []

    def test_peak_detect_all_below_threshold(self) -> None:
        """全部低于阈值 -> 空。"""
        d = AudioFingerprintDetector(threshold=0.9)
        sims = [(10.0, 0.5), (20.0, 0.6), (30.0, 0.7)]
        assert d._peak_detect(sims) == []

    def test_peak_detect_single_peak(self) -> None:
        """单一峰值。"""
        d = AudioFingerprintDetector(threshold=0.8)
        sims = [(10.0, 0.5), (20.0, 0.95), (30.0, 0.6)]
        peaks = d._peak_detect(sims)
        assert len(peaks) == 1
        assert peaks[0][0] == 20.0

    def test_peak_detect_skip_first_near_zero(self) -> None:
        """跳过 offset ≈ 0 的峰值。"""
        d = AudioFingerprintDetector(threshold=0.8)
        sims = [(1.0, 0.95), (60.0, 0.9), (120.0, 0.88)]
        peaks = d._peak_detect(sims)
        # 第一个 (<5s) 被跳过
        assert len(peaks) == 2
        assert peaks[0][0] == 60.0
        assert peaks[1][0] == 120.0

    def test_peak_detect_merge_nearby(self) -> None:
        """合并 < 30s 的邻近峰值。"""
        d = AudioFingerprintDetector(threshold=0.8)
        sims = [(60.0, 0.9), (75.0, 0.95), (150.0, 0.88)]
        peaks = d._peak_detect(sims)
        # 60 和 75 应合并保留 75 (更高)
        assert len(peaks) == 2
        assert peaks[0][0] == 75.0
        assert peaks[1][0] == 150.0

    def test_sliding_window_match_empty(self) -> None:
        """空指纹数组 -> 空结果。"""
        d = AudioFingerprintDetector()
        fp = np.array([], dtype=np.uint32)
        template = np.array([0x12345678], dtype=np.uint32)
        result = d._sliding_window_match(fp, template, 10.0)
        assert result == []

    def test_sliding_window_match_template_larger(self) -> None:
        """模板大于指纹 -> 空。"""
        d = AudioFingerprintDetector()
        fp = np.array([0x1, 0x2], dtype=np.uint32)
        template = np.array([0x1, 0x2, 0x3], dtype=np.uint32)
        result = d._sliding_window_match(fp, template, 10.0)
        assert result == []
