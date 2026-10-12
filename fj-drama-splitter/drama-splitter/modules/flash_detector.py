"""视频场景变化检测器

通过 ffmpeg scene 滤镜检测画面突变作为闪白等效信号，速度比 OpenCV
逐帧读取快 10-50 倍。OpenCV 作为降级兜底。
"""

import logging
import subprocess
import re
import os
from typing import Callable
from modules.exceptions import FileFormatError

logger = logging.getLogger(__name__)

DEFAULT_SCENE_THRESHOLD = 0.3
DEFAULT_MERGE_DISTANCE = 2.0
DEFAULT_MIN_EPISODE_DURATION = 30.0

# 进度回调类型: (percent: int, message: str) -> None
ProgressCallback = Callable[[int, str], None] | None


class FlashDetector:
    """视频场景变化检测器（闪白等效信号）"""

    def __init__(self, config: dict | None = None):
        cfg = config or {}
        self.scene_threshold: float = cfg.get(
            "scene_threshold", cfg.get("white_threshold", DEFAULT_SCENE_THRESHOLD) / 220 * 0.5
        )
        self.merge_distance: float = cfg.get("merge_distance", DEFAULT_MERGE_DISTANCE)
        self.min_episode_duration: float = cfg.get(
            "min_episode_duration", DEFAULT_MIN_EPISODE_DURATION
        )

    def detect(
        self,
        video_path: str,
        progress_callback: ProgressCallback = None,
    ) -> list[float]:
        """主入口：检测场景变化时间戳。

        优先使用 ffmpeg scene 滤镜，失败时降级到 OpenCV。

        Args:
            video_path: 视频文件路径
            progress_callback: 进度回调 (percent, message)

        Returns:
            候选分割点时间戳列表（秒），按时间排序
        """
        logger.info(f"开始场景检测: {video_path}")

        # 获取视频时长用于进度
        duration = self._get_duration(video_path)

        try:
            result = self._detect_via_ffmpeg(video_path, duration, progress_callback)
            logger.info(f"ffmpeg 场景检测完成: {len(result)} 个点")
        except Exception as e:
            logger.warning(f"ffmpeg 场景检测失败，降级到 OpenCV: {e}")
            result = self._detect_via_opencv(video_path, duration, progress_callback)
            logger.info(f"OpenCV 场景检测完成: {len(result)} 个点")

        # 合并邻近候选点
        merged = self._merge_nearby(result, self.merge_distance)
        # 过滤误检
        filtered = self._filter_by_min_duration(merged, self.min_episode_duration)
        logger.info(f"场景检测完成: 最终 {len(filtered)} 个分割点")
        return filtered

    # ── ffmpeg scene detect ─────────────────────────────────────

    def _detect_via_ffmpeg(
        self,
        video_path: str,
        duration: float,
        progress_callback: ProgressCallback,
    ) -> list[float]:
        """使用 ffmpeg scene 滤镜检测场景变化。"""
        if progress_callback:
            progress_callback(0, "ffmpeg 场景分析启动...")

        # 仅解码关键帧 + 降采样到 80px + 1fps → 10-20 秒处理 1 小时视频
        cmd = [
            "ffmpeg", "-y",
            "-skip_frame", "nokey",     # 只读关键帧，50-100x 加速
            "-i", video_path,
            "-vf", f"scale=80:-2,select='gt(scene\\,{self.scene_threshold})',showinfo",
            "-vsync", "vfr",
            "-f", "null", "NUL",
        ]

        # 使用 subprocess 读取 stderr 获取进度和场景信息
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

        scenes: list[float] = []
        time_pattern = re.compile(r"time=(\d+):(\d+):(\d+)\.(\d+)")
        pts_pattern = re.compile(r"pts_time:(\d+\.?\d*)")

        for line in proc.stderr:
            # 解析进度
            m = time_pattern.search(line)
            if m and duration > 0:
                h, mnt, s, cs = int(m[1]), int(m[2]), int(m[3]), int(m[4])
                current = h * 3600 + mnt * 60 + s + cs / 100.0
                pct = min(99, int(current / duration * 100))
                if progress_callback:
                    progress_callback(pct, f"场景分析中... {pct}%")

            # 解析场景时间戳
            m = pts_pattern.search(line)
            if m:
                ts = float(m[1])
                if ts > 0.5:  # 忽略开头的误检
                    scenes.append(ts)

        proc.wait()

        if proc.returncode != 0 and not scenes:
            raise RuntimeError(f"ffmpeg 退出码: {proc.returncode}")

        if progress_callback:
            progress_callback(100, f"场景检测完成 ({len(scenes)} 个)")

        return sorted(set(scenes))

    # ── OpenCV fallback (优化版) ────────────────────────────────

    def _detect_via_opencv(
        self,
        video_path: str,
        duration: float,
        progress_callback: ProgressCallback,
    ) -> list[float]:
        """OpenCV 降级方案：使用 grab() 跳帧加速。"""
        import cv2

        if progress_callback:
            progress_callback(0, "OpenCV 帧采样中...")

        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            raise FileFormatError(f"无法打开视频文件: {video_path}")

        try:
            fps = cap.get(cv2.CAP_PROP_FPS)
            if fps <= 0:
                fps = 24.0

            total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            sample_every = max(1, int(fps / 2))  # 每秒采样 2 帧

            brightness_series: list[tuple[float, float]] = []
            prev_bright = 0
            frame_idx = 0

            while True:
                if frame_idx % sample_every == 0:
                    ret, frame = cap.read()
                    if not ret:
                        break
                    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                    bright = cv2.mean(gray)[0]
                    ts = frame_idx / fps
                    brightness_series.append((ts, bright))
                    prev_bright = bright
                else:
                    # 跳帧：grab 不解码比 read 快很多
                    if not cap.grab():
                        break

                frame_idx += 1

                # 进度报告
                if progress_callback and total_frames > 0 and frame_idx % 100 == 0:
                    pct = min(99, int(frame_idx / total_frames * 100))
                    progress_callback(pct, f"帧采样中... {pct}%")

            # 检测尖峰（亮度变化 > 40 且亮度 > 150）
            candidates: list[float] = []
            for i in range(1, len(brightness_series)):
                ts, bright = brightness_series[i]
                _, prev = brightness_series[i - 1]
                delta = bright - prev
                if bright > 150 and delta > 40:
                    candidates.append(ts)

            if progress_callback:
                progress_callback(100, f"OpenCV 检测完成 ({len(candidates)} 个)")

            return candidates

        finally:
            cap.release()

    # ── 合并 & 过滤 (与旧版兼容) ────────────────────────────────

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
    def _get_duration(video_path: str) -> float:
        """用 ffprobe 快速获取视频时长。"""
        try:
            startupinfo = None
            if os.name == "nt":
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

            result = subprocess.run(
                [
                    "ffprobe", "-v", "quiet", "-show_entries",
                    "format=duration", "-of", "csv=p=0", video_path
                ],
                capture_output=True, text=True, timeout=10,
                startupinfo=startupinfo,
            )
            return float(result.stdout.strip())
        except Exception:
            return 0.0
