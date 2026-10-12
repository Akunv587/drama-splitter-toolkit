"""pytest 共享 fixtures。

提供测试视频生成、模拟数据等通用 fixture。
"""

import subprocess
from pathlib import Path

import pytest


@pytest.fixture(scope="session")
def test_video_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """生成一个 10 秒测试视频，供各模块测试使用。

    使用 FFmpeg 的 testsrc 滤镜生成彩色测试画面。
    视频参数：320×240, 30fps, libx264 编码。
    """
    tmp_dir = tmp_path_factory.mktemp("video")
    video_path = tmp_dir / "test.mp4"

    cmd = [
        "ffmpeg",
        "-f", "lavfi",
        "-i", "testsrc=duration=10:size=320x240:rate=30",
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-y",
        str(video_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        pytest.skip(f"FFmpeg 不可用，无法生成测试视频: {result.stderr}")

    return video_path


@pytest.fixture
def test_video_meta():
    """模拟 VideoMeta 对象。在步骤 0.2 完成后可用。"""
    pytest.skip("VideoMeta 模型尚未定义，待步骤 0.2 完成后启用。")
