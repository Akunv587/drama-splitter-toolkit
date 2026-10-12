"""音频静音检测器

通过 ffmpeg silencedetect 滤镜检测静音区间，速度比 librosa 全量
加载快 5-20 倍。支持所有 ffmpeg 可解码的音频格式（MP3/M4A/AAC/WAV/FLAC）。
librosa 作为降级兜底。
"""

import logging
import subprocess
import re
import os
from typing import Callable
from modules.exceptions import FileFormatError

logger = logging.getLogger(__name__)

DEFAULT_SILENCE_THRESHOLD = -30        # dB
DEFAULT_MIN_SILENCE_DURATION = 0.5     # 秒
DEFAULT_MAX_SILENCE_DURATION = 10.0    # 秒
DEFAULT_MERGE_DISTANCE = 2.0
DEFAULT_MIN_EPISODE_DURATION = 30.0

# 支持的音频扩展名
SUPPORTED_AUDIO_EXTS = {".mp3", ".m4a", ".aac", ".wav", ".flac", ".ogg", ".wma", ".opus"}

ProgressCallback = Callable[[int, str], None] | None


class SilenceDetector:
    """音频静音检测器"""

    def __init__(self, config: dict | None = None):
        cfg = config or {}
        self.silence_threshold: float = cfg.get(
            "silence_threshold", DEFAULT_SILENCE_THRESHOLD
        )
        self.min_silence_duration: float = cfg.get(
            "min_silence_duration", DEFAULT_MIN_SILENCE_DURATION
        )
        self.max_silence_duration: float = cfg.get(
            "max_silence_duration", DEFAULT_MAX_SILENCE_DURATION
        )
        self.merge_distance: float = cfg.get("merge_distance", DEFAULT_MERGE_DISTANCE)
        self.min_episode_duration: float = cfg.get(
            "min_episode_duration", DEFAULT_MIN_EPISODE_DURATION
        )

    def detect(
        self,
        audio_path: str,
        progress_callback: ProgressCallback = None,
    ) -> list[float]:
        """主入口：检测静音区间中点。

        优先使用 ffmpeg silencedetect，失败时降级到 librosa。
        自动处理视频文件（提取音频流）。

        Args:
            audio_path: 音频或视频文件路径
            progress_callback: 进度回调 (percent, message)

        Returns:
            候选分割点时间戳列表（秒），按时间排序
        """
        logger.info(f"开始静音检测: {audio_path}")

        duration = self._get_duration(audio_path)

        try:
            result = self._detect_via_ffmpeg(audio_path, duration, progress_callback)
            logger.info(f"ffmpeg 静音检测完成: {len(result)} 个点")
        except Exception as e:
            logger.warning(f"ffmpeg 静音检测失败，降级到 librosa: {e}")
            result = self._detect_via_librosa(audio_path, duration, progress_callback)
            logger.info(f"librosa 静音检测完成: {len(result)} 个点")

        # 合并邻近候选点
        merged = self._merge_nearby(result, self.merge_distance)
        # 过滤误检
        filtered = self._filter_by_min_duration(merged, self.min_episode_duration)
        logger.info(f"静音检测完成: 最终 {len(filtered)} 个分割点")
        return filtered

    # ── ffmpeg silencedetect ────────────────────────────────────

    def _detect_via_ffmpeg(
        self,
        audio_path: str,
        duration: float,
        progress_callback: ProgressCallback,
    ) -> list[float]:
        """使用 ffmpeg silencedetect 滤镜。"""
        if progress_callback:
            progress_callback(0, "ffmpeg 静音分析启动...")

        # silencedetect 参数
        noise_db = self.silence_threshold
        min_dur = self.min_silence_duration

        cmd = [
            "ffmpeg", "-y",
            "-vn",                # 跳过视频流解码
            "-i", audio_path,
            "-ar", "16000",       # 16kHz 采样率加速
            "-ac", "1",           # 单声道加速
            "-af", f"silencedetect=n={noise_db}dB:d={min_dur}",
            "-f", "null", "NUL",
        ]

        startupinfo = None
        if os.name == "nt":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            errors="replace",
            startupinfo=startupinfo,
        )

        # 收集静音区间
        silence_starts: list[float] = []
        silence_ends: list[float] = []
        time_pattern = re.compile(r"time=(\d+):(\d+):(\d+)\.(\d+)")
        start_pattern = re.compile(r"silence_start:\s*([\d.]+)")
        end_pattern = re.compile(r"silence_end:\s*([\d.]+)")

        for line in proc.stderr:
            # 进度
            m = time_pattern.search(line)
            if m and duration > 0:
                h, mnt, s, cs = int(m[1]), int(m[2]), int(m[3]), int(m[4])
                current = h * 3600 + mnt * 60 + s + cs / 100.0
                pct = min(99, int(current / duration * 100))
                if progress_callback and pct % 10 == 0:
                    progress_callback(pct, f"静音分析中... {pct}%")

            # 静音区间
            m = start_pattern.search(line)
            if m:
                silence_starts.append(float(m[1]))
            m = end_pattern.search(line)
            if m:
                silence_ends.append(float(m[1]))

        proc.wait()

        # 配对 start/end，过滤过长/过短的静音
        midpoints: list[float] = []
        for s, e in zip(silence_starts, silence_ends):
            dur = e - s
            if self.min_silence_duration <= dur <= self.max_silence_duration:
                midpoints.append((s + e) / 2)

        if progress_callback:
            progress_callback(100, f"静音检测完成 ({len(midpoints)} 个)")

        return sorted(midpoints)

    # ── librosa fallback ────────────────────────────────────────

    def _detect_via_librosa(
        self,
        audio_path: str,
        duration: float,
        progress_callback: ProgressCallback,
    ) -> list[float]:
        """librosa 降级方案。先通过 ffmpeg 降采样为 16kHz mono 加速。"""
        import numpy as np

        if progress_callback:
            progress_callback(0, "librosa 加载音频...")

        # 尝试用 ffmpeg 降采样后通过管道传给 librosa（比直接读取快 3-5x）
        try:
            y, sr = self._load_audio_optimized(audio_path)
        except Exception:
            if progress_callback:
                progress_callback(30, "librosa 直接加载...")
            import librosa
            y, sr = librosa.load(audio_path, sr=None)

        if progress_callback:
            progress_callback(50, f"计算 RMS ({len(y)} 样本, {sr}Hz)...")

        import librosa
        rms = librosa.feature.rms(
            y=y, frame_length=2048, hop_length=512
        )[0]

        times = librosa.frames_to_time(
            np.arange(len(rms)), sr=sr, hop_length=512
        )

        # 检测静音区间
        is_silent = rms < 0.01
        silence_regions: list[tuple[float, float]] = []
        in_silence = False
        start = 0.0

        for i, silent in enumerate(is_silent):
            if silent and not in_silence:
                start = float(times[i])
                in_silence = True
            elif not silent and in_silence:
                end = float(times[i])
                dur = end - start
                if self.min_silence_duration <= dur <= self.max_silence_duration:
                    silence_regions.append((start, end))
                in_silence = False

        if in_silence:
            end = float(times[-1])
            dur = end - start
            if self.min_silence_duration <= dur <= self.max_silence_duration:
                silence_regions.append((start, end))

        midpoints = [(s + e) / 2 for s, e in silence_regions]

        if progress_callback:
            progress_callback(100, f"librosa 检测完成 ({len(midpoints)} 个)")

        return sorted(midpoints)

    @staticmethod
    def _load_audio_optimized(audio_path: str) -> tuple:
        """通过 ffmpeg 降采样为 16kHz mono 后由 librosa 加载（piped）。"""
        import librosa
        import numpy as np
        import io
        import tempfile

        # ffmpeg 输出 16kHz mono wav
        startupinfo = None
        if os.name == "nt":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

        proc = subprocess.Popen(
            [
                "ffmpeg", "-y",
                "-i", audio_path,
                "-ar", "16000",
                "-ac", "1",
                "-f", "wav",
                "pipe:1",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            startupinfo=startupinfo,
        )

        raw_data = proc.stdout.read()
        proc.wait()

        # 通过临时文件传给 librosa（soundfile 后端需要 seekable）
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp.write(raw_data)
            tmp.flush()
            y, sr = librosa.load(tmp.name, sr=16000)

        os.unlink(tmp.name)
        return y, sr

    # ── 合并 & 过滤 ─────────────────────────────────────────────

    @staticmethod
    def _merge_nearby(points: list[float], merge_distance: float) -> list[float]:
        if not points:
            return []
        points = sorted(points)
        merged: list[float] = []
        cluster_start = points[0]
        for i in range(1, len(points)):
            if points[i] - points[i - 1] >= merge_distance:
                merged.append((cluster_start + points[i - 1]) / 2)
                cluster_start = points[i]
        merged.append((cluster_start + points[-1]) / 2)
        return merged

    @staticmethod
    def _filter_by_min_duration(
        points: list[float], min_duration: float
    ) -> list[float]:
        if not points:
            return []
        points = sorted(points)
        filtered: list[float] = [points[0]]
        for pt in points[1:]:
            if pt - filtered[-1] >= min_duration:
                filtered.append(pt)
        return filtered

    @staticmethod
    def _get_duration(file_path: str) -> float:
        """用 ffprobe 快速获取时长。"""
        try:
            startupinfo = None
            if os.name == "nt":
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

            result = subprocess.run(
                [
                    "ffprobe", "-v", "quiet", "-show_entries",
                    "format=duration", "-of", "csv=p=0", file_path
                ],
                capture_output=True, text=True, timeout=10,
                startupinfo=startupinfo,
            )
            return float(result.stdout.strip())
        except Exception:
            return 0.0
