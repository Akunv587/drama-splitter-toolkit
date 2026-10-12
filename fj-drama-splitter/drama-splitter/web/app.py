"""Gradio 前端界面

提供三标签页界面：上传分析 / 确认编辑 / 裁剪输出。
"""

import os
import re
import asyncio
import logging
import threading
import time
import gradio as gr
import pandas as pd

from modules.task_queue import TaskQueue
from modules.file_manager import FileManager
from modules.models import EpisodeSegment, ConfidenceLevel, TaskStatusEnum
from modules.subtitle_parser import SubtitleParser
from utils.time_utils import (
    seconds_to_timestamp,
    parse_manual_timestamps,
    parse_episode_end_format,
    format_duration,
    check_subtitle_conflict,
)
from config import AppConfig

logger = logging.getLogger(__name__)


class DramaSplitterApp:
    """Gradio 应用主类"""

    def __init__(self, config: AppConfig | None = None):
        self.config = config or AppConfig()
        self.task_queue = TaskQueue(max_workers=self.config.cut_max_workers)
        self.file_manager = FileManager(
            upload_dir=self.config.upload_dir,
            output_dir=self.config.output_dir
        )
        self._current_task_id: str | None = None
        self._current_subs: list | None = None

    def create_ui(self) -> gr.Blocks:
        with gr.Blocks(
            title="短剧总集篇自动分集工具",
            theme=gr.themes.Soft()
        ) as app:
            gr.Markdown("# 🎬 短剧总集篇自动分集工具")

            # ── 跨标签页共享状态 ──
            shared_episodes = gr.State(pd.DataFrame())

            with gr.Tabs():
                with gr.TabItem("📤 上传与分析"):
                    self._create_upload_tab(shared_episodes)
                with gr.TabItem("✏️ 确认与编辑"):
                    self._create_confirm_tab(shared_episodes)
                with gr.TabItem("📦 裁剪与输出"):
                    self._create_output_tab()

        return app

    def _create_upload_tab(self, shared_episodes: gr.State):
        gr.Markdown("## 上传文件并自动分析分集")

        with gr.Row():
            with gr.Column(scale=2):
                video_input = gr.File(
                    label="📹 视频文件 (mp4, 必填)",
                    file_types=[".mp4"],
                    type="filepath"
                )
                audio_input = gr.File(
                    label="🔊 音频文件 (mp3/m4a/aac/wav, 可选，视频自带音频也支持)",
                    file_types=[".mp3", ".m4a", ".aac", ".wav", ".flac", ".ogg"],
                    type="filepath"
                )
                subtitle_input = gr.File(
                    label="📝 字幕文件 (txt/vtt, 可选)",
                    file_types=[".txt", ".vtt"],
                    type="filepath"
                )
                drama_name_input = gr.Textbox(
                    label="🎭 剧名",
                    placeholder="请输入剧名（用于输出文件命名）"
                )

                analyze_btn = gr.Button("🚀 开始自动分析", variant="primary", size="lg")

            with gr.Column(scale=1):
                progress_bar = gr.Progress()
                status_output = gr.Textbox(
                    label="分析状态",
                    lines=10,
                    interactive=False,
                    placeholder="等待分析..."
                )
                flash_bar = gr.Slider(
                    label="🎬 场景检测", minimum=0, maximum=100, value=0,
                    interactive=False, visible=True
                )
                silence_bar = gr.Slider(
                    label="🔇 静音检测", minimum=0, maximum=100, value=0,
                    interactive=False, visible=True
                )

        gr.Markdown("---")
        gr.Markdown("### 人工输入模式（可选）")

        with gr.Row():
            manual_timestamps = gr.Textbox(
                label="📋 粘贴分集时间（支持两种格式）",
                placeholder=(
                    "格式1 - 分集结束时间:\n"
                    "第1集结束：00:02:03.270\n"
                    "第2集结束：00:04:01.589\n\n"
                    "格式2 - 逗号分隔起始时间:\n"
                    "00:00:00, 00:02:30, 00:05:00"
                ),
                lines=6
            )
            manual_drama_name = gr.Textbox(
                label="🎭 剧名（人工模式）",
                placeholder="请输入剧名"
            )
            manual_submit_btn = gr.Button("📥 提交人工时间", variant="secondary")

        preview_table = gr.Dataframe(
            label="分析结果预览",
            headers=["集数", "开始时间", "结束时间", "时长", "置信度", "台词冲突"],
            interactive=False
        )

        # 事件绑定 - 分析按钮使用生成器实现实时进度
        analyze_btn.click(
            fn=self.handle_upload_and_analyze,
            inputs=[video_input, audio_input, subtitle_input, drama_name_input],
            outputs=[status_output, preview_table, flash_bar, silence_bar, shared_episodes]
        )

        manual_submit_btn.click(
            fn=self.handle_manual_input,
            inputs=[video_input, manual_timestamps, manual_drama_name],
            outputs=[preview_table, status_output, shared_episodes]
        )

    def _create_confirm_tab(self, shared_episodes: gr.State):
        gr.Markdown("## 确认与编辑分集清单")
        gr.Markdown("可直接修改表格中的时间值，修改后自动重新检测台词冲突。")

        episodes_table = gr.Dataframe(
            label="分集清单（可编辑）",
            headers=["集数", "开始时间", "结束时间", "时长", "置信度", "台词冲突"],
            interactive=True,
            type="pandas"
        )

        with gr.Row():
            confirm_btn = gr.Button("✅ 确认并开始裁剪", variant="primary", size="lg")
            refresh_btn = gr.Button("🔄 重新检测冲突", variant="secondary")

        confirm_status = gr.Textbox(label="裁剪状态", lines=3, interactive=False)

        # shared_episodes 变化 → 同步到确认编辑表格
        shared_episodes.change(
            fn=self._sync_to_confirm_table,
            inputs=[shared_episodes],
            outputs=[episodes_table]
        )

        # 刷新冲突检测 → 同时更新表格和共享状态
        refresh_btn.click(
            fn=self.handle_edit_episode,
            inputs=[episodes_table],
            outputs=[episodes_table, shared_episodes]
        )

        # 确认裁剪 → 同时回写共享状态
        confirm_btn.click(
            fn=self.handle_confirm_and_cut,
            inputs=[episodes_table],
            outputs=[confirm_status, shared_episodes]
        )

    def _create_output_tab(self):
        gr.Markdown("## 裁剪进度与输出")

        cut_status = gr.Textbox(label="裁剪进度", lines=5, interactive=False)

        output_files = gr.Dataframe(
            label="输出文件列表",
            headers=["集数", "文件路径", "状态"],
            interactive=False
        )

        with gr.Row():
            open_dir_btn = gr.Button("📂 打开输出目录", variant="secondary")

        open_dir_btn.click(
            fn=lambda: self._open_output_dir(),
            outputs=[]
        )

    # ── 上传分析（带进度） ────────────────────────────────────────

    def handle_upload_and_analyze(
        self, video, audio, subtitle, drama_name
    ):
        """处理上传并触发分析，流式返回进度。

        使用生成器方式让 Gradio 实时更新进度条。
        返回顺序: status, preview_table, flash_bar, silence_bar, shared_episodes
        """
        _empty = pd.DataFrame()
        if not video:
            yield "错误: 请上传视频文件", _empty, 0, 0, _empty
            return

        if not drama_name:
            drama_name = "未命名"

        try:
            # 创建任务 & 保存文件
            yield "📁 保存上传文件...", _empty, 5, 0, _empty

            task_id = self.task_queue.create_task(drama_name=drama_name)
            video_path = self.file_manager.save_upload(video, task_id)
            self.task_queue.tasks[task_id].video_path = video_path

            if audio:
                audio_path = self.file_manager.save_upload(audio, task_id)
                self.task_queue.tasks[task_id].audio_path = audio_path

            if subtitle:
                subtitle_path = self.file_manager.save_upload(subtitle, task_id)
                self.task_queue.tasks[task_id].subtitle_path = subtitle_path

            self._current_task_id = task_id

            yield "🔍 启动分析引擎...", _empty, 10, 5, _empty

            # 在后台线程运行分析，主线程轮询进度
            result_container = {"task": None, "error": None}

            def run_in_thread():
                try:
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    result_container["task"] = loop.run_until_complete(
                        self.task_queue.run_analysis(task_id)
                    )
                    loop.close()
                except Exception as e:
                    result_container["error"] = str(e)

            thread = threading.Thread(target=run_in_thread, daemon=True)
            thread.start()

            # 轮询进度
            last_msg = ""
            while thread.is_alive():
                task = self.task_queue.tasks.get(task_id)
                if task:
                    flash_pct = task.progress_flash
                    silence_pct = task.progress_silence
                    msg = task.progress_message or "分析中..."

                    if msg != last_msg:
                        last_msg = msg
                        yield msg, _empty, flash_pct, silence_pct, _empty
                time.sleep(0.3)

            thread.join()

            if result_container["error"]:
                yield f"分析失败: {result_container['error']}", _empty, 0, 0, _empty
                return

            task = result_container["task"]
            if not task or task.status == TaskStatusEnum.FAILED:
                yield f"分析失败: {task.error_message if task else '未知错误'}", _empty, 100, 100, _empty
                return

            # 加载字幕用于后续冲突检测
            if task.subtitle_path:
                try:
                    self._current_subs = SubtitleParser.parse(task.subtitle_path)
                except Exception:
                    self._current_subs = None
            else:
                self._current_subs = None

            df = self._episodes_to_dataframe(task.episodes or [])
            yield (
                f"✅ 分析完成! 共 {len(task.episodes or [])} 集",
                df, 100, 100, df
            )

        except Exception as e:
            logger.error(f"上传分析失败: {e}", exc_info=True)
            yield f"错误: {str(e)}", _empty, 0, 0, _empty

    # ── 人工输入 ──────────────────────────────────────────────────

    def _get_or_create_task(self, drama_name: str, video_file=None) -> str:
        """获取或创建用于分集操作的任务。

        优先复用已有关联视频的任务，避免手动输入覆盖掉上传的视频引用。
        如果传入了 video_file 且任务尚无视频，自动保存视频到任务。

        Args:
            drama_name: 剧名
            video_file: Gradio File 组件上传的视频路径（可选）

        Returns:
            任务 ID
        """
        if self._current_task_id:
            existing = self.task_queue.tasks.get(self._current_task_id)
            if existing and existing.video_path:
                return self._current_task_id

        # 没有视频任务则创建新的
        task_id = self.task_queue.create_task(drama_name=drama_name)
        self._current_task_id = task_id

        # 如果提供了视频文件，直接保存到新任务
        if video_file:
            video_path = self.file_manager.save_upload(video_file, task_id)
            self.task_queue.tasks[task_id].video_path = video_path
            logger.info(f"手动模式保存视频: {video_path}")

        return task_id

    def handle_manual_input(
        self, video, timestamps_text: str, drama_name: str
    ) -> tuple[pd.DataFrame, str, pd.DataFrame]:
        if not timestamps_text.strip():
            return pd.DataFrame(), "错误: 请输入分集时间", pd.DataFrame()

        if not drama_name:
            drama_name = "未命名"

        try:
            # 自动检测格式：第N集结束 还是逗号分隔
            if re.search(r'第\s*\d+\s*集\s*结\s*束', timestamps_text):
                return self._handle_episode_end_format(video, timestamps_text, drama_name)

            timestamps = parse_manual_timestamps(timestamps_text)
            subs = self._current_subs

            task_id = self._get_or_create_task(drama_name, video)

            episodes = self._build_manual_episodes(timestamps, subs)
            self.task_queue.update_task_episodes(task_id, episodes)

            df = self._episodes_to_dataframe(episodes)
            status = f"人工分集完成! 共 {len(episodes)} 集"
            if any(e.has_subtitle_conflict for e in episodes):
                status += " ⚠️ 检测到台词冲突，请检查标红项"

            return df, status, df

        except Exception as e:
            return pd.DataFrame(), f"错误: {str(e)}", pd.DataFrame()

    def _handle_episode_end_format(
        self, video, text: str, drama_name: str
    ) -> tuple[pd.DataFrame, str, pd.DataFrame]:
        """处理 "第N集结束：HH:MM:SS" 格式的粘贴文本。"""
        end_times = parse_episode_end_format(text)

        task_id = self._get_or_create_task(drama_name, video)

        subs = self._current_subs
        episodes = self._build_episodes_from_end_times(end_times, subs)
        self.task_queue.update_task_episodes(task_id, episodes)

        df = self._episodes_to_dataframe(episodes)
        status = f"分集解析完成! 共 {len(episodes)} 集"
        if any(e.has_subtitle_conflict for e in episodes):
            status += " ⚠️ 检测到台词冲突，请检查标红项"

        return df, status, df

    # ── 编辑 & 裁剪 ───────────────────────────────────────────────

    def _sync_to_confirm_table(self, shared_df: pd.DataFrame) -> pd.DataFrame:
        """将共享状态同步到确认编辑表格（passthrough）。"""
        return shared_df if shared_df is not None and not shared_df.empty else pd.DataFrame()

    def handle_edit_episode(self, episodes_df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
        if episodes_df is None or episodes_df.empty:
            return episodes_df, pd.DataFrame()

        try:
            subs = self._current_subs
            episodes = self._dataframe_to_episodes(episodes_df, subs)
            df = self._episodes_to_dataframe(episodes)
            return df, df
        except Exception as e:
            logger.error(f"编辑检测失败: {e}")
            return episodes_df, pd.DataFrame()

    def handle_confirm_and_cut(self, episodes_df: pd.DataFrame) -> tuple[str, pd.DataFrame]:
        if not self._current_task_id:
            return "错误: 请先进行分析或人工输入", pd.DataFrame()

        if episodes_df is None or episodes_df.empty:
            return "错误: 分集清单为空", pd.DataFrame()

        try:
            subs = self._current_subs
            episodes = self._dataframe_to_episodes(episodes_df, subs)
            self.task_queue.update_task_episodes(self._current_task_id, episodes)

            loop = asyncio.new_event_loop()
            results = loop.run_until_complete(
                self.task_queue.run_cut(self._current_task_id, episodes)
            )
            loop.close()

            success_count = sum(1 for r in results if r.success)
            fail_count = len(results) - success_count

            msg_parts = [f"裁剪完成! 成功: {success_count}/{len(results)}"]
            if fail_count > 0:
                msg_parts.append(f"失败: {fail_count} 集")
                for r in results:
                    if not r.success:
                        msg_parts.append(f"  E{r.episode_num}: {r.error}")

            # 回写编辑后的数据到共享状态
            df = self._episodes_to_dataframe(episodes)
            return "\n".join(msg_parts), df

        except Exception as e:
            logger.error(f"裁剪失败: {e}", exc_info=True)
            return f"裁剪失败: {str(e)}", pd.DataFrame()

    # ── 辅助方法 ──────────────────────────────────────────────────

    def _build_manual_episodes(
        self, timestamps: list[float], subs: list | None
    ) -> list[EpisodeSegment]:
        if not timestamps:
            return []

        video_duration = timestamps[-1] + 180.0
        episodes: list[EpisodeSegment] = []
        for i, start in enumerate(timestamps):
            end = timestamps[i + 1] if i + 1 < len(timestamps) else video_duration
            conflict = self._check_endpoint_conflict(start, end, subs)
            detail = self._get_conflict_detail(end, subs) if conflict else None
            episodes.append(EpisodeSegment(
                episode_num=i + 1,
                start_time=start,
                end_time=end,
                confidence=ConfidenceLevel.LOW,
                has_subtitle_conflict=conflict,
                conflict_detail=detail
            ))
        return episodes

    def _build_episodes_from_end_times(
        self, end_times: list[tuple[int, float]], subs: list | None
    ) -> list[EpisodeSegment]:
        """从 "第N集结束" 格式构建 EpisodeSegment 列表。

        每集的开始时间 = 上一集的结束时间（第一集从 0 开始）。
        """
        if not end_times:
            return []

        episodes: list[EpisodeSegment] = []
        prev_end = 0.0  # 第一集从 00:00:00 开始

        for ep_num, end_time in end_times:
            start = prev_end
            end = end_time
            conflict = self._check_endpoint_conflict(start, end, subs)
            detail = self._get_conflict_detail(end, subs) if conflict else None
            episodes.append(EpisodeSegment(
                episode_num=ep_num,
                start_time=start,
                end_time=end,
                confidence=ConfidenceLevel.LOW,
                has_subtitle_conflict=conflict,
                conflict_detail=detail
            ))
            prev_end = end_time

        return episodes

    def _check_endpoint_conflict(
        self, start: float, end: float, subs: list | None
    ) -> bool:
        """检查起始点是否与字幕冲突。"""
        if not subs:
            return False
        conflict = check_subtitle_conflict(start, subs)
        conflict = conflict or check_subtitle_conflict(end, subs)
        return conflict

    def _get_conflict_detail(self, ts: float, subs: list | None) -> str | None:
        """获取冲突详情。"""
        if not subs:
            return None
        for sub in subs:
            if sub.start < ts < sub.end:
                return (
                    f"切割点 {seconds_to_timestamp(ts)} 落在台词中间: "
                    f"\"{sub.text}\""
                )
        return None

    def _episodes_to_dataframe(
        self, episodes: list[EpisodeSegment]
    ) -> pd.DataFrame:
        rows = []
        for ep in episodes:
            conf_icon = "🟢 HIGH" if ep.confidence == ConfidenceLevel.HIGH else "🔴 LOW"
            conflict_icon = "❌" if ep.has_subtitle_conflict else "✓"

            rows.append({
                "集数": ep.episode_num,
                "开始时间": seconds_to_timestamp(ep.start_time),
                "结束时间": seconds_to_timestamp(ep.end_time),
                "时长": format_duration(ep.end_time - ep.start_time),
                "置信度": conf_icon,
                "台词冲突": conflict_icon,
            })

        return pd.DataFrame(rows)

    def _dataframe_to_episodes(
        self, df: pd.DataFrame, subs: list | None
    ) -> list[EpisodeSegment]:
        from utils.time_utils import timestamp_to_seconds

        episodes: list[EpisodeSegment] = []
        for _, row in df.iterrows():
            try:
                start = timestamp_to_seconds(str(row["开始时间"]))
                end = timestamp_to_seconds(str(row["结束时间"]))
                conf_str = str(row["置信度"])
                confidence = ConfidenceLevel.HIGH if "HIGH" in conf_str else ConfidenceLevel.LOW

                conflict = False
                detail = None
                if subs:
                    conflict = check_subtitle_conflict(start, subs)
                    conflict = conflict or check_subtitle_conflict(end, subs)
                    if conflict:
                        for sub in subs:
                            if sub.start < end < sub.end:
                                detail = (
                                    f"切割点 {seconds_to_timestamp(end)} 落在台词中间: "
                                    f"\"{sub.text}\""
                                )
                                break

                episodes.append(EpisodeSegment(
                    episode_num=int(row["集数"]),
                    start_time=start,
                    end_time=end,
                    confidence=confidence,
                    has_subtitle_conflict=conflict,
                    conflict_detail=detail
                ))
            except Exception as e:
                logger.warning(f"解析行数据失败: {e}")
                continue

        return episodes

    def _open_output_dir(self) -> None:
        output_dir = os.path.abspath(self.config.output_dir)
        if os.path.exists(output_dir):
            os.startfile(output_dir)

    def launch(self, **kwargs):
        app = self.create_ui()
        app.launch(**kwargs)
