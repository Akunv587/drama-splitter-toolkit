"""置信度分级标注与最终时间清单生成。

根据每集边界命中的信号种类和数量，标注 confidence 等级 (high/medium/low)，
并生成最终的 TimeList。
"""

import logging
from typing import List, Literal

from drama_splitter.fusion.episode_aligner import AlignedEpisode
from drama_splitter.models import Episode, TimeList

logger = logging.getLogger(__name__)


class ConfidenceScorer:
    """置信度分级标注器。

    Args:
        min_episode_sec: 最短单集时长（秒），默认 60。
        max_episode_sec: 最长单集时长（秒），默认 300。
    """

    def __init__(
        self,
        min_episode_sec: float = 60.0,
        max_episode_sec: float = 300.0,
    ) -> None:
        self.min_episode_sec = min_episode_sec
        self.max_episode_sec = max_episode_sec

    def score(
        self,
        aligned_episodes: List[AlignedEpisode],
        duration_sec: float,
        video_name: str,
    ) -> TimeList:
        """标注置信度并生成最终时间清单。

        Args:
            aligned_episodes: 对齐后的集边界列表。
            duration_sec: 视频总时长。
            video_name: 视频文件名。

        Returns:
            最终 TimeList，每集带 confidence 标注。
        """
        episodes: List[Episode] = []

        for i, ae in enumerate(aligned_episodes):
            # ── 步骤 A：逐集评级 ──
            confidence = self._rate_confidence(ae)

            # ── 步骤 B：计算时间区间 ──
            start_sec = ae.timestamp_sec
            if i == 0 and start_sec > 5.0:
                start_sec = 0.0

            if i + 1 < len(aligned_episodes):
                end_sec = aligned_episodes[i + 1].timestamp_sec
            else:
                end_sec = duration_sec

            # ── 步骤 C：异常检测 ──
            ep_duration = end_sec - start_sec
            if ep_duration < self.min_episode_sec:
                logger.warning(
                    "第 %d 集时长 %.1fs < 最小值 %.1fs，标记 low",
                    ae.episode, ep_duration, self.min_episode_sec,
                )
                confidence = "low"
            elif ep_duration > self.max_episode_sec:
                logger.warning(
                    "第 %d 集时长 %.1fs > 最大值 %.1fs，标记 low",
                    ae.episode, ep_duration, self.max_episode_sec,
                )
                confidence = "low"

            # 构造 Episode
            episode = Episode(
                episode=ae.episode,
                start_sec=start_sec,
                end_sec=end_sec,
                duration_sec=ep_duration,
                confidence=confidence,
                signals_hit=ae.signals,
            )
            episodes.append(episode)

        # 统计
        high_count = sum(1 for e in episodes if e.confidence == "high")
        medium_count = sum(1 for e in episodes if e.confidence == "medium")
        low_count = sum(1 for e in episodes if e.confidence == "low")
        logger.info(
            "置信度分布: high=%d, medium=%d, low=%d (共 %d 集)",
            high_count, medium_count, low_count, len(episodes),
        )

        return TimeList(
            video_name=video_name,
            duration_sec=duration_sec,
            episodes=episodes,
        )

    def _rate_confidence(self, ae: AlignedEpisode) -> Literal["high", "medium", "low"]:
        """根据信号命中情况评定置信度等级。

        Args:
            ae: 对齐后的集边界。

        Returns:
            置信度等级。
        """
        signals = set(ae.signals)
        has_ocr = "ocr" in signals
        other_signals = signals - {"ocr", "inferred", "scene_cluster"}
        other_count = len(other_signals)

        if has_ocr and other_count >= 1:
            return "high"

        if has_ocr and other_count == 0:
            return "medium"

        if ae.is_inferred and other_count >= 2:
            return "medium"

        if ae.is_inferred and other_count < 2:
            return "low"

        # 仅弱信号命中
        return "low"
