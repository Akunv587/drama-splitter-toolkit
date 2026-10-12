"""视频裁剪引擎

基于 ffmpeg -c copy 无损流拷贝模式裁剪视频。
"""

import os
import subprocess
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from modules.models import EpisodeSegment, CutResult
from modules.exceptions import CutError

logger = logging.getLogger(__name__)


class VideoCutter:
    """视频裁剪引擎"""

    def __init__(self, max_workers: int | None = None):
        """初始化。

        Args:
            max_workers: 并发裁剪数，默认为 CPU 核心数 - 1（至少为 1）
        """
        import os as _os
        if max_workers is None:
            self.max_workers = max(1, (_os.cpu_count() or 2) - 1)
        else:
            self.max_workers = max_workers

    def cut_single(
        self, video_path: str, segment: EpisodeSegment, output_path: str
    ) -> CutResult:
        """裁剪单集视频。

        使用 ffmpeg -c copy 无损模式。

        Args:
            video_path: 输入视频路径
            segment: 分集信息
            output_path: 输出文件路径

        Returns:
            CutResult（包含 success/error 信息）
        """
        start = segment.start_time
        end = segment.end_time

        cmd = [
            "ffmpeg", "-y",
            "-i", video_path,
            "-ss", str(start),
            "-to", str(end),
            "-c", "copy",
            "-avoid_negative_ts", "make_zero",
            output_path
        ]

        logger.debug(f"裁剪命令: {' '.join(cmd)}")

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=600  # 10 分钟超时
            )

            if result.returncode != 0:
                error_msg = result.stderr.strip()[-500:] if result.stderr else "未知错误"
                logger.error(f"裁剪失败 E{segment.episode_num}: {error_msg}")
                return CutResult(
                    episode_num=segment.episode_num,
                    output_path=output_path,
                    success=False,
                    error=error_msg
                )

            logger.info(f"裁剪完成 E{segment.episode_num}: {output_path}")
            return CutResult(
                episode_num=segment.episode_num,
                output_path=output_path,
                success=True
            )

        except subprocess.TimeoutExpired:
            return CutResult(
                episode_num=segment.episode_num,
                output_path=output_path,
                success=False,
                error="裁剪超时（超过 10 分钟）"
            )
        except Exception as e:
            return CutResult(
                episode_num=segment.episode_num,
                output_path=output_path,
                success=False,
                error=str(e)
            )

    def cut_batch(
        self,
        video_path: str,
        segments: list[EpisodeSegment],
        output_dir: str,
        drama_name: str,
        progress_callback: callable | None = None
    ) -> list[CutResult]:
        """顺序批量裁剪。

        Args:
            video_path: 输入视频路径
            segments: 分集列表
            output_dir: 输出目录
            drama_name: 剧名（用于文件命名）
            progress_callback: 进度回调 (current, total)

        Returns:
            CutResult 列表
        """
        os.makedirs(output_dir, exist_ok=True)
        total = len(segments)
        results: list[CutResult] = []

        for i, seg in enumerate(segments):
            output_path = os.path.join(
                output_dir,
                f"{drama_name}_S01E{seg.episode_num:02d}.mp4"
            )
            result = self.cut_single(video_path, seg, output_path)
            results.append(result)

            if progress_callback:
                progress_callback(i + 1, total)

        return results

    def cut_batch_concurrent(
        self,
        video_path: str,
        segments: list[EpisodeSegment],
        output_dir: str,
        drama_name: str,
        progress_callback: callable | None = None
    ) -> list[CutResult]:
        """并发批量裁剪。

        使用 ThreadPoolExecutor 并发执行。结果按 episode_num 排序。

        Args:
            video_path: 输入视频路径
            segments: 分集列表
            output_dir: 输出目录
            drama_name: 剧名（用于文件命名）
            progress_callback: 进度回调 (current, total)

        Returns:
            CutResult 列表（按 episode_num 排序）
        """
        os.makedirs(output_dir, exist_ok=True)

        results: list[CutResult] = []

        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            future_to_seg = {}
            for seg in segments:
                output_path = os.path.join(
                    output_dir,
                    f"{drama_name}_S01E{seg.episode_num:02d}.mp4"
                )
                future = executor.submit(
                    self.cut_single, video_path, seg, output_path
                )
                future_to_seg[future] = seg

            completed = 0
            total = len(segments)
            for future in as_completed(future_to_seg):
                result = future.result()
                results.append(result)
                completed += 1
                if progress_callback:
                    progress_callback(completed, total)

        # 按 episode_num 排序
        results.sort(key=lambda r: r.episode_num)
        return results
