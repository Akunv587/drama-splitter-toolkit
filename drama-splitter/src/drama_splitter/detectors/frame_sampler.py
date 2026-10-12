"""视频抽帧模块。

将长视频按固定时间间隔抽取缩略帧序列，供 OCR 和场景检测共用。
"""

import logging
import os
import shutil
from dataclasses import dataclass
from typing import List

from drama_splitter.exceptions import FrameExtractionError
from drama_splitter.ffmpeg_wrapper import extract_frames, probe_video

logger = logging.getLogger(__name__)


@dataclass
class Frame:
    """单帧信息。"""

    path: str
    """帧图片文件路径。"""
    timestamp_sec: float
    """该帧在视频中的时间位置（秒）。"""


class FrameSampler:
    """按固定间隔抽取视频帧。

    Args:
        interval_sec: 抽帧间隔（秒），默认 0.5。
        frame_width: 缩放宽度（像素），默认 640。
    """

    def __init__(self, interval_sec: float = 0.5, frame_width: int = 640) -> None:
        self.interval_sec = interval_sec
        self.frame_width = frame_width

    def sample(self, video_path: str, output_dir: str) -> List[Frame]:
        """抽取帧并返回带时间戳的帧列表。

        Args:
            video_path: 视频文件路径。
            output_dir: 帧图片输出目录。

        Returns:
            按时间戳升序排列的 ``Frame`` 列表。

        Raises:
            FrameExtractionError: 抽帧失败或结果为空。
        """
        import time

        start_time = time.monotonic()

        # 探测视频信息
        meta = probe_video(video_path)
        logger.info(
            "开始抽帧: %s (时长 %.1fs, 间隔 %.2fs)",
            meta.file_name,
            meta.duration_sec,
            self.interval_sec,
        )

        # 清空已有帧文件
        frames_dir = os.path.join(output_dir, "frames")
        if os.path.exists(frames_dir):
            shutil.rmtree(frames_dir)
        os.makedirs(frames_dir, exist_ok=True)

        # 调用底层抽帧
        raw_frames = extract_frames(
            video_path,
            frames_dir,
            interval_sec=self.interval_sec,
            width=self.frame_width,
        )

        if not raw_frames:
            raise FrameExtractionError(
                f"抽帧结果为空。视频: {video_path}, 间隔: {self.interval_sec}s"
            )

        # 构造 Frame 列表
        frames = [Frame(path=p, timestamp_sec=ts) for p, ts in raw_frames]

        elapsed = time.monotonic() - start_time
        logger.info(
            "抽帧完成: %d 帧, 耗时 %.1fs, 平均 %.3fs/帧",
            len(frames),
            elapsed,
            elapsed / len(frames) if frames else 0,
        )

        return frames
