"""字幕解析模块单元测试"""

import pytest
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from modules.subtitle_parser import SubtitleParser
from modules.models import SubtitleItem, WordTimestamp


# ── SRT 示例数据 ──────────────────────────────────────────────

SAMPLE_SRT_CONTENT = """1
00:00:12,160 --> 00:00:18,640
Tonight, the moon goddess herself shall

2
00:00:15,200 --> 00:00:20,400
bear witness. The silver moon bloodline

3
00:01:02,500 --> 00:01:10,000
The ceremony begins at midnight.
"""

# 包含超过1小时的 SRT 示例（测试 .ordinal 不会丢失小时信息）
SAMPLE_SRT_WITH_HOURS = """1
01:30:00,000 --> 01:35:00,500
Episode two begins after one and a half hours.

2
02:00:00,000 --> 02:05:00,000
The finale.
"""


# ── VTT 示例数据 ──────────────────────────────────────────────

SAMPLE_VTT_CONTENT = """WEBVTT
Kind: captions
Language: en

00:00:12.160 --> 00:00:15.190 align:start position:0%
Tonight,<00:00:13.360><c> the</c><00:00:13.599><c> moon</c><00:00:13.920><c> goddess</c>

00:00:15.190 --> 00:00:15.200 align:start position:0%
Tonight, the moon goddess herself shall

00:00:20.000 --> 00:00:25.000 align:start position:0%
bear<00:00:21.000><c> witness</c><00:00:22.500><c> now</c>

00:00:20.000 --> 00:00:25.000 align:start position:0%
bear witness now
"""

SAMPLE_VTT_WITH_WORD_TIMESTAMPS = """WEBVTT

00:00:01.000 --> 00:00:05.000 align:start position:0%
Hello,<00:00:01.500><c> world</c><00:00:02.000><c> this</c><00:00:02.500><c> is</c>

00:00:01.000 --> 00:00:05.000 align:start position:0%
Hello, world this is

00:00:05.000 --> 00:00:10.000 align:start position:0%
a<00:00:06.000><c> test</c><00:00:07.000><c> of</c><00:00:08.000><c> VTT</c>

00:00:05.000 --> 00:00:10.000 align:start position:0%
a test of VTT
"""


class TestSubtitleParserSRT:
    """测试 SRT 格式解析"""

    def test_parse_srt_basic(self):
        """基本 SRT 解析"""
        with tempfile.NamedTemporaryFile(
            mode='w', suffix='.txt', delete=False, encoding='utf-8'
        ) as f:
            f.write(SAMPLE_SRT_CONTENT)
            tmp_path = f.name

        try:
            subs = SubtitleParser.parse(tmp_path)
            assert len(subs) == 3

            # 第一条字幕
            assert subs[0].index == 1
            assert subs[0].start == pytest.approx(12.16)
            assert subs[0].end == pytest.approx(18.64)
            assert "moon goddess" in subs[0].text

            # 第二条字幕
            assert subs[1].index == 2
            assert subs[1].start == pytest.approx(15.20)
            assert subs[1].end == pytest.approx(20.40)
            assert "silver moon" in subs[1].text

            # 第三条字幕（分钟部分）
            assert subs[2].start == pytest.approx(62.5)
            assert subs[2].end == pytest.approx(70.0)
        finally:
            os.unlink(tmp_path)

    def test_parse_srt_with_hours(self):
        """SRT 时间戳包含小时的情况（验证 .ordinal 不丢失小时）"""
        with tempfile.NamedTemporaryFile(
            mode='w', suffix='.txt', delete=False, encoding='utf-8'
        ) as f:
            f.write(SAMPLE_SRT_WITH_HOURS)
            tmp_path = f.name

        try:
            subs = SubtitleParser.parse(tmp_path)
            assert len(subs) == 2

            # 1小时30分 = 5400秒
            assert subs[0].start == pytest.approx(5400.0)  # 01:30:00
            assert subs[0].end == pytest.approx(5700.5)    # 01:35:00.500

            # 2小时 = 7200秒
            assert subs[1].start == pytest.approx(7200.0)  # 02:00:00
            assert subs[1].end == pytest.approx(7500.0)    # 02:05:00
        finally:
            os.unlink(tmp_path)

    def test_parse_srt_millisecond_precision(self):
        """SRT 毫秒精度验证"""
        content = """1
00:00:01,500 --> 00:00:03,250
Test subtitle with milliseconds.
"""
        with tempfile.NamedTemporaryFile(
            mode='w', suffix='.txt', delete=False, encoding='utf-8'
        ) as f:
            f.write(content)
            tmp_path = f.name

        try:
            subs = SubtitleParser.parse(tmp_path)
            assert len(subs) == 1
            assert subs[0].start == pytest.approx(1.5)
            assert subs[0].end == pytest.approx(3.25)
        finally:
            os.unlink(tmp_path)


