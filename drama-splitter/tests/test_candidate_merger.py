"""候选点时空聚类器单元测试。"""

import pytest

from drama_splitter.fusion.candidate_merger import CandidateMerger, MergedBoundary
from drama_splitter.models import Candidate, CandidateList


def _make_candidate(
    signal: str, ts: float, ep: int | None = None, conf: float = 0.9
) -> Candidate:
    return Candidate(
        signal_type=signal,
        timestamp_sec=ts,
        episode=ep,
        confidence=conf,
    )


def _make_cl(candidates: list[Candidate], signal_type: str = "ocr") -> CandidateList:
    return CandidateList(
        signal_type=signal_type,
        video_name="test.mp4",
        duration_sec=600.0,
        candidates=candidates,
    )


class TestCandidateMerger:
    """候选点聚类合并器测试。"""

    def test_empty_input_returns_empty(self) -> None:
        """空输入返回空列表。"""
        merger = CandidateMerger()
        result = merger.merge({})
        assert result == []

    def test_all_none_candidate_lists(self) -> None:
        """所有 CandidateList 为 None 返回空。"""
        merger = CandidateMerger()
        result = merger.merge({"ocr": None, "audio_fingerprint": None})
        assert result == []

    def test_cluster_within_window(self) -> None:
        """时间差 ≤ 3s 的归入同一簇。"""
        merger = CandidateMerger(window_sec=3.0)
        cl1 = _make_cl(
            [_make_candidate("ocr", 100.0, ep=1)],
            signal_type="ocr",
        )
        cl2 = _make_cl(
            [_make_candidate("audio_fingerprint", 102.0)],
            signal_type="audio_fingerprint",
        )
        result = merger.merge({"ocr": cl1, "audio_fingerprint": cl2})
        assert len(result) == 1
        assert result[0].episode == 1
        assert "ocr" in result[0].signals
        assert "audio_fingerprint" in result[0].signals
        assert result[0].signal_count == 2

    def test_separate_clusters(self) -> None:
        """时间差 > 3s 的归入不同簇。"""
        merger = CandidateMerger(window_sec=3.0)
        cl = _make_cl(
            [
                _make_candidate("ocr", 100.0, ep=1),
                _make_candidate("ocr", 200.0, ep=2),
            ],
            signal_type="ocr",
        )
        result = merger.merge({"ocr": cl})
        assert len(result) == 2

    def test_median_timestamp(self) -> None:
        """簇内中位时间戳正确。"""
        merger = CandidateMerger(window_sec=3.0)
        cl = _make_cl(
            [
                _make_candidate("ocr", 100.0, ep=1),
                _make_candidate("audio_fingerprint", 101.0),
                _make_candidate("black_silence", 102.5),
            ],
            signal_type="ocr",
        )
        result = merger.merge({"ocr": cl})
        assert len(result) == 1
        # 中位数: [100.0, 101.0, 102.5] → 101.0
        assert result[0].timestamp_sec == 101.0

    def test_filter_scene_cluster_only(self) -> None:
        """仅 scene_cluster 的单信号簇被过滤。"""
        merger = CandidateMerger(window_sec=3.0)
        cl = _make_cl(
            [
                _make_candidate("scene_cluster", 150.0),
                _make_candidate("scene_cluster", 152.0),
            ],
            signal_type="scene_cluster",
        )
        result = merger.merge({"scene_cluster": cl})
        assert len(result) == 0

    def test_ocr_episode_transfer(self) -> None:
        """OCR 集数传递到合并边界。"""
        merger = CandidateMerger(window_sec=3.0)
        ocr_cl = _make_cl(
            [_make_candidate("ocr", 100.0, ep=5)],
            signal_type="ocr",
        )
        bs_cl = _make_cl(
            [_make_candidate("black_silence", 101.0)],
            signal_type="black_silence",
        )
        result = merger.merge({"ocr": ocr_cl, "black_silence": bs_cl})
        assert len(result) == 1
        assert result[0].episode == 5

    def test_split_different_ocr_episodes(self) -> None:
        """同一簇内两个不同 OCR 集数被拆分。"""
        merger = CandidateMerger(window_sec=3.0)
        cl = _make_cl(
            [
                _make_candidate("ocr", 99.0, ep=1),
                _make_candidate("ocr", 101.0, ep=2),
            ],
            signal_type="ocr",
        )
        result = merger.merge({"ocr": cl})
        assert len(result) == 2
        episodes = {b.episode for b in result}
        assert episodes == {1, 2}
