"""Chromaprint 音频指纹片头检测器。

提取整条音轨的 Chromaprint 指纹，通过滑窗自相关找到片头音乐重复出现的
时间点，每个重复点对应一集的起始位置。
"""

import json
import logging
import os
import shutil
import subprocess
from typing import List, Tuple

import numpy as np

from drama_splitter.exceptions import ChromaprintNotFoundError
from drama_splitter.ffmpeg_wrapper import extract_audio
from drama_splitter.models import Candidate, CandidateList

logger = logging.getLogger(__name__)

_FPCALC = shutil.which("fpcalc")


def fingerprint_similarity(window: np.ndarray, template: np.ndarray) -> float:
    """计算两段 Chromaprint 指纹的位级相似度。

    Chromaprint 指纹是 32 位整数数组，每个整数是一个子指纹。
    相似度 = 1 - 位错误率（BER），BER = 不同位数 / 总位数。

    Args:
        window: 窗口指纹数组 (dtype=uint32)。
        template: 模板指纹数组 (dtype=uint32)。

    Returns:
        相似度 (0.0–1.0)。
    """
    if len(window) != len(template):
        return 0.0
    if len(window) == 0:
        return 1.0
    xor_result = np.bitwise_xor(
        window.astype(np.uint32), template.astype(np.uint32)
    )
    try:
        bit_counts = np.bitwise_count(xor_result)  # NumPy 2.0+
    except AttributeError:
        bit_counts = np.vectorize(
            lambda x: bin(int(x) & 0xFFFFFFFF).count("1")
        )(xor_result)
    total_bits = len(window) * 32
    total_errors = int(np.sum(bit_counts))
    return 1.0 - total_errors / total_bits


