"""任务队列与并发调度

管理任务状态，协调分析任务和裁剪任务的执行。
分析任务串行执行，但闪白+静音检测可并行。
裁剪任务并发执行。
"""

import logging
import asyncio
import uuid
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor

from modules.models import TaskStatus, EpisodeSegment, CutResult, TaskStatusEnum, ConfidenceLevel
from modules.flash_detector import FlashDetector
from modules.silence_detector import SilenceDetector
from modules.subtitle_parser import SubtitleParser
from modules.cross_validator import CrossValidator
from modules.cutter import VideoCutter
from modules.file_manager import FileManager

logger = logging.getLogger(__name__)


class TaskQueue:
    """任务队列与并发调度"""

    def __init__(self, max_workers: int | None = None):
        """初始化。

        Args:
            max_workers: 并发裁剪数，默认为 CPU 核心数 - 1
        """
        if max_workers is None:
            import os
            max_workers = max(1, (os.cpu_count() or 2) - 1)

        self.max_workers = max_workers
        self.tasks: dict[str, TaskStatus] = {}
        self._executor = ThreadPoolExecutor(max_workers=max_workers)
        self.file_manager = FileManager()
        self.flash_detector = FlashDetector()
        self.silence_detector = SilenceDetector()
        self.cross_validator = CrossValidator()
        self.video_cutter = VideoCutter(max_workers=max_workers)

        logger.info(f"任务队列初始化完成, max_workers={max_workers}")

    def create_task(
        self,
        video_path: str | None = None,
        audio_path: str | None = None,
        subtitle_path: str | None = None,
        drama_name: str | None = None
    ) -> str:
        """创建新任务，返回 task_id (UUID)。

        Args:
            video_path: 视频文件路径
            audio_path: 音频文件路径
            subtitle_path: 字幕文件路径
            drama_name: 剧名

        Returns:
            任务 ID (UUID 字符串)
        """
        task_id = str(uuid.uuid4())
        task = TaskStatus(
            task_id=task_id,
            status=TaskStatusEnum.PENDING,
            video_path=video_path,
            audio_path=audio_path,
            subtitle_path=subtitle_path,
            drama_name=drama_name
        )
        self.tasks[task_id] = task
        logger.info(f"任务创建: {task_id}, drama={drama_name}")
        return task_id

    def get_task(self, task_id: str) -> TaskStatus | None:
        """获取任务状态。

        Args:
            task_id: 任务 ID

        Returns:
            TaskStatus 或 None（不存在时）
        """
        return self.tasks.get(task_id)

    def list_tasks(self) -> list[TaskStatus]:
        """列出所有任务。

        Returns:
            所有任务的 TaskStatus 列表
        """
        return list(self.tasks.values())

    async def run_analysis(self, task_id: str) -> TaskStatus:
        """执行分析任务。

        流程：
        1. 更新状态为 'analyzing'
        2. 并行执行闪白检测和静音检测
        3. 解析字幕（如果有）
        4. 交叉验证
        5. 构建分集清单
        6. 更新状态为 'awaiting_confirm'

        Args:
            task_id: 任务 ID

        Returns:
            更新后的 TaskStatus
        """
        task = self.tasks.get(task_id)
        if not task:
            raise ValueError(f"任务不存在: {task_id}")

        logger.info(f"开始分析: {task_id}")

        try:
            task.status = TaskStatusEnum.ANALYZING
            task.updated_at = datetime.now()

            loop = asyncio.get_event_loop()

            # 进度回调
            def flash_progress(pct: int, msg: str):
                task.progress_flash = pct
                task.progress_message = msg

            def silence_progress(pct: int, msg: str):
                task.progress_silence = pct
                task.progress_message = msg

            # 并行执行闪白检测和静音检测
            flash_task = None
            silence_task = None

            if task.video_path:
                flash_task = loop.run_in_executor(
                    self._executor,
                    self.flash_detector.detect,
                    task.video_path,
                    flash_progress,
                )
                logger.info(f"闪白检测已启动: {task.video_path}")

            audio_source = task.audio_path or task.video_path
            if audio_source:
                silence_task = loop.run_in_executor(
                    self._executor,
                    self.silence_detector.detect,
                    audio_source,
                    silence_progress,
                )
                logger.info(f"静音检测已启动: {audio_source}")

            # 等待并行任务完成
            flash_points: list[float] = []
            silence_points: list[float] = []

            if flash_task:
                try:
                    flash_points = await flash_task
                except Exception as e:
                    logger.warning(f"闪白检测失败（降级处理）: {e}")
                    flash_points = []

            if silence_task:
                try:
                    silence_points = await silence_task
                except Exception as e:
                    logger.warning(f"静音检测失败（降级处理）: {e}")
                    silence_points = []

            # 解析字幕
            subtitle_gaps = None
            subs = None
            if task.subtitle_path:
                try:
                    subs = await loop.run_in_executor(
                        self._executor, SubtitleParser.parse, task.subtitle_path
                    )
                    subtitle_gaps = await loop.run_in_executor(
                        self._executor, SubtitleParser.extract_silence_gaps, subs
                    )
                    logger.info(f"字幕解析完成: {len(subs)} 条, {len(subtitle_gaps) if subtitle_gaps else 0} 个间隙")
                except Exception as e:
                    logger.warning(f"字幕解析失败（降级处理）: {e}")
                    subs = None
                    subtitle_gaps = None

            # 获取视频时长
            video_duration = 0.0
            if task.video_path:
                try:
                    from utils.ffmpeg_check import get_video_info
                    info = await loop.run_in_executor(
                        self._executor, get_video_info, task.video_path
                    )
                    video_duration = info.get("duration", 0.0)
                except Exception as e:
                    logger.warning(f"获取视频时长失败: {e}")

            # 交叉验证
            merged_points = self.cross_validator.validate(
                flash_points=flash_points,
                silence_points=silence_points,
                subtitle_gaps=subtitle_gaps,
                video_duration=video_duration
            )

            # 构建分集清单
            episodes = self.cross_validator.build_episode_list(
                merged_points=merged_points,
                video_duration=video_duration,
                subs=subs
            )

            task.episodes = episodes
            task.status = TaskStatusEnum.AWAITING_CONFIRM
            task.updated_at = datetime.now()

            logger.info(f"分析完成: {task_id}, {len(episodes)} 集")
            return task

        except Exception as e:
            logger.error(f"分析失败: {task_id}, error={e}", exc_info=True)
            task.status = TaskStatusEnum.FAILED
            task.error_message = str(e)
            task.updated_at = datetime.now()
            return task

    async def run_cut(
        self, task_id: str, episodes: list[EpisodeSegment]
    ) -> list[CutResult]:
        """执行裁剪任务。

        Args:
            task_id: 任务 ID
            episodes: 分集清单

        Returns:
            CutResult 列表
        """
        task = self.tasks.get(task_id)
        if not task:
            raise ValueError(f"任务不存在: {task_id}")

        if not task.video_path:
            raise ValueError("任务没有关联视频文件")

        if not task.drama_name:
            task.drama_name = "untitled"

        logger.info(f"开始裁剪: {task_id}, {len(episodes)} 集")

        try:
            task.status = TaskStatusEnum.CUTTING
            task.updated_at = datetime.now()

            # 创建输出目录
            output_dir = self.file_manager.create_output_dir(task.drama_name)

            # 并发裁剪
            loop = asyncio.get_event_loop()
            results = await loop.run_in_executor(
                self._executor,
                lambda: self.video_cutter.cut_batch_concurrent(
                    video_path=task.video_path,
                    segments=episodes,
                    output_dir=output_dir,
                    drama_name=task.drama_name
                )
            )

            task.cut_results = results

            # 判断整体状态
            all_success = all(r.success for r in results)
            task.status = TaskStatusEnum.COMPLETED if all_success else TaskStatusEnum.FAILED
            if not all_success:
                failed = [r for r in results if not r.success]
                task.error_message = f"{len(failed)} 集裁剪失败"

            task.updated_at = datetime.now()

            logger.info(
                f"裁剪完成: {task_id}, "
                f"success={sum(1 for r in results if r.success)}/{len(results)}"
            )
            return results

        except Exception as e:
            logger.error(f"裁剪失败: {task_id}, error={e}", exc_info=True)
            task.status = TaskStatusEnum.FAILED
            task.error_message = str(e)
            task.updated_at = datetime.now()
            return []

    def update_task_episodes(
        self, task_id: str, episodes: list[EpisodeSegment]
    ) -> None:
        """更新任务的分集清单（用户编辑后）。

        Args:
            task_id: 任务 ID
            episodes: 修改后的分集清单
        """
        task = self.tasks.get(task_id)
        if task:
            task.episodes = episodes
            task.updated_at = datetime.now()
            logger.info(f"分集清单已更新: {task_id}")

    def delete_task(self, task_id: str) -> None:
        """删除任务。

        Args:
            task_id: 任务 ID
        """
        if task_id in self.tasks:
            del self.tasks[task_id]
            self.file_manager.cleanup_task_files(task_id)
            logger.info(f"任务已删除: {task_id}")

