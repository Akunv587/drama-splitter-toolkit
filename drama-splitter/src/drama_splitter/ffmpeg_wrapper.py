"""FFmpeg/ffprobe 命令封装层。

封装所有外部命令调用，统一错误处理和返回值类型。
所有函数通过 ``subprocess.run`` 调用，检查 returncode，失败时抛出对应异常。
"""

import json
import logging
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import List, Tuple

from drama_splitter.exceptions import (
    FFmpegCutError,
    FFmpegNotFoundError,
    FFprobeError,
    FrameExtractionError,
)
from drama_splitter.models import VideoMeta

logger = logging.getLogger(__name__)

# ── 环境检测 ───────────────────────────────────────────────────

_FFMPEG = shutil.which("ffmpeg")
_FFPROBE = shutil.which("ffprobe") or shutil.which("ffprobe.exe")

if not _FFMPEG:
    logger.warning("ffmpeg 未在 PATH 中找到，视频相关功能不可用。")
if not _FFPROBE:
    logger.warning("ffprobe 未在 PATH 中找到，视频信息读取不可用。")


def _ensure_ffmpeg() -> None:
    if not _FFMPEG:
        raise FFmpegNotFoundError(
            "ffmpeg 未安装或不在 PATH 中。请安装 FFmpeg 4.4+。\n"
            "macOS: brew install ffmpeg\n"
            "Ubuntu: apt install ffmpeg\n"
            "Windows: 从 https://ffmpeg.org 下载"
        )


def _ensure_ffprobe() -> None:
    if not _FFPROBE:
        raise FFmpegNotFoundError(
            "ffprobe 未安装或不在 PATH 中。请安装 FFmpeg 4.4+。"
        )


# ── 视频信息探测 ───────────────────────────────────────────────

def probe_video(file_path: str) -> VideoMeta:
    """调用 ffprobe 获取视频元信息。

    Args:
        file_path: 视频文件路径。

    Returns:
        填充完整的 ``VideoMeta`` 实例。

    Raises:
        FFmpegNotFoundError: ffprobe 不可用。
        FFprobeError: ffprobe 返回错误或输出无法解析。
    """
    _ensure_ffprobe()

    cmd = [
        _FFPROBE,
        "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        file_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        raise FFprobeError(
            f"ffprobe 读取视频信息失败: {result.stderr.strip()}\n文件: {file_path}"
        )

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as e:
        raise FFprobeError(f"ffprobe 输出 JSON 解析失败: {e}") from e

    fmt = data.get("format", {})
    streams = data.get("streams", [])

    # 找视频流
    video_stream = None
    audio_stream_count = 0
    for s in streams:
        if s.get("codec_type") == "video" and video_stream is None:
            video_stream = s
        elif s.get("codec_type") == "audio":
            audio_stream_count += 1

    if video_stream is None:
        raise FFprobeError("未在文件中找到视频流。")

    # 解析帧率（r_frame_rate 为分数形式如 "30000/1001"）
    fps_str = video_stream.get("r_frame_rate", "0/1")
    try:
        num, den = fps_str.split("/")
        fps = float(num) / float(den) if float(den) != 0 else 0.0
    except (ValueError, ZeroDivisionError):
        fps = 0.0

    duration = float(fmt.get("duration", 0))
    file_size_bytes = int(fmt.get("size", 0))
    file_size_mb = file_size_bytes / (1024 * 1024)

    file_path_obj = Path(file_path)

    return VideoMeta(
        file_path=str(file_path_obj.resolve()),
        file_name=file_path_obj.name,
        format=fmt.get("format_name", "unknown"),
        duration_sec=duration,
        width=int(video_stream.get("width", 0)),
        height=int(video_stream.get("height", 0)),
        codec=video_stream.get("codec_name", "unknown"),
        fps=round(fps, 2),
        file_size_mb=round(file_size_mb, 2),
    )


# ── 视频抽帧 ───────────────────────────────────────────────────

