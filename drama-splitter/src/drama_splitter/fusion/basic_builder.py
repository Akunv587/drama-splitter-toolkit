"""基础时间清单生成器。

基于单一 OCR 信号，将候选点转化为可供裁剪的时间清单。
阶段 1 的简化融合逻辑——不涉及多信号聚类和对齐。
"""

import logging
from typing import List

from drama_splitter.exceptions import InsufficientCandidatesError
from drama_splitter.models import CandidateList, Episode, TimeList

logger = logging.getLogger(__name__)


class BasicTimeListBuilder:
    """基于 OCR 单信号的时间清单生成器。

    将 OCR 检测器产出的候选点（含集数信息）转化为可用于裁剪的时间清单。
    """

    def build(self, candidate_list: CandidateList) -> TimeList:
        """从 OCR 候选点构建时间清单。

        Args:
            candidate_list: OCR 检测器产出的候选点列表。

        Returns:
            时间清单，可直接用于裁剪。

        Raises:
            InsufficientCandidatesError: 候选点不足（< 2 个集数）。
        """
        candidates = candidate_list.candidates
        if not candidates:
            raise InsufficientCandidatesError(
                "OCR 未能识别到任何集数标题，无法生成时间清单。"
            )

        # 筛选有效候选点（至少 2 个集数）
        valid = [c for c in candidates if c.episode is not None]
        if len(valid) < 2:
            raise InsufficientCandidatesError(
                f"OCR 仅识别到 {len(valid)} 个集数，至少需要 2 个才能生成时间清单。"
            )

        # 按集数升序排列
        valid.sort(key=lambda c: c.episode)

        # 检查集数从 0 开始的情况
        if valid[0].episode == 0:
            logger.warning("检测到集数从 0 开始，已修正为 1")
            valid[0].episode = 1

        if valid[0].episode is not None and valid[0].episode > 1:
            logger.warning("最小集数为 %d（非 1），保留原值", valid[0].episode)

        # 检查集数跳号
        for i in range(1, len(valid)):
            prev_ep = valid[i - 1].episode
            curr_ep = valid[i].episode
            if prev_ep is not None and curr_ep is not None and curr_ep - prev_ep > 1:
                missing = list(range(prev_ep + 1, curr_ep))
                logger.warning("检测到集数序列跳号，缺失: %s", missing)

        # 构造 Episode 列表
        episodes: List[Episode] = []
        for i, c in enumerate(valid):
            start_sec = c.timestamp_sec
            # end_sec = 下一集 start_sec，最后一集 = 视频时长
            if i < len(valid) - 1:
                end_sec = valid[i + 1].timestamp_sec
            else:
                end_sec = candidate_list.duration_sec

            duration = end_sec - start_sec
            ep_num = c.episode if c.episode is not None else i + 1

            episodes.append(
                Episode(
                    episode=ep_num,
                    start_sec=start_sec,
                    end_sec=end_sec,
                    duration_sec=duration,
                    confidence="medium",  # 单信号，阶段 3 会重新评级
                    signals_hit=["ocr"],
                )
            )

        logger.info("基础时间清单: %d 集", len(episodes))
        return TimeList(
            video_name=candidate_list.video_name,
            duration_sec=candidate_list.duration_sec,
            episodes=episodes,
        )
