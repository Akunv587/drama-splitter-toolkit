"""集数序列对齐与缺口修补。

以 OCR 集数序列为骨架，将其他信号的候选点向骨架对齐。
对于 OCR 漏识别导致的集数缺口，利用附近的其他信号候选点推断补全。
"""

import logging
from dataclasses import dataclass, field
from typing import List, Optional

from drama_splitter.exceptions import NoBoundariesError
from drama_splitter.fusion.candidate_merger import MergedBoundary

logger = logging.getLogger(__name__)


@dataclass
class AlignedEpisode:
    """对齐后的集边界。

    Attributes:
        episode: 集数（1-based）。
        timestamp_sec: 边界时间戳。
        signals: 命中的信号类型列表。
        is_inferred: 是否通过推断补全。
        confidence_note: 推断/确认的备注说明。
    """

    episode: int
    timestamp_sec: float
    signals: List[str] = field(default_factory=list)
    is_inferred: bool = False
    confidence_note: str = ""


class EpisodeAligner:
    """集数序列对齐器。"""

    def align(
        self,
        boundaries: List[MergedBoundary],
        duration_sec: float,
    ) -> List[AlignedEpisode]:
        """将候选边界对齐到集数序列。

        Args:
            boundaries: 聚类合并后的边界列表。
            duration_sec: 视频总时长。

        Returns:
            对齐后的集边界列表。

        Raises:
            NoBoundariesError: 输入边界列表为空。
        """
        if not boundaries:
            raise NoBoundariesError("边界列表为空，无法进行集数对齐。")

        # 提取带 episode 的边界作为 OCR 骨架
        ocr_boundaries = [b for b in boundaries if b.episode is not None]

        if len(ocr_boundaries) >= 2:
            return self._align_with_ocr_skeleton(
                boundaries, ocr_boundaries, duration_sec
            )
        else:
            # OCR 骨架不足，退化为自动编号
            return self._align_with_auto_numbering(boundaries, duration_sec)

    def _align_with_ocr_skeleton(
        self,
        all_boundaries: List[MergedBoundary],
        ocr_boundaries: List[MergedBoundary],
        duration_sec: float,
    ) -> List[AlignedEpisode]:
        """情况 A/C：有 OCR 骨架（≥ 2 个带 episode 的边界）。"""
        # 按 episode 升序排列骨架
        ocr_boundaries.sort(key=lambda b: b.episode or 0)

        # 提取非 OCR 边界（按时间排序）
        non_ocr = sorted(
            [b for b in all_boundaries if b.episode is None],
            key=lambda b: b.timestamp_sec,
        )

        result: List[AlignedEpisode] = []

        # 遍历相邻两集
        for i in range(len(ocr_boundaries)):
            curr = ocr_boundaries[i]
            next_ep = ocr_boundaries[i + 1] if i + 1 < len(ocr_boundaries) else None

            # 添加当前 OCR 边界
            result.append(
                AlignedEpisode(
                    episode=curr.episode or 0,
                    timestamp_sec=curr.timestamp_sec,
                    signals=curr.signals,
                    is_inferred=False,
                    confidence_note="OCR 直接识别",
                )
            )

            if next_ep is None:
                break

            curr_ep = curr.episode or 0
            next_curr_ep = next_ep.episode or 0
            gap = next_curr_ep - curr_ep

            if gap <= 1:
                # 无缺口
                continue

            # 有缺口：在时间区间内查找非 OCR 边界
            t_start = curr.timestamp_sec
            t_end = next_ep.timestamp_sec

            gap_candidates = [
                b for b in non_ocr
                if t_start < b.timestamp_sec < t_end
            ]

            missing_ep = curr_ep + 1

            if gap_candidates:
                # 取最接近时间中点的
                midpoint = (t_start + t_end) / 2
                gap_candidates.sort(
                    key=lambda b: abs(b.timestamp_sec - midpoint)
                )
                best = gap_candidates[0]

                result.append(
                    AlignedEpisode(
                        episode=missing_ep,
                        timestamp_sec=best.timestamp_sec,
                        signals=best.signals,
                        is_inferred=True,
                        confidence_note=f"推断（集数 {curr_ep} → {next_curr_ep} 缺口补全）",
                    )
                )
                non_ocr.remove(best)
            else:
                logger.warning(
                    "集数 %d → %d 有缺口但无非 OCR 边界可补全",
                    curr_ep, next_curr_ep,
                )

        # 集数序列规范化：前插缺失集
        result = self._prepend_missing_episodes(result)

        return result

    def _align_with_auto_numbering(
        self,
        boundaries: List[MergedBoundary],
        duration_sec: float,
    ) -> List[AlignedEpisode]:
        """情况 B：OCR 骨架不足，自动编号。"""
        # 如果有 1 个 OCR 边界，用它的 episode
        ocr_boundaries = [b for b in boundaries if b.episode is not None]
        if len(ocr_boundaries) == 1:
            known_ep = ocr_boundaries[0].episode or 1
            known_ts = ocr_boundaries[0].timestamp_sec
            # 找到这个已知边界在列表中的位置
            for i, b in enumerate(boundaries):
                if b is ocr_boundaries[0]:
                    result: List[AlignedEpisode] = []
                    for j, boundary in enumerate(boundaries):
                        if j == i:
                            result.append(
                                AlignedEpisode(
                                    episode=known_ep,
                                    timestamp_sec=boundary.timestamp_sec,
                                    signals=boundary.signals,
                                    is_inferred=False,
                                    confidence_note="OCR 直接识别",
                                )
                            )
                        elif j < i:
                            inferred_ep = known_ep - (i - j)
                            result.append(
                                AlignedEpisode(
                                    episode=max(1, inferred_ep),
                                    timestamp_sec=boundary.timestamp_sec,
                                    signals=boundary.signals,
                                    is_inferred=True,
                                    confidence_note="自动编号（推断）",
                                )
                            )
                        else:
                            inferred_ep = known_ep + (j - i)
                            result.append(
                                AlignedEpisode(
                                    episode=inferred_ep,
                                    timestamp_sec=boundary.timestamp_sec,
                                    signals=boundary.signals,
                                    is_inferred=True,
                                    confidence_note="自动编号（推断）",
                                )
                            )
                    # 排序
                    result.sort(key=lambda e: e.episode)
                    return result

        # 完全无 OCR：从 1 开始编号
        result = []
        for i, boundary in enumerate(boundaries):
            result.append(
                AlignedEpisode(
                    episode=i + 1,
                    timestamp_sec=boundary.timestamp_sec,
                    signals=boundary.signals,
                    is_inferred=True,
                    confidence_note="自动编号（推断）",
                )
            )
        return result

    @staticmethod
    def _prepend_missing_episodes(
        episodes: List[AlignedEpisode],
    ) -> List[AlignedEpisode]:
        """集数序列规范化：最小集数 > 1 且 ≤ 5 时前插。

        Args:
            episodes: 对齐后的集边界列表。

        Returns:
            规范化后的列表。
        """
        if not episodes:
            return episodes

        # 按集数排序
        episodes.sort(key=lambda e: e.episode)
        min_ep = episodes[0].episode

        if min_ep <= 1:
            return episodes

        if min_ep > 5:
            logger.warning(
                "最小集数为 %d（> 5），不前插缺失集。可能视频开头被截断。",
                min_ep,
            )
            return episodes

        logger.info("最小集数为 %d，前插 %d 集", min_ep, min_ep - 1)
        prepended: List[AlignedEpisode] = []
        for ep in range(1, min_ep):
            prepended.append(
                AlignedEpisode(
                    episode=ep,
                    timestamp_sec=0.0,
                    signals=["inferred"],
                    is_inferred=True,
                    confidence_note=f"前插推断（集数 {min_ep} 之前缺少 {min_ep - 1} 集）",
                )
            )
        return prepended + episodes
