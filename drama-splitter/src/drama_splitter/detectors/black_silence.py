"""黑帧 + 静音联合检测器。

利用 FFmpeg 的 blackdetect 和 silencedetect 滤镜，找到视频中同时出现
黑帧和静音的时间段，两者的交集作为拼接缝的高置信候选点。
"""

import logging
from typing import List, Tuple

from drama_splitter.ffmpeg_wrapper import detect_black_silence
from drama_splitter.models import Candidate, CandidateList

logger = logging.getLogger(__name__)


class BlackSilenceDetector:
    """黑帧与静音联合检测器。

    Args:
        black_duration: 黑帧最短持续秒数，默认 0.3。
        black_pix_threshold: 黑帧像素阈值 (0–1)，默认 0.10。
        silence_noise: 静音噪声阈值，默认 "-40dB"。
        silence_duration: 静音最短持续秒数，默认 0.3。
    """

    def __init__(
        self,
        black_duration: float = 0.3,
        black_pix_threshold: float = 0.10,
        silence_noise: str = "-40dB",
        silence_duration: float = 0.3,
    ) -> None:
        self.black_duration = black_duration
        self.black_pix_threshold = black_pix_threshold
        self.silence_noise = silence_noise
        self.silence_duration = silence_duration

    def detect(
        self,
        video_path: str,
        duration_sec: float,
        video_name: str,
    ) -> CandidateList:
        """运行黑帧静音检测并输出候选点。

        Args:
            video_path: 视频文件路径。
            duration_sec: 视频时长。
            video_name: 视频文件名。

        Returns:
            黑帧静音候选点列表。
        """
        # ── 步骤 A：运行黑帧与静音检测 ──
        segments = detect_black_silence(
            video_path,
            black_duration=self.black_duration,
            black_pix_threshold=self.black_pix_threshold,
            silence_noise=self.silence_noise,
            silence_duration=self.silence_duration,
        )

        black_segments: List[Tuple[float, float]] = segments["black_segments"]
        silence_segments: List[Tuple[float, float]] = segments["silence_segments"]

        candidates: List[Candidate] = []

        if not black_segments and not silence_segments:
            logger.info("黑帧静音检测: 无黑帧段和静音段")
            return CandidateList(
                signal_type="black_silence",
                video_name=video_name,
                duration_sec=duration_sec,
                candidates=[],
            )

        # ── 步骤 B：计算交集 ──
        for b_start, b_end in black_segments:
            for s_start, s_end in silence_segments:
                if b_start < s_end and s_start < b_end:
                    # 有重叠
                    inter_start = max(b_start, s_start)
                    inter_end = min(b_end, s_end)
                    inter_duration = inter_end - inter_start
                    midpoint = (inter_start + inter_end) / 2
                    confidence = min(inter_duration / 1.0, 1.0)

                    candidates.append(
                        Candidate(
                            signal_type="black_silence",
                            timestamp_sec=midpoint,
                            episode=None,
                            confidence=confidence,
                            metadata={
                                "black_start": b_start,
                                "black_end": b_end,
                                "silence_start": s_start,
                                "silence_end": s_end,
                                "intersection_duration": inter_duration,
                            },
                        )
                    )

        # ── 步骤 C：降级处理（无交集但有单独信号） ──
        if not candidates:
            logger.info("黑帧静音: 无交集，使用降级模式")
            # 使用黑帧段中点
            for b_start, b_end in black_segments:
                midpoint = (b_start + b_end) / 2
                candidates.append(
                    Candidate(
                        signal_type="black_silence",
                        timestamp_sec=midpoint,
                        episode=None,
                        confidence=0.3,
                        metadata={"degraded": True, "source": "black_only"},
                    )
                )
            # 使用静音段中点
            for s_start, s_end in silence_segments:
                midpoint = (s_start + s_end) / 2
                candidates.append(
                    Candidate(
                        signal_type="black_silence",
                        timestamp_sec=midpoint,
                        episode=None,
                        confidence=0.3,
                        metadata={"degraded": True, "source": "silence_only"},
                    )
                )

        # ── 步骤 D：过滤 ──
        candidates = [
            c for c in candidates
            if 1.0 < c.timestamp_sec < duration_sec - 1.0
        ]

        # ── 步骤 E：合并距离 < 3 秒的候选点 ──
        candidates.sort(key=lambda c: c.timestamp_sec)
        merged: List[Candidate] = []
        for c in candidates:
            if merged and c.timestamp_sec - merged[-1].timestamp_sec < 3.0:
                # 取置信度更高的
                if c.confidence > merged[-1].confidence:
                    merged[-1] = c
            else:
                merged.append(c)

        logger.info("黑帧静音: %d 个候选点 (原始 %d)", len(merged), len(candidates))
        return CandidateList(
            signal_type="black_silence",
            video_name=video_name,
            duration_sec=duration_sec,
            candidates=merged,
        )
