"""集数序列对齐器单元测试。"""

import pytest

from drama_splitter.exceptions import NoBoundariesError
from drama_splitter.fusion.candidate_merger import MergedBoundary
from drama_splitter.fusion.episode_aligner import AlignedEpisode, EpisodeAligner
from drama_splitter.models import Candidate


def _make_boundary(
    ts: float,
    signals: list[str],
    episode: int | None = None,
) -> MergedBoundary:
    return MergedBoundary(
        timestamp_sec=ts,
        signals=signals,
        episode=episode,
        candidates=[],
        signal_count=len(signals),
    )


class TestEpisodeAligner:
    """集数序列对齐器测试。"""

    def test_empty_boundaries_raises(self) -> None:
        """空边界列表抛出 NoBoundariesError。"""
        aligner = EpisodeAligner()
        with pytest.raises(NoBoundariesError):
            aligner.align([], 600.0)

    def test_ocr_skeleton_complete(self) -> None:
        """OCR 骨架完整：直接使用。"""
        aligner = EpisodeAligner()
        boundaries = [
            _make_boundary(0.0, ["ocr"], episode=1),
            _make_boundary(120.0, ["ocr"], episode=2),
            _make_boundary(240.0, ["ocr"], episode=3),
        ]
        result = aligner.align(boundaries, 360.0)
        assert len(result) == 3
        assert all(not e.is_inferred for e in result)
        assert [e.episode for e in result] == [1, 2, 3]

    def test_gap_fill_with_non_ocr(self) -> None:
        """OCR 缺口用非 OCR 边界补全。"""
        aligner = EpisodeAligner()
        boundaries = [
            _make_boundary(0.0, ["ocr"], episode=1),
            _make_boundary(120.0, ["ocr"], episode=2),
            # 缺口: 集 3 缺失
            _make_boundary(250.0, ["audio_fingerprint"], episode=None),  # 在 2 和 4 之间
            _make_boundary(360.0, ["ocr"], episode=4),
        ]
        result = aligner.align(boundaries, 480.0)
        episodes = [e.episode for e in result]
        assert 3 in episodes  # 补全了第 3 集
        inferred = [e for e in result if e.episode == 3]
        assert len(inferred) == 1
        assert inferred[0].is_inferred is True

    def test_auto_numbering_no_ocr(self) -> None:
        """无 OCR 时自动编号。"""
        aligner = EpisodeAligner()
        boundaries = [
            _make_boundary(0.0, ["black_silence"], episode=None),
            _make_boundary(120.0, ["scene_cluster"], episode=None),
            _make_boundary(240.0, ["audio_fingerprint"], episode=None),
        ]
        result = aligner.align(boundaries, 360.0)
        assert len(result) == 3
        assert all(e.is_inferred for e in result)
        assert [e.episode for e in result] == [1, 2, 3]

    def test_auto_numbering_single_ocr(self) -> None:
        """单个 OCR 边界时以它为锚点自动编号。"""
        aligner = EpisodeAligner()
        boundaries = [
            _make_boundary(0.0, ["black_silence"], episode=None),
            _make_boundary(120.0, ["ocr"], episode=5),
            _make_boundary(240.0, ["audio_fingerprint"], episode=None),
        ]
        result = aligner.align(boundaries, 360.0)
        episodes = sorted(e.episode for e in result)
        assert episodes == [4, 5, 6]
        # 第 5 集为直接识别
        ep5 = next(e for e in result if e.episode == 5)
        assert ep5.is_inferred is False

    def test_prepend_missing_episodes(self) -> None:
        """最小集数 > 1 且 ≤ 5 时前插。"""
        aligner = EpisodeAligner()
        boundaries = [
            _make_boundary(60.0, ["ocr"], episode=3),
            _make_boundary(180.0, ["ocr"], episode=4),
        ]
        result = aligner.align(boundaries, 300.0)
        episodes = sorted(e.episode for e in result)
        assert 1 in episodes
        assert 2 in episodes
        assert 3 in episodes
        assert 4 in episodes

    def test_no_prepend_when_min_large(self) -> None:
        """最小集数 > 5 时不前插。"""
        aligner = EpisodeAligner()
        boundaries = [
            _make_boundary(60.0, ["ocr"], episode=7),
            _make_boundary(180.0, ["ocr"], episode=8),
        ]
        result = aligner.align(boundaries, 300.0)
        episodes = sorted(e.episode for e in result)
        assert 1 not in episodes
        assert episodes == [7, 8]
