"""集数标题正则匹配与多帧投票。

从 OCR 识别结果中提取集数信息，通过多帧投票消除单帧误识别，
输出标准候选点 JSON。
"""

import logging
import re
from typing import List

from drama_splitter.detectors.ocr_detector import OCRResult
from drama_splitter.models import Candidate, CandidateList

logger = logging.getLogger(__name__)

# ── 正则模式：覆盖中英文常见集数写法 ──────────────────────────

EPISODE_PATTERNS: List[re.Pattern] = [
    # 中文数字：第三集、第十二话、第二十回、第一百二十三集
    re.compile(r"第\s*([零一二三四五六七八九十百]+)\s*[集话話回]"),
    # 阿拉伯数字中文格式：第1集、第03话、第12回
    re.compile(r"第\s*(\d+)\s*[集话話回]"),
    # 英文格式：EP.3 / EP3 / Episode 12 / EPISODE 1
    re.compile(r"EP\.?\s*(\d+)", re.IGNORECASE),
    re.compile(r"Episode\s*(\d+)", re.IGNORECASE),
]

# 中文数字映射
_CN_NUM_MAP: dict[str, int] = {
    "零": 0, "一": 1, "二": 2, "三": 3, "四": 4,
    "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
    "十": 10, "百": 100,
}


def cn2num(text: str) -> int:
    """中文数字转整数，支持 0–999。

    Args:
        text: 中文数字字符串，如 "一百二十三"。

    Returns:
        转换后的整数。

    Examples:
        >>> cn2num("零")
        0
        >>> cn2num("二十三")
        23
        >>> cn2num("一百零一")
        101
        >>> cn2num("九百九十九")
        999
    """
    # 阿拉伯数字直接返回
    if text.isdigit():
        return int(text)

    result = 0
    current = 0

    for ch in text:
        val = _CN_NUM_MAP.get(ch)
        if val is None:
            continue
        if val == 100:
            current = (current if current > 0 else 1) * 100
            result += current
            current = 0
        elif val == 10:
            current = (current if current > 0 else 1) * 10
        else:
            current += val

    result += current
    return result


class EpisodeMatcher:
    """集数标题匹配器。

    从 OCR 结果中提取集数，通过多帧投票提高准确性。
    """

    # 连续帧判定窗口（秒）
    _CONTINUOUS_WINDOW_SEC = 2.0

    def match(
        self,
        ocr_results: list[OCRResult],
        duration_sec: float,
        video_name: str,
    ) -> CandidateList:
        """从 OCR 结果中提取集数候选点。

        Args:
            ocr_results: OCR 识别结果列表。
            duration_sec: 视频总时长。
            video_name: 视频文件名。

        Returns:
            包含集数候选点的 ``CandidateList``。
        """
        # ── 第一阶段：逐帧匹配 ──
        raw_hits: list[tuple[float, int, float, str]] = []  # (ts, ep, score, text)

        for ocr_res in ocr_results:
            for tb in ocr_res.text_blocks:
                if tb.score < 0.5:
                    continue
                for pat in EPISODE_PATTERNS:
                    m = pat.search(tb.text)
                    if m:
                        ep_str = m.group(1)
                        try:
                            ep = cn2num(ep_str)
                        except (ValueError, KeyError):
                            continue
                        if ep <= 0 or ep > 999:
                            logger.warning("OCR 检测到异常集数 %d，已跳过", ep)
                            continue
                        raw_hits.append((ocr_res.timestamp_sec, ep, tb.score, tb.text))
                        break

        if not raw_hits:
            logger.warning("OCR 未识别到任何集数标题")
            return CandidateList(
                signal_type="ocr",
                video_name=video_name,
                duration_sec=duration_sec,
                candidates=[],
            )

        logger.info("OCR 原始命中: %d 次", len(raw_hits))

        # ── 第二阶段：多帧投票 ──
        # 按集数分组
        by_episode: dict[int, list[tuple[float, float, str]]] = {}
        for ts, ep, score, text in raw_hits:
            by_episode.setdefault(ep, []).append((ts, score, text))

        candidates: list[Candidate] = []
        for ep, hits in sorted(by_episode.items()):
            hits.sort(key=lambda x: x[0])
            vote_count = self._count_continuous(hits)

            if vote_count >= 2:
                confidence = 0.9
            elif vote_count == 1 and hits[0][1] >= 0.8:
                confidence = 0.7
            else:
                # 弃用
                continue

            first_hit = hits[0]
            candidates.append(
                Candidate(
                    signal_type="ocr",
                    timestamp_sec=first_hit[0],
                    episode=ep,
                    confidence=confidence,
                    metadata={
                        "text": first_hit[2],
                        "score": first_hit[1],
                        "vote_count": vote_count,
                    },
                )
            )

        # ── 第三阶段：去重（同一集数只保留首次出现） ──
        # 已在上面按 episode 分组取第一个

        # ── 第四阶段：构造 CandidateList ──
        candidates.sort(key=lambda c: c.timestamp_sec)
        logger.info("OCR 匹配结果: %d 个候选集", len(candidates))

        return CandidateList(
            signal_type="ocr",
            video_name=video_name,
            duration_sec=duration_sec,
            candidates=candidates,
        )

    def _count_continuous(self, hits: list[tuple[float, float, str]]) -> int:
        """统计连续帧中的命中次数。"""
        if not hits:
            return 0
        count = 1
        max_count = 1
        for i in range(1, len(hits)):
            if hits[i][0] - hits[i - 1][0] <= self._CONTINUOUS_WINDOW_SEC:
                count += 1
                max_count = max(max_count, count)
            else:
                count = 1
        return max_count