class TestSubtitleParserVTT:
    """测试 VTT 格式解析"""

    def test_parse_vtt_basic(self):
        """基本 VTT 解析（清理逐词标记、去重）"""
        with tempfile.NamedTemporaryFile(
            mode='w', suffix='.vtt', delete=False, encoding='utf-8'
        ) as f:
            f.write(SAMPLE_VTT_CONTENT)
            tmp_path = f.name

        try:
            subs = SubtitleParser.parse(tmp_path)
            # 共3条：第1条（清理后"Tonight, the moon goddess"）、
            # 第2条（"Tonight, the moon goddess herself shall"）、
            # 第3条和第4条去重为1条（"bear witness now"）
            assert len(subs) == 3

            # 第一条：清理后的文本（带逐词标记的版本）
            assert subs[0].start == pytest.approx(12.16)
            assert "moon goddess" in subs[0].text
            # 不应该包含逐词标记
            assert "<00:00:" not in subs[0].text
            assert "<c>" not in subs[0].text

            # 第二条：纯文本版本
            assert subs[1].start == pytest.approx(15.19)

            # 第三条：去重后的
            assert subs[2].start == pytest.approx(20.0)
            assert "bear" in subs[2].text or "witness" in subs[2].text
        finally:
            os.unlink(tmp_path)

    def test_parse_vtt_dedup(self):
        """VTT 去重：同一起始时间且文本相同的只保留一条"""
        # 构造两个时间完全相同、文本也相同的字幕
        content = """WEBVTT

00:00:10.000 --> 00:00:15.000 align:start position:0%
Same text here

00:00:10.050 --> 00:00:15.000 align:start position:0%
Same text here
"""
        with tempfile.NamedTemporaryFile(
            mode='w', suffix='.vtt', delete=False, encoding='utf-8'
        ) as f:
            f.write(content)
            tmp_path = f.name

        try:
            subs = SubtitleParser.parse(tmp_path)
            # 应该去重为 1 条（start 相差 0.05 < 0.1）
            assert len(subs) == 1
        finally:
            os.unlink(tmp_path)

    def test_parse_vtt_no_dedup_different_text(self):
        """VTT 不去重：相同时间但不同文本"""
        content = """WEBVTT

00:00:10.000 --> 00:00:15.000 align:start position:0%
Different text one

00:00:10.050 --> 00:00:15.000 align:start position:0%
Different text two
"""
        with tempfile.NamedTemporaryFile(
            mode='w', suffix='.vtt', delete=False, encoding='utf-8'
        ) as f:
            f.write(content)
            tmp_path = f.name

        try:
            subs = SubtitleParser.parse(tmp_path)
            # 文本不同，不去重
            assert len(subs) == 2
        finally:
            os.unlink(tmp_path)


