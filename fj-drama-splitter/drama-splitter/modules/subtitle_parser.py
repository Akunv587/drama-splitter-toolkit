"""统一字幕解析器（SRT + VTT）

支持两种格式：
1. .txt 文件：内容为标准 SRT 格式
2. .vtt 文件：WebVTT 格式，包含 YouTube 特有的逐词时间戳标记
"""

import re
import logging
from modules.models import SubtitleItem, WordTimestamp
from utils.time_utils import vtt_timestamp_to_seconds

logger = logging.getLogger(__name__)


class SubtitleParser:
    """统一字幕解析器（SRT + VTT）"""

    @staticmethod
    def parse(subtitle_path: str) -> list[SubtitleItem]:
        """自动识别格式并解析字幕。

        Args:
            subtitle_path: 字幕文件路径

        Returns:
            SubtitleItem 列表

        Raises:
            ValueError: 不支持的字幕格式
        """
        if subtitle_path.lower().endswith('.vtt'):
            logger.info(f"检测到 VTT 格式字幕: {subtitle_path}")
            return SubtitleParser._parse_vtt(subtitle_path)
        elif subtitle_path.lower().endswith('.txt'):
            logger.info(f"检测到 SRT 格式字幕 (txt): {subtitle_path}")
            return SubtitleParser._parse_srt(subtitle_path)
        else:
            raise ValueError(f"不支持的字幕格式: {subtitle_path}（仅支持 .txt 和 .vtt）")

    @staticmethod
    def _parse_srt(subtitle_path: str) -> list[SubtitleItem]:
        """解析 .txt 文件（内容为 SRT 格式）。

        使用 pysrt.from_string() 读取文件内容后解析。
        时间戳转换使用 .ordinal / 1000（毫秒转秒），不使用 .seconds。

        Args:
            subtitle_path: SRT 字幕文件路径

        Returns:
            SubtitleItem 列表
        """
        import pysrt

        with open(subtitle_path, 'r', encoding='utf-8') as f:
            content = f.read()

        subs = pysrt.from_string(content)

        result: list[SubtitleItem] = []
        for s in subs:
            result.append(SubtitleItem(
                index=s.index,
                start=s.start.ordinal / 1000,  # 毫秒 → 秒（关键！不能用 .seconds）
                end=s.end.ordinal / 1000,
                text=s.text
            ))

        logger.debug(f"SRT 解析完成: {len(result)} 条字幕")
        return result

    @staticmethod
    def _parse_vtt(subtitle_path: str) -> list[SubtitleItem]:
        """解析 YouTube VTT 字幕文件。

        处理流程：
        1. 用 webvtt.read() 读取
        2. 清理逐词标记：移除 <HH:MM:SS.mmm> 和 </c><c> 标签
        3. 跳过空文本行
        4. 去重：同一句话出现两次时只保留第一条
        5. 转为 SubtitleItem

        Args:
            subtitle_path: VTT 字幕文件路径

        Returns:
            SubtitleItem 列表
        """
        import webvtt

        subs = webvtt.read(subtitle_path)
        result: list[SubtitleItem] = []

        for sub in subs:
            # 清理文本：移除逐词时间戳标签 <00:00:13.360>
            clean_text = re.sub(r'<\d{2}:\d{2}:\d{2}\.\d{3}>', '', sub.text)
            # 移除 <c> 和 </c> 标签
            clean_text = re.sub(r'</?c>', '', clean_text)
            clean_text = clean_text.strip()

            # 跳过空文本（纯标记行）
            if not clean_text:
                continue

            # 解析时间戳（VTT 用点号分隔毫秒）
            start_sec = vtt_timestamp_to_seconds(sub.start)
            end_sec = vtt_timestamp_to_seconds(sub.end)

            # 去重：YouTube VTT 中同一句话会出现两次（带标记行 + 纯文本行）
            # 两条字幕 start 相差 < 0.1s 且 text 相同则视为重复
            if result and abs(result[-1].start - start_sec) < 0.1 and result[-1].text == clean_text:
                continue

            result.append(SubtitleItem(
                index=len(result) + 1,
                start=start_sec,
                end=end_sec,
                text=clean_text
            ))

        logger.debug(f"VTT 解析完成: {len(result)} 条字幕（去重后）")
        return result

    @staticmethod
    def extract_silence_gaps(subs: list[SubtitleItem]) -> list[tuple[float, float]]:
        """提取字幕中相邻两条之间的静默区间。

        间隙 > 0.1s 才算静默区间。

        Args:
            subs: 字幕条目列表

        Returns:
            静默区间列表 [(gap_start, gap_end), ...]
        """
        if len(subs) < 2:
            return []

        gaps: list[tuple[float, float]] = []
        for i in range(len(subs) - 1):
            gap_start = subs[i].end
            gap_end = subs[i + 1].start
            if gap_end - gap_start > 0.1:
                gaps.append((gap_start, gap_end))

        logger.debug(f"提取到 {len(gaps)} 个字幕静默区间")
        return gaps

    @staticmethod
    def extract_word_timestamps(subtitle_path: str) -> list[WordTimestamp]:
        """从 VTT 逐词标记中提取每个词的精确时间戳。

        仅 VTT 格式可用，SRT 格式返回空列表。

        Args:
            subtitle_path: 字幕文件路径（仅 .vtt）

        Returns:
            WordTimestamp 列表
        """
        if not subtitle_path.lower().endswith('.vtt'):
            logger.debug("非 VTT 格式，跳过逐词时间戳提取")
            return []

        with open(subtitle_path, 'r', encoding='utf-8') as f:
            content = f.read()

        words: list[WordTimestamp] = []
        # 匹配 <HH:MM:SS.mmm> 后紧跟的文本
        # 模式：<00:00:13.360><c> the</c>
        pattern = r'<(\d{2}:\d{2}:\d{2}\.\d{3})>(?:<c>)?([^<]*)(?:</c>)?'
        for match in re.finditer(pattern, content):
            ts = vtt_timestamp_to_seconds(match.group(1))
            word = match.group(2).strip()
            if word:
                words.append(WordTimestamp(word=word, start=ts))

        logger.debug(f"VTT 逐词时间戳提取: {len(words)} 个词")
        return words
