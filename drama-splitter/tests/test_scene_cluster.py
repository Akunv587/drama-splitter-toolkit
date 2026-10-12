"""场景聚类检测器单元测试。"""

import pytest
from unittest.mock import patch, MagicMock

from drama_splitter.detectors.scene_cluster import SceneClusterDetector
from drama_splitter.models import CandidateList


class TestSceneClusterDetector:
    """场景聚类检测器测试。"""

    def test_init_defaults(self) -> None:
        """默认参数。"""
        d = SceneClusterDetector()
        assert d.min_episode_sec == 60.0
        assert d.max_episode_sec == 300.0

    def test_init_custom(self) -> None:
        """自定义参数。"""
        d = SceneClusterDetector(min_episode_sec=90.0, max_episode_sec=240.0)
        assert d.min_episode_sec == 90.0
        assert d.max_episode_sec == 240.0

    def test_detect_scenedetect_failure(self) -> None:
        """PySceneDetect 失败 -> 返回空 CandidateList。"""
        d = SceneClusterDetector()
        with patch(
            "drama_splitter.detectors.scene_cluster.detect",
            side_effect=RuntimeError("no video stream"),
        ):
            result = d.detect("test.mp4", 600.0, "test.mp4")
            assert isinstance(result, CandidateList)
            assert result.signal_type == "scene_cluster"
            assert len(result.candidates) == 0

    def test_detect_no_scenes(self) -> None:
        """无场景切换 -> 空。"""
        d = SceneClusterDetector()
        with patch(
            "drama_splitter.detectors.scene_cluster.detect",
            return_value=[],
        ):
            result = d.detect("test.mp4", 600.0, "test.mp4")
            assert len(result.candidates) == 0

    def test_cluster_by_duration_empty(self) -> None:
        """空间隔 -> 默认 120s。"""
        d = SceneClusterDetector()
        typical = d._cluster_by_duration([])
        assert typical == 120.0

    def test_cluster_by_duration_filtered(self) -> None:
        """所有间隔被过滤 -> 默认 120s。"""
        d = SceneClusterDetector()
        # 全部 < 30s，被 min_episode_sec * 0.5 过滤
        typical = d._cluster_by_duration([1.0, 2.0, 5.0, 10.0])
        assert typical == 120.0

    def test_cluster_by_duration_normal(self) -> None:
        """正常聚类 -> 找到典型时长。"""
        d = SceneClusterDetector(min_episode_sec=60, max_episode_sec=300)
        # 模拟 5 集每集约 120s 的间隔
        intervals = [118.0, 122.0, 119.0, 121.0, 125.0, 500.0]
        typical = d._cluster_by_duration(intervals)
        # 应该在 120 附近
        assert 100.0 <= typical <= 140.0

    def test_cluster_by_duration_outliers_excluded(self) -> None:
        """异常值被排除。"""
        d = SceneClusterDetector(min_episode_sec=60, max_episode_sec=300)
        # 2s 和 1000s 的应该被过滤
        intervals = [2.0, 120.0, 121.0, 119.0, 1000.0]
        typical = d._cluster_by_duration(intervals)
        assert 100.0 <= typical <= 140.0