class TestSubtitleParserFormatDetection:
    """测试格式自动识别"""

    def test_auto_detect_txt_as_srt(self):
        """自动识别 .txt 为 SRT"""
        with tempfile.NamedTemporaryFile(
            mode='w', suffix='.txt', delete=False, encoding='utf-8'
        ) as f:
            f.write(SAMPLE_SRT_CONTENT)
            tmp_path = f.name

        try:
            subs = SubtitleParser.parse(tmp_path)
            assert len(subs) == 3
        finally:
            os.unlink(tmp_path)

    def test_auto_detect_vtt(self):
        """自动识别 .vtt"""
        with tempfile.NamedTemporaryFile(
            mode='w', suffix='.vtt', delete=False, encoding='utf-8'
        ) as f:
            f.write(SAMPLE_VTT_CONTENT)
            tmp_path = f.name

        try:
            subs = SubtitleParser.parse(tmp_path)
            assert len(subs) == 3
        finally:
            os.unlink(tmp_path)

    def test_unsupported_format(self):
        """不支持格式应抛出异常"""
        with pytest.raises(ValueError, match="不支持的字幕格式"):
            SubtitleParser.parse("test.srt")


class TestExtractSilenceGaps:
    """测试静默区间提取"""

    def test_basic_gaps(self):
        """基本静默区间提取"""
        subs = [
            SubtitleItem(index=1, start=0.0, end=5.0, text="First"),
            SubtitleItem(index=2, start=7.0, end=12.0, text="Second"),
            SubtitleItem(index=3, start=15.0, end=20.0, text="Third"),
        ]
        gaps = SubtitleParser.extract_silence_gaps(subs)
        assert len(gaps) == 2
        assert gaps[0] == (5.0, 7.0)   # 间隙 2s > 0.1s
        assert gaps[1] == (12.0, 15.0)  # 间隙 3s > 0.1s

    def test_tiny_gap_filtered(self):
        """太小的间隙被过滤"""
        subs = [
            SubtitleItem(index=1, start=0.0, end=5.0, text="First"),
            SubtitleItem(index=2, start=5.05, end=10.0, text="Second"),
        ]
        gaps = SubtitleParser.extract_silence_gaps(subs)
        # 间隙 0.05s < 0.1s，被过滤
        assert len(gaps) == 0

    def test_no_gap_overlapping(self):
        """字幕重叠（无间隙）"""
        subs = [
            SubtitleItem(index=1, start=0.0, end=10.0, text="First"),
            SubtitleItem(index=2, start=5.0, end=15.0, text="Second"),
        ]
        gaps = SubtitleParser.extract_silence_gaps(subs)
        # 间隙为负（重叠），被过滤
        assert len(gaps) == 0

    def test_single_subtitle(self):
        """单条字幕无间隙"""
        subs = [
            SubtitleItem(index=1, start=0.0, end=10.0, text="Only"),
        ]
        gaps = SubtitleParser.extract_silence_gaps(subs)
        assert len(gaps) == 0

    def test_empty_subs(self):
        """空列表无间隙"""
        gaps = SubtitleParser.extract_silence_gaps([])
        assert len(gaps) == 0


class TestExtractWordTimestamps:
    """测试 VTT 逐词时间戳提取"""

    def test_extract_word_timestamps(self):
        """从 VTT 提取逐词时间戳"""
        with tempfile.NamedTemporaryFile(
            mode='w', suffix='.vtt', delete=False, encoding='utf-8'
        ) as f:
            f.write(SAMPLE_VTT_WITH_WORD_TIMESTAMPS)
            tmp_path = f.name

        try:
            words = SubtitleParser.extract_word_timestamps(tmp_path)
            assert len(words) >= 4

            # "world" 在 00:00:01.500
            world_words = [w for w in words if w.word == "world"]
            assert len(world_words) == 1
            assert world_words[0].start == pytest.approx(1.5)

            # "test" 在 00:00:06.000
            test_words = [w for w in words if w.word == "test"]
            assert len(test_words) >= 1
        finally:
            os.unlink(tmp_path)

    def test_extract_word_timestamps_from_txt(self):
        """SRT 格式返回空列表"""
        with tempfile.NamedTemporaryFile(
            mode='w', suffix='.txt', delete=False, encoding='utf-8'
        ) as f:
            f.write(SAMPLE_SRT_CONTENT)
            tmp_path = f.name

        try:
            words = SubtitleParser.extract_word_timestamps(tmp_path)
            assert len(words) == 0
        finally:
            os.unlink(tmp_path)
