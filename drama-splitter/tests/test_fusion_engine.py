"""融合决策引擎单元测试。"""

import json
import os
import pytest
from unittest.mock import patch, MagicMock

from drama_splitter.fusion.engine import FusionEngine
from drama_splitter.models import Candidate, CandidateList, TimeList
from drama_splitter.models.config import AppConfig


@pytest.fixture
def mock_config() -> AppConfig:
    return AppConfig(
        ocr_engine="rapidocr",
        frame_interval_sec=0.5,
        cluster_window_sec=3.0,
        fingerprint_template_sec=20.0,
        fingerprint_threshold=0.85,
        black_duration=0.3,
        black_pix_threshold=0.10,
        silence_noise="-40dB",
        silence_duration=0.3,
        min_episode_sec=60.0,
        max_episode_sec=300.0,
    )


def _make_ocr_cl(timestamps: list[tuple[int, float]]) -> CandidateList:
    return CandidateList(
        signal_type="ocr",
        video_name="test.mp4",
        duration_sec=480.0,
        candidates=[
            Candidate(
                signal_type="ocr",
                timestamp_sec=ts,
                episode=ep,
                confidence=0.9,
            )
            for ep, ts in timestamps
        ],
    )


def _make_af_cl(timestamps: list[float]) -> CandidateList:
    return CandidateList(
        signal_type="audio_fingerprint",
        video_name="test.mp4",
        duration_sec=480.0,
        candidates=[
            Candidate(
                signal_type="audio_fingerprint",
                timestamp_sec=ts,
                episode=None,
                confidence=0.85,
            )
            for ts in timestamps
        ],
    )


class TestFusionEngine:
    """融合引擎测试。"""

    def test_fuse_ocr_only(self, mock_config) -> None:
        """仅 OCR 候选点融合成功。"""
        engine = FusionEngine(mock_config)
        cl = _make_ocr_cl([(1, 0.0), (2, 120.0), (3, 240.0), (4, 360.0)])
        tl = engine.fuse({"ocr": cl}, 480.0, "test.mp4")
        assert isinstance(tl, TimeList)
        assert len(tl.episodes) == 4
        assert [e.episode for e in tl.episodes] == [1, 2, 3, 4]

    def test_fuse_multi_signal(self, mock_config) -> None:
        """多信号融合。"""
        engine = FusionEngine(mock_config)
        ocr_cl = _make_ocr_cl([(1, 0.0), (2, 120.0), (4, 360.0)])  # 缺口: 第 3 集
        af_cl = _make_af_cl([122.0, 250.0, 362.0])
        tl = engine.fuse({"ocr": ocr_cl, "audio_fingerprint": af_cl}, 480.0, "test.mp4")
        assert len(tl.episodes) >= 3

    def test_fuse_from_files(self, mock_config, tmp_path) -> None:
        """fuse_from_files 从 JSON 加载并融合。"""
        engine = FusionEngine(mock_config)

        # 写 OCR JSON
        ocr_cl = _make_ocr_cl([(1, 0.0), (2, 120.0), (3, 240.0)])
        ocr_cl.to_json(str(tmp_path / "ocr.json"))

        tl = engine.fuse_from_files(str(tmp_path), 360.0, "test.mp4")
        assert len(tl.episodes) == 3

    def test_fuse_from_files_missing_signal_no_error(self, mock_config, tmp_path) -> None:
        """缺少某路 JSON 时不报错。"""
        engine = FusionEngine(mock_config)
        ocr_cl = _make_ocr_cl([(1, 0.0), (2, 120.0)])
        ocr_cl.to_json(str(tmp_path / "ocr.json"))
        # 只有 ocr.json，缺少其他信号
        tl = engine.fuse_from_files(str(tmp_path), 360.0, "test.mp4")
        assert len(tl.episodes) == 2

    def test_export_json(self, mock_config, tmp_path) -> None:
        """export_json 正确输出。"""
        engine = FusionEngine(mock_config)
        cl = _make_ocr_cl([(1, 0.0), (2, 120.0)])
        tl = engine.fuse({"ocr": cl}, 240.0, "test.mp4")
        output = str(tmp_path / "result.json")
        engine.export_json(tl, output)
        assert os.path.exists(output)

    def test_export_csv(self, mock_config, tmp_path) -> None:
        """export_csv 正确输出。"""
        engine = FusionEngine(mock_config)
        cl = _make_ocr_cl([(1, 0.0), (2, 120.0)])
        tl = engine.fuse({"ocr": cl}, 240.0, "test.mp4")
        output = str(tmp_path / "result.csv")
        engine.export_csv(tl, output)
        assert os.path.exists(output)

    def test_export_summary(self, mock_config) -> None:
        """export_summary 输出非空字符串。"""
        engine = FusionEngine(mock_config)
        cl = _make_ocr_cl([(1, 0.0), (2, 120.0)])
        tl = engine.fuse({"ocr": cl}, 240.0, "test.mp4")
        summary = engine.export_summary(tl)
        assert "短剧分集时间清单" in summary
        assert "2 集" in summary

    def test_checkpoint_resume(self, mock_config, tmp_path) -> None:
        """断点续传：第二次调用跳过融合直接加载。"""
        engine = FusionEngine(mock_config)
        cl = _make_ocr_cl([(1, 0.0), (2, 120.0)])
        work_dir = str(tmp_path / "work")

        # 第一次融合
        tl1 = engine.fuse({"ocr": cl}, 240.0, "test.mp4", work_dir=work_dir)

        # 模拟 checkpoint 文件已存在
        assert os.path.exists(os.path.join(work_dir, "time_list.json"))

        # 第二次：应该直接加载 checkpoint
        tl2 = engine.fuse({"ocr": cl}, 240.0, "test.mp4", work_dir=work_dir)
        assert len(tl2.episodes) == len(tl1.episodes)
