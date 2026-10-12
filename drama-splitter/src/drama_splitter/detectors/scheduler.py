"""信号采集层并行调度引擎。

将 OCR、音频指纹、黑帧静音、场景聚类四个检测器按顺序调度执行，
OCR 先行（获取 episode 信息的核心信号），后三个可并行执行。
统一汇总为 CandidateList 列表，提供给融合层使用。
"""

import logging
import os
import tempfile
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional

from drama_splitter.detectors.audio_fingerprint import AudioFingerprintDetector
from drama_splitter.detectors.black_silence import BlackSilenceDetector
from drama_splitter.detectors.episode_matcher import EpisodeMatcher
from drama_splitter.detectors.frame_sampler import FrameSampler
from drama_splitter.detectors.ocr_detector import OCRDetector
from drama_splitter.detectors.scene_cluster import SceneClusterDetector
from drama_splitter.exceptions import DetectionError
from drama_splitter.ffmpeg_wrapper import probe_video
from drama_splitter.models import CandidateList, VideoMeta
from drama_splitter.models.config import AppConfig

logger = logging.getLogger(__name__)


class SignalScheduler:
    """信号采集调度器。

    按以下顺序执行：
    1. 同步：视频探测 (probe)
    2. 同步：帧采样 (OCR 先行)
    3. 并行：音频指纹、黑帧静音、场景聚类

    Args:
        config: 全局配置。
        video_path: 输入视频路径。
        output_dir: 输出目录（用于中间文件）。
    """

    def __init__(
        self,
        config: AppConfig,
        video_path: str,
        output_dir: Optional[str] = None,
    ) -> None:
        self.config = config
        self.video_path = os.path.abspath(video_path)
        self.output_dir = output_dir or tempfile.mkdtemp(
            prefix="ds_work_", dir=os.path.dirname(self.video_path) or "."
        )
        self.video_name = os.path.basename(self.video_path)

    def run(self) -> Dict[str, CandidateList]:
        """运行全信号采集流水线。

        Returns:
            字典，key 为 signal_type，value 为 CandidateList。

        Raises:
            DetectionError: 所有信号均失败时抛出。
        """
        logger.info("开始信号采集: %s", self.video_path)

        # ═════════════════════════════════════════════
        # 步骤 A：视频探测
        # ═════════════════════════════════════════════
        logger.info("信号采集 [1/4] 视频探测...")
        try:
            meta = probe_video(self.video_path)
        except Exception as e:
            raise DetectionError(
                f"视频探测失败: {e}",
                signal_type="probe",
            ) from e

        duration_sec = meta.duration_sec
        logger.info("视频: %s, %.1fs, %dx%d", self.video_name, duration_sec, meta.width, meta.height)

        results: Dict[str, CandidateList] = {}

        # ═════════════════════════════════════════════
        # 步骤 B：OCR 先行（主信号）
        # ═════════════════════════════════════════════
        logger.info("信号采集 [2/4] OCR 检测...")
        ocr_result = self._run_ocr(meta)
        if ocr_result is not None:
            results["ocr"] = ocr_result
            logger.info("OCR: %d 候选点", len(ocr_result.candidates))
        else:
            logger.warning("OCR 检测未产生结果")

        # ═════════════════════════════════════════════
        # 步骤 C：并行检测器
        # ═════════════════════════════════════════════
        logger.info("信号采集 [3/4] 并行检测 (音频/黑帧静音/场景)...")
        parallel_results = self._run_parallel(meta)
        for signal_type, cl in parallel_results.items():
            if cl is not None and cl.candidates:
                results[signal_type] = cl
                logger.info(
                    "%s: %d 候选点", signal_type, len(cl.candidates)
                )
            elif cl is not None:
                logger.debug("%s: 无候选点", signal_type)

        # ═════════════════════════════════════════════
        # 步骤 D：汇总检查
        # ═════════════════════════════════════════════
        logger.info("信号采集 [4/4] 汇总...")
        total_candidates = sum(
            len(cl.candidates) for cl in results.values()
        )
        logger.info("信号采集完成: %d 个信号源, 共 %d 个候选点",
                     len(results), total_candidates)

        if not results or total_candidates == 0:
            raise DetectionError(
                "所有信号检测器均未产生候选点，无法继续分析。\n"
                "可能原因: 视频无音轨、画面无变化、或参数阈值过高。",
                signal_type="scheduler",
            )

        return results

    def _run_ocr(self, meta: VideoMeta) -> Optional[CandidateList]:
        """同步运行 OCR 检测流水线。

        Args:
            meta: 视频元信息。

        Returns:
            OCR 候选点列表，失败返回 None。
        """
        try:
            sampler = FrameSampler(
                sample_interval_sec=self.config.frame_interval_sec,
            )
            ocr = OCRDetector(
                engine=self.config.ocr_engine,
            )
            matcher = EpisodeMatcher(
                vote_window_sec=self.config.cluster_window_sec,
                min_votes=2,
                single_vote_threshold=0.8,
            )

            ocr_result = matcher.match(
                sampler.sample(
                    self.video_path,
                ),
                ocr,
                duration_sec=meta.duration_sec,
                video_name=self.video_name,
            )

            return ocr_result

        except Exception as e:
            logger.warning("OCR 检测失败: %s", e)
            return None

    def _run_parallel(self, meta: VideoMeta) -> Dict[str, Optional[CandidateList]]:
        """并行运行音频指纹、黑帧静音、场景聚类。

        Args:
            meta: 视频元信息。

        Returns:
            {signal_type: CandidateList} 字典。
        """
        results: Dict[str, Optional[CandidateList]] = {
            "audio_fingerprint": None,
            "black_silence": None,
            "scene_cluster": None,
        }

        with ThreadPoolExecutor(max_workers=3) as executor:
            futures: Dict[Future, str] = {}

            # 提交音频指纹
            af_detector = AudioFingerprintDetector(
                template_sec=self.config.fingerprint_template_sec,
                threshold=self.config.fingerprint_threshold,
            )
            futures[
                executor.submit(
                    af_detector.detect,
                    self.video_path,
                    meta.duration_sec,
                    self.video_name,
                    self.output_dir,
                )
            ] = "audio_fingerprint"

            # 提交黑帧静音
            bs_detector = BlackSilenceDetector(
                black_duration=self.config.black_duration,
                black_pix_threshold=self.config.black_pix_threshold,
                silence_noise=self.config.silence_noise,
                silence_duration=self.config.silence_duration,
            )
            futures[
                executor.submit(
                    bs_detector.detect,
                    self.video_path,
                    meta.duration_sec,
                    self.video_name,
                )
            ] = "black_silence"

            # 提交场景聚类
            sc_detector = SceneClusterDetector(
                min_episode_sec=self.config.min_episode_sec,
                max_episode_sec=self.config.max_episode_sec,
            )
            futures[
                executor.submit(
                    sc_detector.detect,
                    self.video_path,
                    meta.duration_sec,
                    self.video_name,
                )
            ] = "scene_cluster"

            # 收集结果
            for future in as_completed(futures):
                signal_type = futures[future]
                try:
                    result = future.result()
                    results[signal_type] = result
                except Exception as e:
                    logger.warning("%s 检测失败: %s", signal_type, e)

        return results
