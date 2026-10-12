"""融合决策引擎。

将 CandidateMerger → EpisodeAligner → ConfidenceScorer 三步串联
为一条完整的融合流水线，支持断点续传和多格式导出。
"""

import json
import logging
import os
from pathlib import Path
from typing import Dict, Optional

from drama_splitter.fusion.candidate_merger import CandidateMerger
from drama_splitter.fusion.confidence_scorer import ConfidenceScorer
from drama_splitter.fusion.episode_aligner import EpisodeAligner
from drama_splitter.models import CandidateList, TimeList
from drama_splitter.models.config import AppConfig

logger = logging.getLogger(__name__)


class FusionEngine:
    """融合决策引擎。

    Args:
        config: 全局配置对象。
    """

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self._merger = CandidateMerger(
            window_sec=config.cluster_window_sec,
        )
        self._aligner = EpisodeAligner()
        self._scorer = ConfidenceScorer(
            min_episode_sec=config.min_episode_sec,
            max_episode_sec=config.max_episode_sec,
        )

    def fuse(
        self,
        candidate_lists: Dict[str, CandidateList],
        duration_sec: float,
        video_name: str,
        work_dir: Optional[str] = None,
        fresh: bool = False,
    ) -> TimeList:
        """执行完整融合流水线。

        Args:
            candidate_lists: {signal_type: CandidateList} 字典。
            duration_sec: 视频时长。
            video_name: 视频文件名。
            work_dir: 工作目录（用于断点续传）。
            fresh: 若为 True，强制重新融合。

        Returns:
            最终时间清单。
        """
        # ── 断点续传 ──
        if work_dir and not fresh:
            checkpoint_path = os.path.join(work_dir, "time_list.json")
            if os.path.exists(checkpoint_path):
                logger.info("断点续传: 加载已有 time_list.json")
                with open(checkpoint_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                return TimeList.model_validate(data)

        logger.info("融合引擎: 开始候选点聚类...")
        boundaries = self._merger.merge(candidate_lists)
        logger.info("融合引擎: 聚类完成，%d 个合并边界", len(boundaries))

        logger.info("融合引擎: 开始集数对齐...")
        aligned = self._aligner.align(boundaries, duration_sec)
        logger.info("融合引擎: 对齐完成，%d 集", len(aligned))

        logger.info("融合引擎: 开始置信度评分...")
        time_list = self._scorer.score(aligned, duration_sec, video_name)
        logger.info("融合引擎: 评分完成，%d 集", len(time_list.episodes))

        # ── 写 checkpoint ──
        if work_dir:
            os.makedirs(work_dir, exist_ok=True)
            checkpoint_path = os.path.join(work_dir, "time_list.json")
            time_list.to_json(checkpoint_path)
            logger.info("融合引擎: 时间清单已保存至 %s", checkpoint_path)

        return time_list

    def fuse_from_files(
        self,
        candidate_dir: str,
        duration_sec: float,
        video_name: str,
        work_dir: Optional[str] = None,
        fresh: bool = False,
    ) -> TimeList:
        """从候选点 JSON 文件目录加载并融合。

        Args:
            candidate_dir: 存放 <signal_type>.json 的目录。
            duration_sec: 视频时长。
            video_name: 视频文件名。
            work_dir: 工作目录。
            fresh: 强制重新融合。

        Returns:
            最终时间清单。
        """
        candidate_lists: Dict[str, CandidateList] = {}
        signal_types = ["ocr", "audio_fingerprint", "black_silence", "scene_cluster"]

        for st in signal_types:
            json_path = os.path.join(candidate_dir, f"{st}.json")
            if not os.path.exists(json_path):
                logger.debug("缺少 %s，跳过", json_path)
                continue
            try:
                with open(json_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                candidate_lists[st] = CandidateList.model_validate(data)
                logger.debug("加载 %s: %d 候选点", st, len(candidate_lists[st].candidates))
            except Exception as e:
                logger.warning("加载 %s 失败: %s", json_path, e)

        if not candidate_lists:
            logger.warning("candidate_dir 中无有效候选点文件")
            # 返回空列表，让调用方处理
            return TimeList(video_name=video_name, duration_sec=duration_sec, episodes=[])

        return self.fuse(candidate_lists, duration_sec, video_name, work_dir, fresh)

    @staticmethod
    def export_json(time_list: TimeList, output_path: str) -> None:
        """导出 JSON 格式时间清单。

        Args:
            time_list: 时间清单。
            output_path: 输出文件路径。
        """
        time_list.to_json(output_path)
        logger.info("时间清单已导出 JSON: %s", output_path)

    @staticmethod
    def export_csv(time_list: TimeList, output_path: str) -> None:
        """导出 CSV 格式时间清单。

        Args:
            time_list: 时间清单。
            output_path: 输出文件路径。
        """
        time_list.to_csv(output_path)
        logger.info("时间清单已导出 CSV: %s", output_path)

    @staticmethod
    def export_summary(time_list: TimeList) -> str:
        """生成人类可读文本摘要。

        Args:
            time_list: 时间清单。

        Returns:
            格式化摘要字符串。
        """
        lines: list[str] = []
        lines.append("=" * 60)
        lines.append("  短剧分集时间清单")
        lines.append("=" * 60)
        lines.append("")

        if not time_list.episodes:
            lines.append("  (无集数)")
            return "\n".join(lines)

        high = sum(1 for e in time_list.episodes if e.confidence == "high")
        medium = sum(1 for e in time_list.episodes if e.confidence == "medium")
        low = sum(1 for e in time_list.episodes if e.confidence == "low")

        lines.append(f"  共 {len(time_list.episodes)} 集")
        lines.append(f"  高置信度: {high}  |  中置信度: {medium}  |  低置信度: {low}")
        lines.append("")

        header = f"  {'集数':>4s}  {'开始':>10s}  {'结束':>10s}  {'时长':>8s}  {'置信度':>6s}  {'信号':s}"
        lines.append(header)
        lines.append(f"  {'─' * 4}  {'─' * 10}  {'─' * 10}  {'─' * 8}  {'─' * 6}  {'─' * 20}")

        for ep in time_list.episodes:
            start_str = f"{int(ep.start_sec // 60):d}:{int(ep.start_sec % 60):02d}"
            end_str = f"{int(ep.end_sec // 60):d}:{int(ep.end_sec % 60):02d}"
            dur_str = f"{int(ep.duration_sec // 60):d}:{int(ep.duration_sec % 60):02d}"
            signals_str = ", ".join(ep.signals_hit[:3])
            confidence_mark = {"high": " ● ", "medium": " ○ ", "low": " △ "}.get(
                ep.confidence, " ? "
            )
            lines.append(
                f"  {ep.episode:>4d}  {start_str:>10s}  {end_str:>10s}  "
                f"{dur_str:>8s}  {confidence_mark}{ep.confidence:<4s}  {signals_str}"
            )

        lines.append("")
        lines.append("=" * 60)
        return "\n".join(lines)