class AudioFingerprintDetector:
    """音频指纹片头检测器。

    提取音轨指纹 → 取开头为模板 → 滑窗 Hamming 距离匹配 → 峰值检测。

    Args:
        template_sec: 片头模板长度（秒），默认 20。
        threshold: 指纹相似度阈值 (0–1)，默认 0.85。
    """

    def __init__(
        self,
        template_sec: float = 20.0,
        threshold: float = 0.85,
    ) -> None:
        self.template_sec = template_sec
        self.threshold = threshold

    def detect(
        self,
        video_path: str,
        duration_sec: float,
        video_name: str,
        work_dir: str,
    ) -> CandidateList:
        """提取指纹、滑窗匹配、输出候选点。

        Args:
            video_path: 视频文件路径。
            duration_sec: 视频时长。
            video_name: 视频文件名。
            work_dir: 工作目录（用于存放中间产物）。

        Returns:
            音频指纹候选点列表。
        """
        if _FPCALC is None:
            raise ChromaprintNotFoundError(
                "fpcalc 未安装或不在 PATH 中。\n"
                "macOS: brew install chromaprint\n"
                "Ubuntu: apt install libchromaprint-tools"
            )

        os.makedirs(work_dir, exist_ok=True)

        # ── 步骤 A：提取音轨 ──
        wav_path = os.path.join(work_dir, "audio.wav")
        try:
            extract_audio(video_path, wav_path)
        except Exception as e:
            logger.warning("音轨提取失败，音频指纹检测跳过: %s", e)
            return CandidateList(
                signal_type="audio_fingerprint",
                video_name=video_name,
                duration_sec=duration_sec,
                candidates=[],
            )

        # ── 步骤 B：生成指纹序列 ──
        try:
            fp_full, fingerprint_rate = self._generate_fingerprint(wav_path)
        except Exception as e:
            logger.warning("指纹生成失败: %s", e)
            return CandidateList(
                signal_type="audio_fingerprint",
                video_name=video_name,
                duration_sec=duration_sec,
                candidates=[],
            )

        if len(fp_full) == 0:
            logger.warning("指纹数组为空，音频指纹检测跳过")
            return CandidateList(
                signal_type="audio_fingerprint",
                video_name=video_name,
                duration_sec=duration_sec,
                candidates=[],
            )

        logger.info("音频指纹: %d 个子指纹, 速率 %.2f/s", len(fp_full), fingerprint_rate)

        # ── 步骤 C：提取片头模板 + 有效性校验 ──
        template_len = int(self.template_sec * fingerprint_rate)
        if template_len >= len(fp_full):
            logger.warning("模板长度 (%d) >= 指纹长度 (%d)，跳过", template_len, len(fp_full))
            return CandidateList(
                signal_type="audio_fingerprint",
                video_name=video_name,
                duration_sec=duration_sec,
                candidates=[],
            )

        template = fp_full[:template_len]
        # 模板有效性校验：与紧随其后的等长片段比对
        if template_len * 2 <= len(fp_full):
            next_segment = fp_full[template_len : template_len * 2]
            sim = fingerprint_similarity(template, next_segment)
            logger.info("片头模板自校验相似度: %.2f", sim)
            if sim < 0.5:
                logger.warning("片头模板自校验相似度过低 (%.2f < 0.5)，可能无固定片头", sim)
                return CandidateList(
                    signal_type="audio_fingerprint",
                    video_name=video_name,
                    duration_sec=duration_sec,
                    candidates=[],
                )

        # ── 步骤 D：滑窗匹配 ──
        sims = self._sliding_window_match(fp_full, template, fingerprint_rate)
        if not sims:
            return CandidateList(
                signal_type="audio_fingerprint",
                video_name=video_name,
                duration_sec=duration_sec,
                candidates=[],
            )

        # ── 步骤 E：峰值检测 ──
        peaks = self._peak_detect(sims)
        if not peaks:
            logger.info("音频指纹未检测到显著峰值")
            return CandidateList(
                signal_type="audio_fingerprint",
                video_name=video_name,
                duration_sec=duration_sec,
                candidates=[],
            )

        # ── 步骤 F：构造 CandidateList ──
        candidates = []
        for rank, (ts, sim) in enumerate(peaks):
            candidates.append(
                Candidate(
                    signal_type="audio_fingerprint",
                    timestamp_sec=ts,
                    episode=None,
                    confidence=sim,
                    metadata={"similarity": sim, "peak_rank": rank + 1},
                )
            )

        logger.info("音频指纹: %d 个候选峰值", len(candidates))
        return CandidateList(
            signal_type="audio_fingerprint",
            video_name=video_name,
            duration_sec=duration_sec,
            candidates=candidates,
        )

    def _generate_fingerprint(self, wav_path: str) -> Tuple[np.ndarray, float]:
        """调用 fpcalc 生成原始指纹。"""
        cmd = [_FPCALC, "-raw", "-json", wav_path]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"fpcalc 执行失败: {result.stderr.strip()}")

        data = json.loads(result.stdout)
        duration = float(data.get("duration", 0))
        fp_array = np.array(data.get("fingerprint", []), dtype=np.uint32)
        rate = len(fp_array) / duration if duration > 0 else 0.0
        return fp_array, rate

    def _sliding_window_match(
        self,
        fp_full: np.ndarray,
        template: np.ndarray,
        fingerprint_rate: float,
    ) -> List[Tuple[float, float]]:
        """滑窗计算每个偏移的相似度，返回 [(timestamp, similarity), ...]。"""
        template_len = len(template)
        total = len(fp_full) - template_len
        if total <= 0:
            return []

        sims: List[Tuple[float, float]] = []
        # 步长：每 ~0.5 秒采样一次以加速
        step = max(1, int(fingerprint_rate * 0.5))
        for off in range(0, total, step):
            window = fp_full[off : off + template_len]
            sim = fingerprint_similarity(window, template)
            ts = off / fingerprint_rate
            sims.append((ts, sim))
        return sims

    def _peak_detect(
        self,
        sims: List[Tuple[float, float]],
    ) -> List[Tuple[float, float]]:
        """峰值检测：过滤低于阈值的，取局部极大值，合并邻近峰值。"""
        # 过滤
        above = [(ts, s) for ts, s in sims if s >= self.threshold]
        if not above:
            return []

        # 局部极大值 (时间窗口 ±10s)
        peak_window_sec = 10.0
        peaks: List[Tuple[float, float]] = []
        for i, (ts, s) in enumerate(above):
            is_peak = True
            # 检查时间窗口内的邻点
            for other_ts, other_s in above:
                if other_ts == ts:
                    continue
                if abs(other_ts - ts) <= peak_window_sec and other_s >= s:
                    is_peak = False
                    break
            if is_peak:
                peaks.append((ts, s))

        # 跳过第一个峰值（offset ≈ 0，即片头自身）
        if peaks and peaks[0][0] < 5.0:
            peaks = peaks[1:]

        # 合并距离 < 30 秒的峰值（保留相似度最高的）
        merged: List[Tuple[float, float]] = []
        for ts, s in peaks:
            if merged and ts - merged[-1][0] < 30:
                if s > merged[-1][1]:
                    merged[-1] = (ts, s)
            else:
                merged.append((ts, s))

        return merged
