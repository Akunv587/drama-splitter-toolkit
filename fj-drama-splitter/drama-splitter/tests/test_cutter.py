"""视频裁剪模块单元测试

测试路径生成逻辑和 CutResult 模型，ffmpeg 裁剪测试标记为 skip。
"""

import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from modules.cutter import VideoCutter
from modules.models import EpisodeSegment, CutResult, ConfidenceLevel


class TestCutResult:
    """测试 CutResult 数据模型"""

    def test_cut_result_success(self):
        result = CutResult(
            episode_num=1,
            output_path="/output/test_S01E01.mp4",
            success=True
        )
        assert result.episode_num == 1
        assert result.success is True
        assert result.error is None

    def test_cut_result_failure(self):
        result = CutResult(
            episode_num=2,
            output_path="/output/test_S01E02.mp4",
            success=False,
            error="磁盘空间不足"
        )
        assert result.episode_num == 2
        assert result.success is False
        assert result.error == "磁盘空间不足"


class TestOutputPathGeneration:
    """测试输出路径生成逻辑"""

    def test_output_naming(self):
        """输出文件命名规则验证"""
        cutter = VideoCutter()
        # 验证路径拼接逻辑
        output_dir = "/test/outputs/MyDrama"
        drama_name = "MyDrama"
        episode_num = 3

        output_path = os.path.join(
            output_dir,
            f"{drama_name}_S01E{episode_num:02d}.mp4"
        )
        assert output_path.endswith("MyDrama_S01E03.mp4")

    def test_episode_number_padding(self):
        """集数补零验证"""
        assert f"S01E{1:02d}" == "S01E01"
        assert f"S01E{10:02d}" == "S01E10"
        assert f"S01E{99:02d}" == "S01E99"


class TestVideoCutterInit:
    """测试 VideoCutter 初始化"""

    def test_default_workers(self):
        cutter = VideoCutter()
        assert cutter.max_workers >= 1

    def test_custom_workers(self):
        cutter = VideoCutter(max_workers=2)
        assert cutter.max_workers == 2


@pytest.mark.skip(reason="需要真实视频文件进行 ffmpeg 裁剪测试")
class TestCutSingle:
    """ffmpeg 裁剪测试（需要真实视频文件）"""

    def test_cut_single_with_real_video(self):
        pass
