"""Streamlit Web UI – 短剧总集篇自动分集工具。

提供完整的交互式操作流程：
  输入 → 分析 → 复核 → 裁剪 → 完成
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import streamlit as st

from drama_splitter.ffmpeg_wrapper import extract_frames, probe_video
from drama_splitter.models import Episode, TimeList, VideoMeta
from drama_splitter.models.config import AppConfig
from drama_splitter.ui.utils import (
    build_config_from_ui,
    build_timeline_data,
    format_timestamp,
    merge_edits,
    validate_edited_episodes,
    validate_naming_template,
    validate_video_path,
)

# ── 页面设置 ────────────────────────────────────────────────────

st.set_page_config(
    page_title="短剧分集工具",
    page_icon="🎬",
    layout="wide",
)

# ── 会话状态初始化 ──────────────────────────────────────────────

_SESSION_DEFAULTS: Dict[str, Any] = {
    "step": "input",
    "video_path": "",
    "video_meta": None,
    "config_params": {
        "ocr_engine": "rapidocr",
        "frame_interval_sec": 0.5,
        "cut_mode": "lossless",
        "naming_template": "第{ep:02d}集.mp4",
    },
    "work_dir": "",
    "time_list": None,
    "time_list_path": "",
    "process": None,
    "process_status": "",
    "process_error": "",
    "process_logs": [],
    "process_start_time": 0.0,
    "cut_results": None,
    "edited_episodes": {},
    "output_dir": "",
    "preview_cache": {},
}

for _key, _val in _SESSION_DEFAULTS.items():
    if _key not in st.session_state:
        st.session_state[_key] = _val


# ── 工具函数 ────────────────────────────────────────────────────

def _reset_state(keep_video: bool = False) -> None:
    """重置会话状态（可选保留视频信息）。"""
    video_path = st.session_state.video_path if keep_video else ""
    video_meta = st.session_state.video_meta if keep_video else None
    for key, val in _SESSION_DEFAULTS.items():
        st.session_state[key] = val
    if keep_video:
        st.session_state.video_path = video_path
        st.session_state.video_meta = video_meta


def _strip_ansi(text: str) -> str:
    """去除 ANSI 转义序列和 Rich 标记，保留可读文本。"""
    # 去除 ANSI CSI 序列: \x1b[...m
    text = re.sub(r'\x1b\[[0-9;]*[a-zA-Z]', '', text)
    # 去除 Rich 标记标签: [/...], [red], [green] 等
    text = re.sub(r'\[/?[a-z_]+(?:\s[^\]]*)?\]', '', text)
    # 去除 Bell 字符
    text = text.replace('\x07', '')
    return text.strip()


def _run_subprocess(cmd: List[str], work_dir: str) -> None:
    """启动后台子进程执行 CLI 命令，启动线程实时读取输出。"""
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"
    env["FORCE_COLOR"] = "1"
    env["TERM"] = "xterm-256color"
    env["COLUMNS"] = "120"
    env["DRAMA_SPLITTER_FORCE_TERMINAL"] = "1"
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        env=env,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,  # type: ignore[attr-defined]
    )
    st.session_state.process = proc
    st.session_state.process_status = "running"
    st.session_state.process_error = ""
    st.session_state.process_logs = []
    st.session_state.process_start_time = time.time()

    # 后台线程读取 stdout（按块读取，兼容 Rich 的 \r 刷新）
    def _reader() -> None:
        if proc.stdout is None:
            return
        logs: List[str] = st.session_state.process_logs
        buf = ""
        for chunk in iter(lambda: proc.stdout.read(4096) if proc.stdout else "", ""):
            if not chunk:
                break
            buf += chunk
            # 按 \n 或 \r 分割输出行
            while True:
                idx = -1
                for sep in ("\n", "\r"):
                    pos = buf.find(sep)
                    if pos != -1 and (idx == -1 or pos < idx):
                        idx = pos
                if idx == -1:
                    break
                line = buf[:idx]
                buf = buf[idx + 1:]
                cleaned = _strip_ansi(line)
                if cleaned:
                    # 避免重复的进度行（相同内容只保留一次）
                    if logs and cleaned == logs[-1]:
                        continue
                    logs.append(cleaned)
        # 残留内容
        if buf:
            cleaned = _strip_ansi(buf)
            if cleaned:
                logs.append(cleaned)

    t = threading.Thread(target=_reader, daemon=True)
    t.start()


def _check_process() -> Optional[int]:
    """检查后台进程状态，返回 exit code 或 None（仍在运行）。"""
    proc = st.session_state.process
    if proc is None:
        return None
    ret = proc.poll()
    if ret is not None:
        st.session_state.process_status = "done" if ret == 0 else "error"
        if ret != 0:
            logs = st.session_state.process_logs
            st.session_state.process_error = "\n".join(logs[-20:]) if logs else f"exit code {ret}"
        st.session_state.process = None
    return ret


# ── 步骤导航条 ──────────────────────────────────────────────────

def _render_step_nav() -> None:
    """渲染顶部步骤导航条。"""
    steps = ["📁 输入", "🔍 分析", "🔎 复核", "✂️ 裁剪", "✅ 完成"]
    step_map = {"input": 0, "analyzing": 1, "review": 2, "cutting": 3, "done": 4}
    current = step_map.get(st.session_state.step, 0)

    cols = st.columns(len(steps))
    for i, (col, label) in enumerate(zip(cols, steps)):
        with col:
            if i < current:
                st.markdown(f"<span style='color:green'>{label}</span>", unsafe_allow_html=True)
            elif i == current:
                st.markdown(f"<b><span style='color:#1f77b4'>{label}</span></b>", unsafe_allow_html=True)
            else:
                st.markdown(f"<span style='color:gray'>{label}</span>", unsafe_allow_html=True)


# ── 模块一：文件输入与任务配置 ──────────────────────────────────

def _render_input_module() -> None:
    """渲染输入与配置页面。"""
    st.title("短剧总集篇自动分集工具")

    # ── 文件选择区 ──
    st.subheader("选择视频文件")
    col1, col2 = st.columns([4, 1])
    with col1:
        video_path = st.text_input(
            "视频文件路径",
            value=st.session_state.video_path,
            placeholder="请输入本地视频文件的完整路径，如 C:\\Videos\\drama.mp4",
            label_visibility="collapsed",
        )
    with col2:
        load_clicked = st.button("加载视频", type="secondary", use_container_width=True)

    if load_clicked and video_path:
        ok, msg, clean_path = validate_video_path(video_path)
        if not ok:
            st.error(msg)
        else:
            video_path = clean_path
            with st.spinner("正在读取视频信息..."):
                try:
                    meta = probe_video(video_path)
                    st.session_state.video_path = video_path
                    st.session_state.video_meta = meta
                    # 自动生成工作目录
                    stem = Path(video_path).stem
                    st.session_state.work_dir = os.path.join(
                        tempfile.gettempdir(), "drama_splitter", stem
                    )
                    st.session_state.output_dir = os.path.join(
                        os.path.dirname(video_path) or ".", f"{stem}_split"
                    )
                    st.success("视频加载成功")
                except Exception as e:
                    st.error(f"无法读取视频信息: {e}")

    # ── 视频信息展示 ──
    meta = st.session_state.video_meta
    if meta is not None:
        st.markdown("---")
        cols = st.columns([2, 1, 1, 1, 1, 1])
        with cols[0]:
            st.metric("文件名", meta.file_name[:30] + ("..." if len(meta.file_name) > 30 else ""))
        with cols[1]:
            st.metric("格式", meta.format.split(",")[0] if "," in meta.format else meta.format)
        with cols[2]:
            st.metric("分辨率", f"{meta.width}x{meta.height}")
        with cols[3]:
            st.metric("时长", format_timestamp(meta.duration_sec))
        with cols[4]:
            st.metric("大小", f"{meta.file_size_mb:.0f}MB")
        with cols[5]:
            if st.button("移除", type="secondary"):
                _reset_state(keep_video=False)
                st.rerun()

    # ── 配置面板 ──
    if meta is not None:
        st.markdown("---")
        st.subheader("分析配置")

        col_a, col_b = st.columns(2)
        with col_a:
            ocr_engine = st.selectbox(
                "OCR 引擎",
                options=["rapidocr", "paddleocr"],
                index=0 if st.session_state.config_params.get("ocr_engine") == "rapidocr" else 1,
                help="RapidOCR 更快（推荐），PaddleOCR 更准",
            )
            frame_interval = st.select_slider(
                "抽帧间隔（秒）",
                options=[0.25, 0.5, 1.0, 2.0],
                value=st.session_state.config_params.get("frame_interval_sec", 0.5),
                help="间隔越小越精确但越慢",
            )
        with col_b:
            cut_mode = st.selectbox(
                "裁剪模式",
                options=["lossless", "reencode"],
                index=0 if st.session_state.config_params.get("cut_mode") == "lossless" else 1,
                help="lossless 无损快速（推荐），reencode 重编码",
            )
            naming_template = st.text_input(
                "文件命名模板",
                value=st.session_state.config_params.get("naming_template", "第{ep:02d}集.mp4"),
                help="使用 {ep} 或 {ep:02d} 占位符",
            )

        # 命名预览
        if naming_template:
            ok_tmpl, msg_tmpl = validate_naming_template(naming_template)
            if ok_tmpl:
                preview = naming_template
                if "{ep:02d}" in preview:
                    preview = preview.replace("{ep:02d}", "01").replace("{ep}", "01")
                elif "{ep}" in preview:
                    preview = preview.replace("{ep}", "01")
                st.caption(f"命名预览：**{preview}**")
            else:
                st.caption(f"⚠️ {msg_tmpl}")

        # 工作目录
        work_dir = st.text_input(
            "工作目录（缓存中间结果）",
            value=st.session_state.work_dir,
            help="存放候选点、时间清单等中间文件，可断点续传",
        )
        st.session_state.work_dir = work_dir

        # 高级配置
        with st.expander("高级配置"):
            fingerprint_threshold = st.slider(
                "音频指纹相似度阈值",
                min_value=0.0,
                max_value=1.0,
                value=st.session_state.config_params.get("fingerprint_threshold", 0.7),
                step=0.05,
            )
            min_ep = st.number_input(
                "最小单集时长（秒）",
                min_value=10,
                max_value=300,
                value=st.session_state.config_params.get("min_episode_sec", 60),
            )
            max_ep = st.number_input(
                "最大单集时长（秒）",
                min_value=60,
                max_value=1800,
                value=st.session_state.config_params.get("max_episode_sec", 300),
            )
            st.session_state.config_params.update({
                "fingerprint_threshold": fingerprint_threshold,
                "min_episode_sec": min_ep,
                "max_episode_sec": max_ep,
            })

        # 更新配置参数
        st.session_state.config_params.update({
            "ocr_engine": ocr_engine,
            "frame_interval_sec": frame_interval,
            "cut_mode": cut_mode,
            "naming_template": naming_template,
        })

        # ── 启动按钮 ──
        st.markdown("---")
        can_start = True
        if not video_path:
            st.warning("请输入视频文件路径")
            can_start = False
        if naming_template:
            ok_tmpl, _ = validate_naming_template(naming_template)
            if not ok_tmpl:
                can_start = False

        if st.button("开始分析", type="primary", disabled=not can_start, use_container_width=True):
            os.makedirs(st.session_state.work_dir, exist_ok=True)

            # 写入配置文件
            config_path = os.path.join(st.session_state.work_dir, "config.json")
            try:
                config = build_config_from_ui(st.session_state.config_params)
                with open(config_path, "w", encoding="utf-8") as f:
                    json.dump(config.model_dump(), f, ensure_ascii=False, indent=2)
            except Exception as e:
                st.error(f"配置校验失败: {e}")
                return

            # 启动后台分析进程
            cmd = [
                sys.executable, "-u", "-m", "drama_splitter", "analyze",
                st.session_state.video_path,
                "--config", config_path,
                "-o", st.session_state.work_dir,
                "--fresh",
            ]
            _run_subprocess(cmd, st.session_state.work_dir)
            st.session_state.step = "analyzing"
            st.rerun()


# ── 模块二：分析进度 ────────────────────────────────────────────

def _render_analyzing_module() -> None:
    """渲染分析进度页面（实时日志 + 时间计数 + 进度条）。"""
    st.title("正在分析视频...")

    ret = _check_process()

    # 计算已用时间
    elapsed = time.time() - st.session_state.process_start_time
    elapsed_str = f"{int(elapsed // 60)}分{int(elapsed % 60)}秒" if elapsed >= 60 else f"{int(elapsed)}秒"

    logs = st.session_state.process_logs
    log_count = len(logs)

    if ret is None:
        # ── 仍在运行 ──
        # 进度条（不确定模式）
        st.progress(0.0, text=f"⏳ 分析进行中 · 已用 {elapsed_str}")

        # 日志区域
        col1, col2 = st.columns([3, 1])
        with col1:
            st.caption(f"工作目录：`{st.session_state.work_dir}`")
        with col2:
            st.metric("日志行数", log_count)

        if logs:
            # 显示最近 15 行日志
            latest = "\n".join(logs[-15:])
            st.code(latest, language=None)
        else:
            st.info("等待 CLI 输出...")

        # 自动刷新
        time.sleep(1.5)
        st.rerun()

    elif ret == 0:
        # ── 成功完成 ──
        st.progress(1.0, text=f"✅ 分析完成 · 总用时 {elapsed_str}")

        # 显示最后日志
        if logs:
            with st.expander("查看完整日志", expanded=False):
                st.code("\n".join(logs), language=None)

        time_list_path = os.path.join(st.session_state.work_dir, "time_list.json")
        if os.path.exists(time_list_path):
            try:
                tl = TimeList.from_json(time_list_path)
                st.session_state.time_list = tl
                st.session_state.time_list_path = time_list_path
                st.session_state.step = "review"
                st.success(f"分析完成！检测到 {len(tl.episodes)} 集")
                time.sleep(1)
                st.rerun()
            except Exception as e:
                st.error(f"加载时间清单失败: {e}")
                st.session_state.step = "input"
        else:
            st.error("未找到时间清单文件，分析可能未成功")
            st.session_state.step = "input"
    else:
        # ── 失败 ──
        st.progress(1.0, text=f"❌ 分析失败 · exit code {ret}")

        if logs:
            with st.expander("查看错误日志", expanded=True):
                st.code("\n".join(logs[-30:]), language=None)

        st.error(f"分析失败: {st.session_state.process_error[-300:]}")
        col1, col2 = st.columns(2)
        with col1:
            if st.button("返回重试", type="primary", use_container_width=True):
                _reset_state(keep_video=True)
                st.rerun()
        with col2:
            if st.button("重新配置", use_container_width=True):
                _reset_state()
                st.rerun()


# ── 模块三：时间清单查看与人工复核 ──────────────────────────────

def _render_review_module() -> None:
    """渲染复核页面。"""
    st.title("时间清单复核")

    tl = st.session_state.time_list
    if tl is None:
        st.warning("未找到时间清单，请重新分析")
        if st.button("返回输入页"):
            st.session_state.step = "input"
            st.rerun()
        return

    # 从 edited_episodes 重建当前清单
    if st.session_state.edited_episodes:
        tl = merge_edits(tl, st.session_state.edited_episodes)
        st.session_state.time_list = tl

    episodes = tl.episodes

    # ── 时间轴可视化 ──
    st.subheader("时间轴总览")
    _render_timeline(tl)

    # ── 统计摘要 ──
    high_count = sum(1 for e in episodes if e.confidence == "high")
    med_count = sum(1 for e in episodes if e.confidence == "medium")
    low_count = sum(1 for e in episodes if e.confidence == "low")
    edited_count = len(st.session_state.edited_episodes)

    cols = st.columns(4)
    cols[0].metric("总集数", len(episodes))
    cols[1].metric("高置信", high_count, delta=None)
    cols[2].metric("中置信", med_count, delta=None)
    cols[3].metric("低置信", low_count, delta=f"{'⚠️' if low_count > 0 else ''}")

    if edited_count > 0:
        st.caption(f"已手动调整 {edited_count} 集的时间")

    # ── 集数清单表格 ──
    st.markdown("---")
    st.subheader("集数清单")

    # 表格数据
    rows = []
    for ep in episodes:
        status = "✅" if ep.episode in st.session_state.edited_episodes else ""
        rows.append({
            "集数": ep.episode,
            "开始": format_timestamp(ep.start_sec),
            "结束": format_timestamp(ep.end_sec),
            "时长": format_timestamp(ep.duration_sec),
            "置信度": ep.confidence,
            "信号": ", ".join(ep.signals_hit[:2]),
            "已调整": status,
        })

    # 使用 dataframe 显示
    df_data = []
    for ep in episodes:
        df_data.append({
            "集数": ep.episode,
            "开始": format_timestamp(ep.start_sec),
            "结束": format_timestamp(ep.end_sec),
            "时长": format_timestamp(ep.duration_sec),
            "置信度": ep.confidence,
            "信号": ", ".join(ep.signals_hit[:3]),
        })

    st.dataframe(
        df_data,
        use_container_width=True,
        column_config={
            "置信度": st.column_config.TextColumn(
                "置信度",
                help="high=多信号验证 | medium=仅OCR | low=推断",
            ),
        },
        hide_index=True,
    )

    # ── 详情面板 ──
    st.markdown("---")
    st.subheader("逐集调整")

    selected_ep = st.selectbox(
        "选择要查看/调整的集数",
        options=[e.episode for e in episodes],
        format_func=lambda x: f"第 {x} 集",
    )

    if selected_ep is not None:
        ep = next(e for e in episodes if e.episode == selected_ep)
        _render_episode_detail(ep, tl)

    # ── 操作按钮 ──
    st.markdown("---")
    col_a, col_b, col_c = st.columns(3)

    with col_a:
        if st.button("全部确认", type="secondary", use_container_width=True):
            if low_count > 0:
                st.warning(f"还有 {low_count} 集置信度较低，确认继续？")
            else:
                st.success("所有集数已确认")

    with col_b:
        if st.button("重置所有修改", type="secondary", use_container_width=True):
            st.session_state.edited_episodes = {}
            st.rerun()

    with col_c:
        if st.button("继续裁剪 →", type="primary", use_container_width=True):
            st.session_state.step = "cutting"
            st.rerun()


def _render_timeline(tl: TimeList) -> None:
    """使用 matplotlib 绘制水平时间轴。"""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import numpy as np
    except ImportError:
        st.warning("matplotlib 未安装，无法显示时间轴图表")
        return

    color_map = {"high": "#2ca02c", "medium": "#bcbd22", "low": "#d62728"}

    fig, ax = plt.subplots(figsize=(10, 1.5))
    fig.patch.set_facecolor("none")

    total = tl.duration_sec
    for ep in tl.episodes:
        start_pct = ep.start_sec / total * 100
        width_pct = ep.duration_sec / total * 100
        color = color_map.get(ep.confidence, "#7f7f7f")
        ax.barh(0, width_pct, left=start_pct, height=0.6,
                color=color, edgecolor="white", linewidth=0.5)

        # 集数标签
        mid_pct = start_pct + width_pct / 2
        ax.text(mid_pct, 0, str(ep.episode), ha="center", va="center",
                fontsize=7, color="white", fontweight="bold")

    ax.set_xlim(0, 100)
    ax.set_ylim(-0.5, 0.5)
    ax.set_xlabel("进度 (%)")
    ax.set_yticks([])
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_visible(False)

    # 图例
    from matplotlib.patches import Patch
    legend_patches = [
        Patch(color=color_map["high"], label="高置信"),
        Patch(color=color_map["medium"], label="中置信"),
        Patch(color=color_map["low"], label="低置信"),
    ]
    ax.legend(handles=legend_patches, loc="upper right", fontsize=8, ncol=3)

    plt.tight_layout()
    st.pyplot(fig)
    plt.close(fig)


def _render_episode_detail(ep: Episode, tl: TimeList) -> None:
    """渲染单集详情面板（帧预览 + 时间微调）。"""
    col_left, col_right = st.columns([2, 1])

    with col_left:
        st.markdown(f"**第 {ep.episode} 集** | 置信度: `{ep.confidence}` | 信号: {', '.join(ep.signals_hit)}")

        # 帧预览
        st.caption("边界帧预览")
        preview_dir = os.path.join(st.session_state.work_dir, "previews", f"ep_{ep.episode}")
        if not os.path.exists(preview_dir):
            os.makedirs(preview_dir, exist_ok=True)

        # 在 start_sec 前后抽帧
        existing_frames = sorted(
            [f for f in os.listdir(preview_dir) if f.endswith(".png")]
        ) if os.path.exists(preview_dir) else []

        if not existing_frames or st.button("重新抽帧", key=f"reframe_{ep.episode}"):
            try:
                # 清理旧帧
                for f in os.listdir(preview_dir):
                    os.unlink(os.path.join(preview_dir, f))
                # 在边界前后 ±3 秒抽 5 帧
                offsets = [-3.0, -1.5, 0.0, 1.5, 3.0]
                for i, offset in enumerate(offsets):
                    t = max(0, ep.start_sec + offset)
                    extract_frames(
                        st.session_state.video_path,
                        os.path.join(preview_dir, f"offset_{i}"),
                        interval_sec=0.1,
                        width=320,
                    )
                    # extract_frames 按间隔抽很多帧，我们只需要 t 附近一帧
                    # 简化处理：直接用 FFmpeg 抽单帧
                    _extract_single_frame(
                        st.session_state.video_path,
                        t,
                        os.path.join(preview_dir, f"frame_{i:02d}.png"),
                    )
                st.rerun()
            except Exception as e:
                st.warning(f"抽帧失败: {e}")

        # 显示帧
        frame_files = sorted(
            [f for f in os.listdir(preview_dir) if f.endswith(".png")]
        ) if os.path.exists(preview_dir) else []

        if frame_files:
            frame_cols = st.columns(len(frame_files[:5]))
            for i, (col, fname) in enumerate(zip(frame_cols, frame_files[:5])):
                fpath = os.path.join(preview_dir, fname)
                with col:
                    st.image(str(fpath), caption=f"t={ep.start_sec + (i-2)*1.5:.1f}s", use_container_width=True)

    with col_right:
        st.markdown("**时间微调**")

        # 取编辑后的值
        edit = st.session_state.edited_episodes.get(ep.episode, {})
        new_start = st.number_input(
            "开始时间（秒）",
            min_value=0.0,
            max_value=tl.duration_sec,
            value=edit.get("start_sec", ep.start_sec),
            step=1.0,
            key=f"start_{ep.episode}",
        )
        new_end = st.number_input(
            "结束时间（秒）",
            min_value=0.0,
            max_value=tl.duration_sec,
            value=edit.get("end_sec", ep.end_sec),
            step=1.0,
            key=f"end_{ep.episode}",
        )

        if new_start != ep.start_sec or new_end != ep.end_sec:
            st.caption(f"已调整: {format_timestamp(new_start)} → {format_timestamp(new_end)}")
            if st.button("应用修改", key=f"apply_{ep.episode}"):
                edits = dict(st.session_state.edited_episodes)
                edits[ep.episode] = {"start_sec": new_start, "end_sec": new_end}
                st.session_state.edited_episodes = edits
                st.rerun()

        if ep.episode in st.session_state.edited_episodes:
            if st.button("恢复原始", key=f"revert_{ep.episode}"):
                edits = dict(st.session_state.edited_episodes)
                del edits[ep.episode]
                st.session_state.edited_episodes = edits
                st.rerun()


def _extract_single_frame(video_path: str, timestamp_sec: float, output_path: str) -> None:
    """使用 FFmpeg 抽取单帧。"""
    import subprocess as sp
    cmd = [
        "ffmpeg",
        "-ss", str(timestamp_sec),
        "-i", video_path,
        "-vframes", "1",
        "-q:v", "2",
        "-y",
        output_path,
    ]
    sp.run(cmd, capture_output=True, creationflags=sp.CREATE_NO_WINDOW if sys.platform == "win32" else 0)


# ── 模块四：批量裁剪与输出 ──────────────────────────────────────

def _render_cutting_module() -> None:
    """渲染裁剪输出页面。"""
    st.title("批量裁剪输出")

    tl = st.session_state.time_list
    if tl is None:
        st.warning("未找到时间清单")
        if st.button("返回输入页"):
            st.session_state.step = "input"
            st.rerun()
        return

    # 应用编辑
    if st.session_state.edited_episodes:
        tl = merge_edits(tl, st.session_state.edited_episodes)

    st.subheader("输出配置")

    col1, col2 = st.columns(2)
    with col1:
        output_dir = st.text_input(
            "输出目录",
            value=st.session_state.output_dir,
        )
        st.session_state.output_dir = output_dir

    with col2:
        naming = st.session_state.config_params.get("naming_template", "第{ep:02d}集.mp4")
        st.text_input("命名模板", value=naming, disabled=True)
        preview = naming
        if "{ep:02d}" in preview:
            preview = preview.replace("{ep:02d}", "01")
        elif "{ep}" in preview:
            preview = preview.replace("{ep}", "01")
        st.caption(f"示例：{preview}")

    # 输出预览表格
    st.caption(f"将输出 {len(tl.episodes)} 个文件到: `{output_dir}`")
    preview_rows = []
    for ep in tl.episodes:
        fname = naming
        if "{ep:02d}" in fname:
            fname = fname.replace("{ep:02d}", f"{ep.episode:02d}")
        elif "{ep}" in fname:
            fname = fname.replace("{ep}", str(ep.episode))
        preview_rows.append({
            "文件名": fname,
            "集数": ep.episode,
            "时长": format_timestamp(ep.duration_sec),
        })
    st.dataframe(preview_rows, use_container_width=True, hide_index=True)

    # 开始裁剪
    st.markdown("---")

    if st.button("开始裁剪", type="primary", use_container_width=True):
        os.makedirs(output_dir, exist_ok=True)
        os.makedirs(st.session_state.work_dir, exist_ok=True)

        # 写入裁剪用的时间清单
        cut_tl_path = os.path.join(st.session_state.work_dir, "time_list_for_cut.json")
        tl.to_json(cut_tl_path)

        config_path = os.path.join(st.session_state.work_dir, "config.json")

        cmd = [
            sys.executable, "-u", "-m", "drama_splitter", "cut",
            st.session_state.video_path,
            "--time-list", cut_tl_path,
            "-o", output_dir,
            "--mode", st.session_state.config_params.get("cut_mode", "lossless"),
            "--naming-template", naming,
            "--yes",
        ]
        _run_subprocess(cmd, st.session_state.work_dir)
        st.session_state.step = "cutting"
        st.rerun()

    # 检查裁剪进程
    ret = _check_process()

    if st.session_state.process_status == "running":
        # ── 裁剪中 ──
        elapsed = time.time() - st.session_state.process_start_time
        elapsed_str = f"{int(elapsed // 60)}分{int(elapsed % 60)}秒" if elapsed >= 60 else f"{int(elapsed)}秒"
        st.progress(0.0, text=f"✂️ 裁剪进行中 · 已用 {elapsed_str}")

        logs = st.session_state.process_logs
        if logs:
            st.code("\n".join(logs[-10:]), language=None)
        else:
            st.info("正在逐集裁剪，请稍候...")

        time.sleep(1.5)
        st.rerun()
    elif st.session_state.process_status == "done":
        # ── 完成 ──
        elapsed = time.time() - st.session_state.process_start_time
        elapsed_str = f"{int(elapsed // 60)}分{int(elapsed % 60)}秒" if elapsed >= 60 else f"{int(elapsed)}秒"
        st.progress(1.0, text=f"✅ 裁剪完成 · 总用时 {elapsed_str}")

        logs = st.session_state.process_logs
        if logs:
            with st.expander("查看裁剪日志", expanded=False):
                st.code("\n".join(logs), language=None)

        # 扫描输出文件
        if os.path.exists(output_dir):
            out_files = sorted([
                f for f in os.listdir(output_dir)
                if f.endswith((".mp4", ".mkv", ".ts", ".mov"))
            ])
            if out_files:
                st.subheader("输出文件")
                file_rows = []
                total_size = 0.0
                for fname in out_files:
                    fpath = os.path.join(output_dir, fname)
                    size = os.path.getsize(fpath) / (1024 * 1024)
                    total_size += size
                    file_rows.append({
                        "文件名": fname,
                        "大小": f"{size:.1f}MB",
                    })
                file_rows.append({"文件名": "**合计**", "大小": f"{total_size:.1f}MB"})
                st.dataframe(file_rows, use_container_width=True, hide_index=True)

        st.session_state.step = "done"
        st.rerun()
    elif st.session_state.process_status == "error":
        # ── 失败 ──
        st.error(f"裁剪失败: {st.session_state.process_error[-300:]}")
        logs = st.session_state.process_logs
        if logs:
            with st.expander("查看错误日志", expanded=True):
                st.code("\n".join(logs[-20:]), language=None)
        col1, col2 = st.columns(2)
        with col1:
            if st.button("重试", use_container_width=True):
                st.session_state.process_status = ""
                st.rerun()
        with col2:
            if st.button("返回复核", use_container_width=True):
                st.session_state.step = "review"
                st.rerun()


# ── 模块五：完成 ─────────────────────────────────────────────────

def _render_done_module() -> None:
    """渲染完成页面。"""
    st.title("处理完成！")

    tl = st.session_state.time_list
    episode_count = len(tl.episodes) if tl else 0
    st.success(f"成功将视频拆分为 {episode_count} 集")

    col1, col2, col3 = st.columns(3)

    with col1:
        output_dir = st.session_state.output_dir
        if output_dir and os.path.exists(output_dir):
            if st.button("打开输出目录", use_container_width=True):
                if sys.platform == "win32":
                    os.startfile(output_dir)
                elif sys.platform == "darwin":
                    subprocess.Popen(["open", output_dir])
                else:
                    subprocess.Popen(["xdg-open", output_dir])

    with col2:
        work_dir = st.session_state.work_dir
        if work_dir and os.path.exists(work_dir):
            time_list_path = os.path.join(work_dir, "time_list.json")
            if os.path.exists(time_list_path):
                with open(time_list_path, "r", encoding="utf-8") as f:
                    st.download_button(
                        "下载时间清单 (JSON)",
                        data=f.read(),
                        file_name="time_list.json",
                        mime="application/json",
                        use_container_width=True,
                    )

    with col3:
        if st.button("处理下一个视频", type="primary", use_container_width=True):
            _reset_state(keep_video=False)
            st.rerun()


# ── 主入口 ──────────────────────────────────────────────────────

def main() -> None:
    """Streamlit 应用主入口。"""
    _render_step_nav()
    st.markdown("---")

    step = st.session_state.step

    if step == "input":
        _render_input_module()
    elif step == "analyzing":
        _render_analyzing_module()
    elif step == "review":
        _render_review_module()
    elif step == "cutting":
        _render_cutting_module()
    elif step == "done":
        _render_done_module()
    else:
        st.error(f"未知步骤: {step}")
        st.session_state.step = "input"
        st.rerun()


if __name__ == "__main__":
    main()
