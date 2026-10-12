"""交叉验证模块单元测试

覆盖所有信号组合和置信度评定场景。
"""

import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from modules.cross_validator import CrossValidator
from modules.models import MergedPoint, EpisodeSegment, ConfidenceLevel, SubtitleItem


class TestCrossValidatorValidate:
    """测试 validate() 方法"""

    def setup_method(self):
        self.validator = CrossValidator(match_window=3.0, min_episode_duration=30.0)

    def test_three_signals_high(self):
        """三个信号吻合 → HIGH"""
        flash = [100.0, 200.0, 300.0]
        silence = [100.5, 200.5, 300.5]
        gaps = [(98.0, 102.0), (198.0, 202.0), (298.0, 302.0)]

        result = self.validator.validate(flash, silence, gaps, 400.0)
        assert len(result) == 3
        for mp in result:
            assert mp.confidence == ConfidenceLevel.HIGH
            assert len(mp.sources) == 3

    def test_flash_silence_high(self):
        """闪白+静音两个信号吻合 → HIGH"""
        flash = [100.0, 200.0]
        silence = [100.5, 200.5]

        result = self.validator.validate(flash, silence, None, 300.0)
        assert len(result) == 2
        for mp in result:
            assert mp.confidence == ConfidenceLevel.HIGH
            assert "flash" in mp.sources
            assert "silence" in mp.sources

    def test_flash_subtitle_high(self):
        """闪白+字幕两个信号吻合 → HIGH"""
        flash = [100.0, 200.0]
        gaps = [(98.0, 102.0), (198.0, 202.0)]

        result = self.validator.validate(flash, [], gaps, 300.0)
        assert len(result) == 2
        for mp in result:
            assert mp.confidence == ConfidenceLevel.HIGH
            assert "flash" in mp.sources
            assert "subtitle" in mp.sources

    def test_silence_subtitle_high(self):
        """静音+字幕两个信号吻合 → HIGH"""
        silence = [100.0, 200.0]
        gaps = [(98.0, 102.0), (198.0, 202.0)]

        result = self.validator.validate([], silence, gaps, 300.0)
        assert len(result) == 2
        for mp in result:
            assert mp.confidence == ConfidenceLevel.HIGH
            assert "silence" in mp.sources
            assert "subtitle" in mp.sources

    def test_flash_only_low(self):
        """仅闪白信号 → LOW"""
        flash = [100.0, 200.0]

        result = self.validator.validate(flash, [], None, 300.0)
        assert len(result) == 2
        for mp in result:
            assert mp.confidence == ConfidenceLevel.LOW
            assert mp.sources == ["flash"]

    def test_silence_only_low(self):
        """仅静音信号 → LOW"""
        silence = [100.0, 200.0]

        result = self.validator.validate([], silence, None, 300.0)
        assert len(result) == 2
        for mp in result:
            assert mp.confidence == ConfidenceLevel.LOW
            assert mp.sources == ["silence"]

    def test_subtitle_only_low(self):
        """仅字幕信号 → LOW"""
        gaps = [(98.0, 102.0), (198.0, 202.0)]

        result = self.validator.validate([], [], gaps, 300.0)
        # 字幕间隙中点会被用作候选点
        assert len(result) > 0
        for mp in result:
            assert mp.confidence == ConfidenceLevel.LOW
            assert "subtitle" in mp.sources

    def test_no_medium_confidence(self):
        """验证不存在 MEDIUM 置信度"""
        flash = [100.0]
        silence = [100.5]

        result = self.validator.validate(flash, silence, None, 200.0)
        for mp in result:
            assert mp.confidence != "MEDIUM"
            assert mp.confidence in (ConfidenceLevel.HIGH, ConfidenceLevel.LOW)

    def test_all_signals_empty(self):
        """所有信号源为空列表 → 返回空列表"""
        result = self.validator.validate([], [], None, 100.0)
        assert result == []

    def test_auto_degrade_when_one_signal_empty(self):
        """某一信号为空时自动降级"""
        flash = [100.0]
        silence = [100.5]
        # 无字幕

        result = self.validator.validate(flash, silence, None, 200.0)
        # 闪白+静音 → HIGH
        assert len(result) == 1
        assert result[0].confidence == ConfidenceLevel.HIGH

    def test_timestamp_adjustment_to_gap(self):
        """有字幕时时间戳应微调到静默区间中点"""
        flash = [101.0]
        gaps = [(98.0, 102.0)]  # 中点是 100.0
        silence = [100.5]

        result = self.validator.validate(flash, silence, gaps, 200.0)
        # 应调整到间隙中点 100.0
        assert len(result) == 1
        assert abs(result[0].timestamp - 100.0) < 0.1

    def test_min_episode_duration_filter(self):
        """过滤小于最小时长的分割点"""
        flash = [50.0, 70.0, 150.0]  # 50和70间距20 < 30，会被过滤

        result = self.validator.validate(flash, [], None, 200.0)
        # 50和70合并后保留一个，150保留
        assert len(result) <= 2
        timestamps = [mp.timestamp for mp in result]
        # 最后一个点应该接近150
        assert timestamps[-1] > 100.0


