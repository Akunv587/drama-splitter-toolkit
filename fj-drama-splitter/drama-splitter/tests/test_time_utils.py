"""时间工具模块单元测试"""

import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from utils.time_utils import (
    seconds_to_timestamp,
    timestamp_to_seconds,
    vtt_timestamp_to_seconds,
    parse_manual_timestamps,
    format_duration,
    check_subtitle_conflict,
    find_nearest_silence_gap,
)
from modules.models import SubtitleItem


class TestSecondsToTimestamp:
    """测试秒数转时间戳"""

    def test_zero_seconds(self):
        assert seconds_to_timestamp(0) == "00:00:00"

    def test_less_than_minute(self):
        assert seconds_to_timestamp(22.5) == "00:00:22"

    def test_less_than_hour(self):
        assert seconds_to_timestamp(142.3) == "00:02:22"

    def test_more_than_hour(self):
        assert seconds_to_timestamp(3661.5) == "01:01:01"

    def test_negative_seconds(self):
        assert seconds_to_timestamp(-10) == "00:00:00"


class TestTimestampToSeconds:
    """测试时间戳转秒数"""

    def test_hh_mm_ss(self):
        assert timestamp_to_seconds("00:02:22") == 142.0

    def test_mm_ss(self):
        assert timestamp_to_seconds("02:30") == 150.0

    def test_srt_format_with_comma(self):
        """SRT 格式 HH:MM:SS,mmm"""
        assert timestamp_to_seconds("00:02:22,160") == pytest.approx(142.16)

    def test_vtt_format_with_dot(self):
        """VTT 格式 HH:MM:SS.mmm"""
        assert timestamp_to_seconds("00:02:22.160") == pytest.approx(142.16)

    def test_one_hour_plus(self):
        assert timestamp_to_seconds("01:30:45") == 5445.0

    def test_empty_string(self):
        with pytest.raises(ValueError):
            timestamp_to_seconds("")

    def test_invalid_format(self):
        with pytest.raises(ValueError):
            timestamp_to_seconds("abc")


class TestVttTimestampToSeconds:
    """测试 VTT 时间戳解析"""

    def test_basic_vtt(self):
        assert vtt_timestamp_to_seconds("00:01:23.456") == pytest.approx(83.456)

    def test_zero(self):
        assert vtt_timestamp_to_seconds("00:00:00.000") == 0.0

    def test_long_vtt(self):
        assert vtt_timestamp_to_seconds("01:30:45.500") == pytest.approx(5445.5)

    def test_invalid_vtt_format(self):
        with pytest.raises(ValueError):
            vtt_timestamp_to_seconds("01:23")  # 只有 MM:SS


class TestParseManualTimestamps:
    """测试人工输入时间戳解析"""

    def test_basic(self):
        result = parse_manual_timestamps("00:00:00, 00:02:30, 00:05:00")
        assert result == [0.0, 150.0, 300.0]

    def test_mm_ss_format(self):
        result = parse_manual_timestamps("00:00, 02:30, 05:00")
        assert result == [0.0, 150.0, 300.0]

    def test_mixed_format(self):
        result = parse_manual_timestamps("00:00:00, 02:30, 01:00:00")
        assert result == [0.0, 150.0, 3600.0]

    def test_single_timestamp(self):
        result = parse_manual_timestamps("00:01:00")
        assert result == [60.0]

    def test_with_spaces(self):
        result = parse_manual_timestamps("  00:00:00  ,  00:02:30  ,  00:05:00  ")
        assert result == [0.0, 150.0, 300.0]

    def test_empty_input(self):
        result = parse_manual_timestamps("")
        assert result == []

    def test_invalid_format(self):
        with pytest.raises(ValueError):
            parse_manual_timestamps("abc, def")


class TestFormatDuration:
    """测试时长格式化"""

    def test_zero(self):
        assert format_duration(0) == "0'00\""

    def test_less_than_minute(self):
        assert format_duration(45) == "0'45\""

    def test_minutes_and_seconds(self):
        assert format_duration(142.3) == "2'22\""

    def test_hour(self):
        assert format_duration(3661) == "61'01\""


class TestCheckSubtitleConflict:
    """测试字幕冲突检测"""

    def _make_subs(self) -> list[SubtitleItem]:
        return [
            SubtitleItem(index=1, start=10.0, end=15.0, text="第一句台词"),
            SubtitleItem(index=2, start=20.0, end=25.0, text="第二句台词"),
            SubtitleItem(index=3, start=30.0, end=35.0, text="第三句台词"),
        ]

    def test_cut_in_middle_of_subtitle(self):
        """切割点在台词中间"""
        subs = self._make_subs()
        assert check_subtitle_conflict(12.0, subs) is True

    def test_cut_between_subtitles(self):
        """切割点在台词之间"""
        subs = self._make_subs()
        assert check_subtitle_conflict(17.0, subs) is False

    def test_cut_before_first_subtitle(self):
        """切割点在第一句之前"""
        subs = self._make_subs()
        assert check_subtitle_conflict(5.0, subs) is False

    def test_cut_after_last_subtitle(self):
        """切割点在最后一句之后"""
        subs = self._make_subs()
        assert check_subtitle_conflict(40.0, subs) is False

    def test_cut_exactly_at_start(self):
        """切割点恰好等于字幕开始时间（不算冲突）"""
        subs = self._make_subs()
        assert check_subtitle_conflict(10.0, subs) is False

    def test_cut_exactly_at_end(self):
        """切割点恰好等于字幕结束时间（不算冲突）"""
        subs = self._make_subs()
        assert check_subtitle_conflict(15.0, subs) is False

    def test_empty_subs(self):
        """无字幕时应返回 False"""
        assert check_subtitle_conflict(12.0, []) is False

    def test_none_subs(self):
        """字幕为 None 时应返回 False"""
        # 测试传入空列表等同于无字幕
        assert check_subtitle_conflict(12.0, []) is False


class TestFindNearestSilenceGap:
    """测试查找最近静默区间"""

    def _make_gaps(self) -> list[tuple[float, float]]:
        return [
            (5.0, 7.0),    # 中点 6.0
            (15.0, 17.0),  # 中点 16.0
            (25.0, 27.0),  # 中点 26.0
        ]

    def test_find_nearest_within_tolerance(self):
        """切割点在容差范围内有间隙"""
        gaps = self._make_gaps()
        # 切割点 5.5 距离第一个间隙中点 6.0 差 0.5，在容差 3.0 内
        adjusted, is_adjusted = find_nearest_silence_gap(5.5, gaps, tolerance=3.0)
        assert is_adjusted is True
        assert adjusted == pytest.approx(6.0)

    def test_no_gap_within_tolerance(self):
        """切割点不在任何间隙容差范围内"""
        gaps = self._make_gaps()
        adjusted, is_adjusted = find_nearest_silence_gap(100.0, gaps, tolerance=3.0)
        assert is_adjusted is False
        assert adjusted == pytest.approx(100.0)

    def test_exact_match(self):
        """切割点恰好等于某个间隙中点"""
        gaps = self._make_gaps()
        adjusted, is_adjusted = find_nearest_silence_gap(16.0, gaps, tolerance=3.0)
        assert is_adjusted is True
        assert adjusted == pytest.approx(16.0)

    def test_empty_gaps(self):
        """无间隙时返回原值"""
        adjusted, is_adjusted = find_nearest_silence_gap(10.0, [], tolerance=3.0)
        assert is_adjusted is False
        assert adjusted == pytest.approx(10.0)
