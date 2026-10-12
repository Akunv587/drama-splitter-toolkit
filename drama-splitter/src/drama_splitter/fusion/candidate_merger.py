"""多信号候选点时空聚类。

将四路检测器产出的候选点按时间 proximity 聚类，
±3 秒窗口内的点归为同一簇，取簇内中位时间作为该边界的最终时间。
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np

from drama_splitter.models import Candidate, CandidateList


@dataclass
class MergedBoundary:
    """合并后的集边界。

    Attributes:
        timestamp_sec: 簇内中位时间戳。
        signals: 命中的信号类型列表（去重）。
        episode: OCR 检测到的集数（如有）。
        candidates: 簇内所有原始候选点。
        signal_count: 信号种类数量。
    """

    timestamp_sec: float
    signals: List[str] = field(default_factory=list)
    episode: Optional[int] = None
    candidates: List[Candidate] = field(default_factory=list)
    signal_count: int = 0


class CandidateMerger:
    """多信号候选点聚类合并器。

    Args:
        window_sec: 聚类时间窗口（秒），默认 3.0。
    """

    def __init__(self, window_sec: float = 3.0) -> None:
        self.window_sec = window_sec

    def merge(
        self,
        candidate_lists: Dict[str, CandidateList],
    ) -> List[MergedBoundary]:
        """合并多路候选点，返回聚类后的边界列表。

        Args:
            candidate_lists: {signal_type: CandidateList} 字典。

        Returns:
            聚类合并后的 MergedBoundary 列表。
        """
        # ── 步骤 A：汇总所有候选点 ──
        all_candidates: List[Candidate] = []
        for signal_type, cl in candidate_lists.items():
            if cl is None:
                continue
            for c in cl.candidates:
                all_candidates.append(c)

        if not all_candidates:
            return []

        # ── 步骤 B：按时间戳排序 ──
        all_candidates.sort(key=lambda c: c.timestamp_sec)

        # ── 步骤 C：聚类 ──
        clusters: List[List[Candidate]] = []
        current_cluster: List[Candidate] = [all_candidates[0]]

        for c in all_candidates[1:]:
            gap = c.timestamp_sec - current_cluster[-1].timestamp_sec
            if gap <= self.window_sec:
                current_cluster.append(c)
            else:
                clusters.append(current_cluster)
                current_cluster = [c]
        clusters.append(current_cluster)

        # ── 步骤 D：每簇生成 MergedBoundary ──
        boundaries: List[MergedBoundary] = []
        for cluster in clusters:
            # 计算中位时间戳
            timestamps = [c.timestamp_sec for c in cluster]
            median_ts = float(np.median(timestamps))

            # 信号类型去重
            signals = list({c.signal_type for c in cluster})

            # 提取 OCR 集数
            ocr_candidates = [c for c in cluster if c.signal_type == "ocr" and c.episode is not None]

            if not ocr_candidates:
                # 无 OCR 候选点
                boundary = MergedBoundary(
                    timestamp_sec=median_ts,
                    signals=signals,
                    episode=None,
                    candidates=cluster,
                    signal_count=len(signals),
                )
                boundaries.append(boundary)
                continue

            # 收集所有不同的 OCR 集数
            ocr_episodes: Dict[int, List[Candidate]] = {}
            for c in ocr_candidates:
                assert c.episode is not None
                if c.episode not in ocr_episodes:
                    ocr_episodes[c.episode] = []
                ocr_episodes[c.episode].append(c)

            if len(ocr_episodes) == 1:
                # 单一集数：直接使用
                ep = list(ocr_episodes.keys())[0]
                boundary = MergedBoundary(
                    timestamp_sec=median_ts,
                    signals=signals,
                    episode=ep,
                    candidates=cluster,
                    signal_count=len(signals),
                )
                boundaries.append(boundary)
            else:
                # 多个不同集数：拆分为多个边界
                for ep, ep_candidates in ocr_episodes.items():
                    ep_ts = [c.timestamp_sec for c in ep_candidates]
                    median_ep_ts = float(np.median(ep_ts))
                    # 合并其他信号候选点
                    other_signals = [
                        c for c in cluster
                        if c.signal_type != "ocr" or c.episode is None
                    ]
                    ep_signals = list(
                        {c.signal_type for c in ep_candidates}.union(
                            {c.signal_type for c in other_signals}
                        )
                    )
                    boundary = MergedBoundary(
                        timestamp_sec=median_ep_ts,
                        signals=ep_signals,
                        episode=ep,
                        candidates=ep_candidates + other_signals,
                        signal_count=len(ep_signals),
                    )
                    boundaries.append(boundary)

        # ── 步骤 E：过滤仅 scene_cluster 的单信号簇 ──
        boundaries = [
            b for b in boundaries
            if not (b.signal_count == 1 and "scene_cluster" in b.signals)
        ]

        # 按时间戳排序
        boundaries.sort(key=lambda b: b.timestamp_sec)

        return boundaries
