"""ffmpeg 环境检测

提供 ffmpeg 可用性检查和视频信息获取功能。
"""

import subprocess
import shutil
import json
import logging
from modules.exceptions import FFmpegError

logger = logging.getLogger(__name__)


def check_ffmpeg() -> tuple[bool, str]:
    """检查 ffmpeg 是否已安装且在 PATH 中。

    Returns:
        (is_available, message)
        如果可用，message 包含版本号。
        如果不可用，message 包含安装指引链接。
    """
    # 检查 ffmpeg
    ffmpeg_path = shutil.which("ffmpeg")
    if ffmpeg_path is None:
        return False, "未找到 ffmpeg。请安装 ffmpeg 并添加到系统 PATH。\n" \
                      "下载地址: https://ffmpeg.org/download.html"

    try:
        result = subprocess.run(
            ["ffmpeg", "-version"],
            capture_output=True,
            text=True,
            timeout=10
        )
        if result.returncode != 0:
            return False, f"ffmpeg 执行异常: {result.stderr.strip()}"

        # 提取第一行版本信息
        version_line = result.stdout.strip().split('\n')[0]
        return True, version_line
    except FileNotFoundError:
        return False, "未找到 ffmpeg。请安装 ffmpeg 并添加到系统 PATH。"
    except Exception as e:
        return False, f"检查 ffmpeg 时出错: {str(e)}"


def get_video_info(video_path: str) -> dict:
    """用 ffprobe 获取视频基本信息。

    Args:
        video_path: 视频文件路径

    Returns:
        {
            'duration': float,     # 时长（秒）
            'fps': float,          # 帧率
            'width': int,          # 宽度（像素）
            'height': int,         # 高度（像素）
            'total_frames': int    # 总帧数
        }

    Raises:
        FFmpegError: ffprobe 执行失败时抛出
    """
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v", "quiet",
                "-print_format", "json",
                "-show_format",
                "-show_streams",
                video_path
            ],
            capture_output=True,
            text=True,
            timeout=60
        )

        if result.returncode != 0:
            raise FFmpegError(f"ffprobe 执行失败: {result.stderr.strip()}")

        data = json.loads(result.stdout)

        # 从 format 中获取时长
        duration = float(data.get("format", {}).get("duration", 0))

        # 从第一个视频流中获取信息
        video_stream = None
        for stream in data.get("streams", []):
            if stream.get("codec_type") == "video":
                video_stream = stream
                break

        if video_stream is None:
            raise FFmpegError("未找到视频流")

        # 解析帧率（可能是 "30000/1001" 格式）
        fps_str = video_stream.get("r_frame_rate", "0/1")
        if '/' in fps_str:
            num, den = fps_str.split('/')
            fps = float(num) / float(den) if float(den) != 0 else 0.0
        else:
            fps = float(fps_str)

        width = int(video_stream.get("width", 0))
        height = int(video_stream.get("height", 0))

        # 总帧数
        nb_frames = video_stream.get("nb_frames")
        if nb_frames is not None:
            total_frames = int(nb_frames)
        else:
            # 如果 nb_frames 不可用，通过 duration * fps 估算
            total_frames = int(duration * fps) if fps > 0 else 0

        logger.debug(
            f"视频信息: duration={duration}s, fps={fps:.2f}, "
            f"resolution={width}x{height}, frames={total_frames}"
        )

        return {
            "duration": duration,
            "fps": fps,
            "width": width,
            "height": height,
            "total_frames": total_frames
        }

    except json.JSONDecodeError as e:
        raise FFmpegError(f"ffprobe 输出解析失败: {str(e)}")
    except FFmpegError:
        raise
    except Exception as e:
        raise FFmpegError(f"获取视频信息时出错: {str(e)}")
