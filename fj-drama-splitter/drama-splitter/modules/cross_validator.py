"""三重交叉验证引擎

融合闪白、静音、字幕三种信号源，输出带置信度的分割点列表。
"""

import logging
from modules.models import MergedPoint, EpisodeSegment, ConfidenceLevel
from utils.time_utils import check_subtitle_conflict

logger = logging.getLogger(__name__)


class CrossValidator:
    """三重交叉验证引擎"""

    def __init__(self, match_window: float = 3.0, min_episode_duration: float = 30.0):
        """初始化。

        Args:
            match_window: 信号匹配窗口（秒），默认 3.0
            min_episode_duration: 最小集时长（秒），默认 30.0
        """
        self.match_window = match_window
        self.min_episode_duration = min_episode_duration

    def validate(
        self,
        flash_points: list[float],
        silence_points: list[float],
        subtitle_gaps: list[tuple[float, float]] | None,
        video_duration: float
    ) -> list[MergedPoint]:
        """主入口：融合三种信号，返回分割点列表（MergedPoint）。

        流程：
        1. 收集所有候选点
        2. 对每个候选点检查信号吻合情况
        3. 置信度评定：2+ 信号 → HIGH，1 信号 → LOW
        4. 有字幕时微调时间戳
        5. 合并邻近点
        6. 过滤最小集时长

        Args:
            flash_points: 闪白检测时间戳列表
            silence_points: 静音检测时间戳列表
            subtitle_gaps: 字幕静默区间列表，None 表示无字幕
            video_duration: 视频总时长（秒）

        Returns:
            MergedPoint 列表
        """
        logger.info(
            f"开始交叉验证: flash={len(flash_points)}, "
            f"silence={len(silence_points)}, "
            f"subtitle_gaps={len(subtitle_gaps) if subtitle_gaps else 0}, "
            f"duration={video_duration:.1f}s"
        )

        # 收集所有候选点（使用有序列表）
        all_candidates = sorted(set(
            list(flash_points) +
            list(silence_points) +
            ([(s + e) / 2 for s, e in subtitle_gaps] if subtitle_gaps else [])
        ))

        if not all_candidates:
            logger.info("无候选分割点，返回空列表")
            return []

        # 对每个候选点进行交叉验证
        merged: list[MergedPoint] = []
        for ts in all_candidates:
            sources: list[str] = []

            if self._check_flash_match(ts, flash_points):
                sources.append("flash")
            if self._check_silence_match(ts, silence_points):
                sources.append("silence")
            if self._check_subtitle_match(ts, subtitle_gaps):
                sources.append("subtitle")

            n_sources = len(sources)
            confidence = ConfidenceLevel.HIGH if n_sources >= 2 else ConfidenceLevel.LOW

            # 微调时间戳：有字幕时调整到最近静默区间中点
            adjusted_ts = ts
            if subtitle_gaps:
                adjusted_ts = self._adjust_to_gap(ts, subtitle_gaps)

            merged.append(MergedPoint(
                timestamp=adjusted_ts,
                confidence=confidence,
                sources=sources
            ))

        # 合并邻近 MergedPoint
        merged = self._deduplicate_merged_points(merged, self.match_window)

        # 过滤最小集时长
        merged = self._filter_by_min_duration(merged, self.min_episode_duration)

        logger.info(
            f"交叉验证完成: {len(merged)} 个分割点, "
            f"HIGH={sum(1 for m in merged if m.confidence == ConfidenceLevel.HIGH)}, "
            f"LOW={sum(1 for m in merged if m.confidence == ConfidenceLevel.LOW)}"
        )

        return merged

    def _check_flash_match(self, ts: float, flash_points: list[float]) -> bool:
        """检查 ts 附近是否有闪白信号。

        Args:
            ts: 候选时间戳
            flash_points: 闪白检测结果

        Returns:
            是否在匹配窗口内有闪白信号
        """
        return any(abs(ts - fp) <= self.match_window for fp in flash_points)

    def _check_silence_match(self, ts: float, silence_points: list[float]) -> bool:
        """检查 ts 附近是否有静音信号。

        Args:
            ts: 候选时间戳
            silence_points: 静音检测结果

        Returns:
            是否在匹配窗口内有静音信号
        """
        return any(abs(ts - sp) <= self.match_window for sp in silence_points)

    def _check_subtitle_match(
        self, ts: float, subtitle_gaps: list[tuple[float, float]] | None
    ) -> bool:
        """检查 ts 附近是否有字幕间隙。

        Args:
            ts: 候选时间戳
            subtitle_gaps: 字幕静默区间列表

        Returns:
            是否在匹配窗口内有字幕间隙
        """
        if not subtitle_gaps:
            return False
        for gs, ge in subtitle_gaps:
            if gs - self.match_window <= ts <= ge + self.match_window:
                return True
        return False

    def _adjust_to_gap(
        self, ts: float, subtitle_gaps: list[tuple[float, float]] | None
    ) -> float:
        """将时间戳微调到最近的字幕静默区间中点。

        Args:
            ts: 原始时间戳
            subtitle_gaps: 字幕静默区间列表

        Returns:
            调整后的时间戳
        """
        if not subtitle_gaps:
            return ts

        best_mid = ts
        best_distance = float('inf')

        for gs, ge in subtitle_gaps:
            mid = (gs + ge) / 2
            distance = abs(ts - mid)
            if distance <= self.match_window and distance < best_distance:
                best_distance = distance
                best_mid = mid

        return best_mid

    @staticmethod
    def _deduplicate_merged_points(
        points: list[MergedPoint], window: float
    ) -> list[MergedPoint]:
        """合并 window 内的邻近 MergedPoint，保留置信度最高的。

        Args:
            points: MergedPoint 列表
            window: 合并窗口（秒）

        Returns:
            去重后的 MergedPoint 列表
        """
        if not points:
            return []

        points = sorted(points, key=lambda p: p.timestamp)
        result: list[MergedPoint] = []
        cluster: list[MergedPoint] = [points[0]]

        for i in range(1, len(points)):
            if points[i].timestamp - cluster[-1].timestamp <= window:
                cluster.append(points[i])
            else:
                # 处理当前簇：保留置信度最高的
                best = max(cluster, key=lambda p: (
                    0 if p.confidence == ConfidenceLevel.HIGH else 1,
                    len(p.sources)
                ))
                result.append(best)
                cluster = [points[i]]

        # 处理最后一个簇
        if cluster:
            best = max(cluster, key=lambda p: (
                0 if p.confidence == ConfidenceLevel.HIGH else 1,
                len(p.sources)
            ))
            result.append(best)

        return result

    @staticmethod
    def _filter_by_min_duration(
        points: list[MergedPoint], min_duration: float
    ) -> list[MergedPoint]:
        """过滤掉间距 < min_duration 的点。

        Args:
            points: MergedPoint 列表
            min_duration: 最小集时长（秒）

        Returns:
            过滤后的列表
        """
        if not points:
            return []

        points = sorted(points, key=lambda p: p.timestamp)
        filtered: list[MergedPoint] = [points[0]]

        for pt in points[1:]:
            if pt.timestamp - filtered[-1].timestamp >= min_duration:
                filtered.append(pt)

        return filtered

    def build_episode_list(
        self,
        merged_points: list[MergedPoint],
        video_duration: float,
        subs: list | None = None
    ) -> list[EpisodeSegment]:
        """构建分集清单。

        - 第一集从 0 开始，最后一集到 video_duration
        - merged_points 为空时返回单集（整片为第一集，confidence=LOW）
        - 检查每个切割点的台词冲突

        Args:
            merged_points: 交叉验证后的分割点列表
            video_duration: 视频总时长（秒）
            subs: 字幕条目列表（可选），用于检测台词冲突

        Returns:
            EpisodeSegment 列表
        """
        if not merged_points:
            # 无分割点：整片作为单集
            conflict = False
            if subs:
                conflict = check_subtitle_conflict(0.0, subs)
            return [EpisodeSegment(
                episode_num=1,
                start_time=0.0,
                end_time=video_duration,
                confidence=ConfidenceLevel.LOW,
                has_subtitle_conflict=conflict
            )]

        # 构建起止时间列表
        all_starts = [0.0] + [mp.timestamp for mp in merged_points]
        all_ends = [mp.timestamp for mp in merged_points] + [video_duration]

        # 每集的置信度 = 该集结束分割点的置信度
        confidences = [mp.confidence for mp in merged_points] + [ConfidenceLevel.LOW]

        episodes: list[EpisodeSegment] = []
        for i, (start, end, conf) in enumerate(zip(all_starts, all_ends, confidences), 1):
            conflict = False
            conflict_detail = None

            if subs:
                # 检查起始点和结束点是否落在台词中间
                conflict = check_subtitle_conflict(start, subs)
                if i < len(all_starts):
                    conflict = conflict or check_subtitle_conflict(end, subs)

                if conflict:
                    # 获取冲突详情
                    for sub in subs:
                        if sub.start < end < sub.end:
                            conflict_detail = (
                                f"切割点 {end:.1f}s 落在台词中间: "
                                f"\"{sub.text}\" ({sub.start:.1f}s - {sub.end:.1f}s)"
                            )
                            break

            episodes.append(EpisodeSegment(
                episode_num=i,
                start_time=start,
                end_time=end,
                confidence=conf,
                has_subtitle_conflict=conflict,
                conflict_detail=conflict_detail
            ))

        logger.info(
            f"分集清单: {len(episodes)} 集, "
            f"冲突={sum(1 for e in episodes if e.has_subtitle_conflict)}"
        )

        return episodes