def extract_frames(
    file_path: str,
    output_dir: str,
    interval_sec: float = 0.5,
    width: int = 640,
) -> List[Tuple[str, float]]:
    """按固定间隔抽取视频帧，帧文件按序号命名。

    时间戳通过 ``帧序号 / fps`` 计算，不使用 ``-frame_pts`` 模式。

    Args:
        file_path: 视频文件路径。
        output_dir: 帧图片输出目录。
        interval_sec: 抽帧间隔（秒）。
        width: 缩放宽度（保持比例）。

    Returns:
        ``[(frame_path, timestamp_sec), ...]`` 列表，按时间戳升序。

    Raises:
        FFmpegNotFoundError: ffmpeg 不可用。
        FrameExtractionError: 抽帧结果为空。
    """
    _ensure_ffmpeg()

    os.makedirs(output_dir, exist_ok=True)
    fps = 1.0 / interval_sec
    output_pattern = os.path.join(output_dir, "frame_%08d.png")

    cmd = [
        _FFMPEG,
        "-i", file_path,
        "-vf", f"fps={fps},scale={width}:-1",
        "-y",
        output_pattern,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        raise FrameExtractionError(
            f"FFmpeg 抽帧失败: {result.stderr.strip()}\n文件: {file_path}"
        )

    # 收集输出帧文件
    frame_files = sorted(Path(output_dir).glob("frame_*.png"))
    if not frame_files:
        raise FrameExtractionError(
            f"FFmpeg 未输出任何帧文件。视频: {file_path}"
        )

    result_list: List[Tuple[str, float]] = []
    for f in frame_files:
        # 从文件名提取帧序号（frame_00000001.png → 1）
        match = re.search(r"frame_(\d+)\.png", f.name)
        if match:
            frame_idx = int(match.group(1))
            timestamp_sec = (frame_idx - 1) * interval_sec
            result_list.append((str(f), timestamp_sec))

    result_list.sort(key=lambda x: x[1])
    logger.info(
        "抽帧完成: %d 帧, 间隔 %.2fs, 输出到 %s",
        len(result_list), interval_sec, output_dir,
    )
    return result_list


# ── 音频提取 ───────────────────────────────────────────────────

def extract_audio(
    file_path: str,
    output_path: str,
    sample_rate: int = 16000,
) -> str:
    """提取视频音轨为 16kHz 单声道 WAV。

    Args:
        file_path: 视频文件路径。
        output_path: 音频输出路径。
        sample_rate: 采样率（Hz），默认 16000。

    Returns:
        输出文件路径。

    Raises:
        FFmpegNotFoundError: ffmpeg 不可用。
        FFprobeError: 视频无音轨。
    """
    _ensure_ffmpeg()

    cmd = [
        _FFMPEG,
        "-i", file_path,
        "-vn",
        "-ac", "1",
        "-ar", str(sample_rate),
        "-map", "0:a:0",
        "-f", "wav",
        "-y",
        output_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        stderr = result.stderr.strip()
        # 多种 "无音轨" 错误模式：map 匹配不到流、无效的 map 参数等
        if any(
            kw in stderr
            for kw in ("matches no streams", "Invalid argument")
        ):
            raise FFprobeError("未检测到音轨，音频指纹检测将自动跳过。")
        raise FFprobeError(f"音频提取失败: {stderr}")

    logger.info("音频提取完成: %s", output_path)
    return output_path


# ── 黑帧 + 静音联合检测 ───────────────────────────────────────

def detect_black_silence(
    file_path: str,
    black_duration: float = 0.3,
    black_pix_threshold: float = 0.10,
    silence_noise: str = "-40dB",
    silence_duration: float = 0.3,
) -> dict:
    """同时运行 blackdetect 和 silencedetect，解析输出。

    Args:
        file_path: 视频文件路径。
        black_duration: 黑帧最短持续秒数。
        black_pix_threshold: 黑帧像素阈值 (0–1)。
        silence_noise: 静音噪声阈值（如 "-40dB"）。
        silence_duration: 静音最短持续秒数。

    Returns:
        ``{"black_segments": [(start, end), ...], "silence_segments": [(start, end), ...]}``

    Raises:
        FFmpegNotFoundError: ffmpeg 不可用。
    """
    _ensure_ffmpeg()

    cmd = [
        _FFMPEG,
        "-i", file_path,
        "-vf", f"blackdetect=d={black_duration}:pix_th={black_pix_threshold}",
        "-af", f"silencedetect=n={silence_noise}:d={silence_duration}",
        "-f", "null",
        "-",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    # 注意：blackdetect/silencedetect 的输出在 stderr 中，即使 returncode 为 0
    output = result.stderr

    # 解析 blackdetect
    black_segments: List[Tuple[float, float]] = []
    for line in output.split("\n"):
        if "black_start:" in line:
            start_match = re.search(r"black_start:([\d.]+)", line)
            end_match = re.search(r"black_end:([\d.]+)", line)
            if start_match and end_match:
                black_segments.append((float(start_match.group(1)), float(end_match.group(1))))

    # 解析 silencedetect
    silence_segments: List[Tuple[float, float]] = []
    silence_start: float | None = None
    for line in output.split("\n"):
        if "silence_start:" in line:
            m = re.search(r"silence_start:\s*([\d.]+)", line)
            if m:
                silence_start = float(m.group(1))
        elif "silence_end:" in line and silence_start is not None:
            m = re.search(r"silence_end:\s*([\d.]+)", line)
            if m:
                silence_segments.append((silence_start, float(m.group(1))))
                silence_start = None

    logger.info(
        "黑帧静音检测: %d 个黑帧段, %d 个静音段",
        len(black_segments), len(silence_segments),
    )
    return {
        "black_segments": black_segments,
        "silence_segments": silence_segments,
    }


# ── 视频裁剪 ───────────────────────────────────────────────────

def cut_segment(
    file_path: str,
    start_sec: float,
    end_sec: float,
    output_path: str,
    mode: str = "lossless",
) -> str:
    """裁剪视频片段。

    Args:
        file_path: 源视频路径。
        start_sec: 起始时间（秒）。
        end_sec: 结束时间（秒）。
        output_path: 输出文件路径。
        mode: ``"lossless"``（流复制）或 ``"reencode"``（重编码）。

    Returns:
        输出文件路径。

    Raises:
        FFmpegNotFoundError: ffmpeg 不可用。
        FFmpegCutError: 裁剪失败。
    """
    _ensure_ffmpeg()

    duration = end_sec - start_sec
    if duration <= 0:
        raise FFmpegCutError(f"无效时间区间: start={start_sec}, end={end_sec}")

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

    if mode == "lossless":
        cmd = [
            _FFMPEG,
            "-ss", str(start_sec),
            "-i", file_path,
            "-to", str(end_sec),
            "-c", "copy",
            "-avoid_negative_ts", "make_zero",
            "-y",
            output_path,
        ]
    elif mode == "reencode":
        cmd = [
            _FFMPEG,
            "-i", file_path,
            "-ss", str(start_sec),
            "-to", str(end_sec),
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "20",
            "-c:a", "aac",
            "-y",
            output_path,
        ]
    else:
        raise FFmpegCutError(f"未知裁剪模式: {mode}，可选 'lossless' 或 'reencode'")

    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        raise FFmpegCutError(
            f"FFmpeg 裁剪失败 [{start_sec}s–{end_sec}s]: {result.stderr.strip()}"
        )

    logger.info("裁剪完成: %s [%.1fs–%.1fs] → %s", mode, start_sec, end_sec, output_path)
    return output_path