class TestCrossValidatorBuildEpisodeList:
    """测试 build_episode_list() 方法"""

    def setup_method(self):
        self.validator = CrossValidator()

    def test_basic_episode_list(self):
        """基本分集清单生成"""
        merged = [
            MergedPoint(timestamp=100.0, confidence=ConfidenceLevel.HIGH, sources=["flash", "silence"]),
            MergedPoint(timestamp=200.0, confidence=ConfidenceLevel.LOW, sources=["flash"]),
        ]

        episodes = self.validator.build_episode_list(merged, 300.0)
        assert len(episodes) == 3

        # 第一集 0 → 100
        assert episodes[0].episode_num == 1
        assert episodes[0].start_time == 0.0
        assert episodes[0].end_time == 100.0
        assert episodes[0].confidence == ConfidenceLevel.HIGH

        # 第二集 100 → 200
        assert episodes[1].episode_num == 2
        assert episodes[1].start_time == 100.0
        assert episodes[1].end_time == 200.0
        assert episodes[1].confidence == ConfidenceLevel.LOW

        # 第三集 200 → 300
        assert episodes[2].episode_num == 3
        assert episodes[2].start_time == 200.0
        assert episodes[2].end_time == 300.0
        assert episodes[2].confidence == ConfidenceLevel.LOW

    def test_empty_merged_points_returns_single_episode(self):
        """空分割点列表返回单集"""
        episodes = self.validator.build_episode_list([], 300.0)
        assert len(episodes) == 1
        assert episodes[0].episode_num == 1
        assert episodes[0].start_time == 0.0
        assert episodes[0].end_time == 300.0
        assert episodes[0].confidence == ConfidenceLevel.LOW

    def test_subtitle_conflict_detection(self):
        """台词冲突检测"""
        merged = [
            MergedPoint(timestamp=15.0, confidence=ConfidenceLevel.HIGH, sources=["flash"]),
        ]

        subs = [
            SubtitleItem(index=1, start=10.0, end=20.0, text="测试台词"),
        ]

        episodes = self.validator.build_episode_list(merged, 60.0, subs=subs)
        assert len(episodes) == 2

        # 第一集结束点 15.0 落在字幕 10.0-20.0 中 → 冲突
        assert episodes[0].has_subtitle_conflict is True
        assert episodes[0].conflict_detail is not None

    def test_no_conflict_when_cut_between_subtitles(self):
        """切割点在字幕之间无冲突"""
        merged = [
            MergedPoint(timestamp=25.0, confidence=ConfidenceLevel.HIGH, sources=["flash"]),
        ]

        subs = [
            SubtitleItem(index=1, start=10.0, end=20.0, text="第一句"),
            SubtitleItem(index=2, start=30.0, end=40.0, text="第二句"),
        ]

        episodes = self.validator.build_episode_list(merged, 60.0, subs=subs)
        # 切割点 25.0 在 20-30 之间，不在任何字幕中
        assert episodes[0].has_subtitle_conflict is False
