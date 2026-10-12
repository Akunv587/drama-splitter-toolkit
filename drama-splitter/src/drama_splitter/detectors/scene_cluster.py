"""场景突变 + 时长聚类检测器。

使用 PySceneDetect 扫描视频中的所有场景切换点，结合短剧单集时长特征
做直方图聚类，筛选出可能是集边界的切换点。
"""

import logging
from typing import List, Tuple

from scenedetect import ContentDetector, detect

from drama_splitter.models import Candidate, CandidateList

logger = logging.getLogger(__name__)


class SceneClusterDetector:
    """场景检测 + 时长聚类检测器。

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

    def detect(
        self,
        video_path: str,
        duration_sec: float,
        video_name: str,
    ) -> CandidateList:
        """场景检测 + 时长聚类，输出候选边界点。

        Args:
            video_path: 视频文件路径。
            duration_sec: 视频时长。
            video_name: 视频文件名。

        Returns:
            场景聚类候选点列表。
        """
        # ── 步骤 A：场景检测 ──
        try:
            scene_list = detect(
                video_path,
                ContentDetector(threshold=27.0),
                show_progress=False,
            )
        except Exception as e:
            logger.warning("PySceneDetect 检测失败: %s", e)
            return CandidateList(
                signal_type="scene_cluster",
                video_name=video_name,
                duration_sec=duration_sec,
                candidates=[],
            )

        if not scene_list:
            logger.info("场景检测: 未检测到任何场景切换")
            return CandidateList(
                signal_type="scene_cluster",
                video_name=video_name,
                duration_sec=duration_sec,
                candidates=[],
            )

        # 提取所有场景切换点时间戳
        cut_points: List[float] = []
        for scene in scene_list:
            end_time = scene[1].get_seconds()
            if 1.0 < end_time < duration_sec - 1.0:
                cut_points.append(end_time)

        if len(cut_points) < 3:
            logger.info("场景检测: 切换点 < 3，全部输出为低置信度候选")
            candidates = []
            for t in cut_points:
                candidates.append(
                    Candidate(
                        signal_type="scene_cluster",
                        timestamp_sec=t,
                        episode=None,
                        confidence=0.2,
                        metadata={"degraded": True},
                    )
                )
            return CandidateList(
                signal_type="scene_cluster",
                video_name=video_name,
                duration_sec=duration_sec,
                candidates=candidates,
            )

        # ── 步骤 B：计算间隔 ──
        intervals: List[float] = []
        prev = 0.0
        for t in cut_points:
            intervals.append(t - prev)
            prev = t
        # 加上最后一个间隔（到视频结尾）
        intervals.append(duration_sec - prev if prev < duration_sec else 0)

        # ── 步骤 C：直方图聚类 ──
        typical = self._cluster_by_duration(intervals)
        logger.info("场景聚类: 典型单集时长 ≈ %.1fs", typical)

        # ── 步骤 D：筛选集边界候选 ──
        candidates: List[Candidate] = []
        for t in cut_points:
            # 检查该点后是否存在间隔接近典型时长的另一个切换点
            found = False
            for other_t in cut_points:
                if other_t > t:
                    gap = other_t - t
                    if typical * 0.8 <= gap <= typical * 1.2:
                        found = True
                        break
            # 也检查到视频结尾
            if not found:
                gap = duration_sec - t
                if typical * 0.8 <= gap <= typical * 1.2:
                    found = True

            confidence = 0.5 if found else 0.2
            candidates.append(
                Candidate(
                    signal_type="scene_cluster",
                    timestamp_sec=t,
                    episode=None,
                    confidence=confidence,
                    metadata={"typical_episode_sec": typical, "matched": found},
                )
            )

        logger.info("场景聚类: %d 个候选点 (共 %d 个切换点)", len(candidates), len(cut_points))
        return CandidateList(
            signal_type="scene_cluster",
            video_name=video_name,
            duration_sec=duration_sec,
            candidates=candidates,
        )

    def _cluster_by_duration(self, intervals: List[float]) -> float:
        """对间隔做直方图聚类，找到典型单集时长。

        Args:
            intervals: 时间间隔列表。

        Returns:
            典型单集时长（秒）。
        """
        import numpy as np

        # 过滤掉过小和过大的间隔
        filtered = [
            iv for iv in intervals
            if self.min_episode_sec * 0.5 <= iv <= self.max_episode_sec * 2
        ]

        if not filtered:
            return 120.0  # 默认值

        # 直方图，bin 宽度 10 秒
        bin_width = 10.0
        bins_range = (0, self.max_episode_sec * 2)
        num_bins = int((bins_range[1] - bins_range[0]) / bin_width)
        hist, bin_edges = np.histogram(filtered, bins=num_bins, range=bins_range)

        # 在 [min_episode_sec, max_episode_sec] 范围内找频次最高的 bin
        min_idx = int(self.min_episode_sec / bin_width)
        max_idx = int(self.max_episode_sec / bin_width)
        if min_idx >= len(hist):
            return 120.0
        max_idx = min(max_idx, len(hist) - 1)

        if min_idx > max_idx:
            return 120.0

        region_hist = hist[min_idx : max_idx + 1]
        if len(region_hist) == 0 or np.max(region_hist) == 0:
            return 120.0

        best_local_idx = int(np.argmax(region_hist))
        best_global_idx = min_idx + best_local_idx
        typical = (bin_edges[best_global_idx] + bin_edges[best_global_idx + 1]) / 2

        return max(self.min_episode_sec, min(typical, self.max_episode_sec))
