"""置信度分级标注器单元测试。"""

import pytest

from drama_splitter.fusion.confidence_scorer import ConfidenceScorer
from drama_splitter.fusion.episode_aligner import AlignedEpisode
from drama_splitter.models import TimeList


def _make_aligned(
    episode: int,
    ts: float,
    signals: list[str],
    is_inferred: bool = False,
) -> AlignedEpisode:
    return AlignedEpisode(
        episode=episode,
        timestamp_sec=ts,
        signals=signals,
        is_inferred=is_inferred,
        confidence_note="test",
    )


class TestConfidenceScorer:
    """置信度分级标注器测试。"""

    def test_ocr_plus_other_signal_is_high(self) -> None:
        """OCR + 另一个信号 → high。"""
        scorer = ConfidenceScorer(min_episode_sec=30, max_episode_sec=600)
        episodes = [_make_aligned(1, 0.0, ["ocr", "audio_fingerprint"])]
        tl = scorer.score(episodes, 120.0, "test.mp4")
        assert tl.episodes[0].confidence == "high"

    def test_ocr_only_is_medium(self) -> None:
        """仅 OCR → medium。"""
        scorer = ConfidenceScorer(min_episode_sec=30, max_episode_sec=600)
        episodes = [_make_aligned(1, 0.0, ["ocr"])]
        tl = scorer.score(episodes, 120.0, "test.mp4")
        assert tl.episodes[0].confidence == "medium"

    def test_inferred_one_signal_is_low(self) -> None:
        """推断、单信号 → low。"""
        scorer = ConfidenceScorer()
        episodes = [
            _make_aligned(1, 0.0, ["audio_fingerprint"], is_inferred=True)
        ]
        tl = scorer.score(episodes, 600.0, "test.mp4")
        assert tl.episodes[0].confidence == "low"

    def test_inferred_two_signals_is_medium(self) -> None:
        """推断、双信号 → medium。"""
        scorer = ConfidenceScorer(min_episode_sec=30, max_episode_sec=600)
        episodes = [
            _make_aligned(
                1, 0.0,
                ["audio_fingerprint", "black_silence"],
                is_inferred=True,
            )
        ]
        tl = scorer.score(episodes, 120.0, "test.mp4")
        assert tl.episodes[0].confidence == "medium"

    def test_first_ep_start_fixed_to_zero(self) -> None:
        """第一集 start_sec > 5 被修正为 0。"""
        scorer = ConfidenceScorer(min_episode_sec=30, max_episode_sec=600)
        episodes = [
            _make_aligned(1, 10.0, ["ocr"]),
            _make_aligned(2, 120.0, ["ocr"]),
        ]
        tl = scorer.score(episodes, 240.0, "test.mp4")
        assert tl.episodes[0].start_sec == 0.0
        assert tl.episodes[0].end_sec == 120.0

    def test_duration_too_short_is_low(self) -> None:
        """时长过短标记为 low。"""
        scorer = ConfidenceScorer(min_episode_sec=60.0)
        episodes = [
            _make_aligned(1, 0.0, ["ocr"]),
            _make_aligned(2, 10.0, ["ocr"]),  # 仅 10s
        ]
        tl = scorer.score(episodes, 120.0, "test.mp4")
        short_eps = [e for e in tl.episodes if e.duration_sec < 60.0]
        assert all(e.confidence == "low" for e in short_eps)

    def test_returns_valid_timelist(self) -> None:
        """返回的 TimeList 可通过模型验证。"""
        scorer = ConfidenceScorer()
        episodes = [
            _make_aligned(1, 0.0, ["ocr"]),
            _make_aligned(2, 120.0, ["ocr"]),
            _make_aligned(3, 240.0, ["ocr"]),
        ]
        tl = scorer.score(episodes, 360.0, "test.mp4")
        assert isinstance(tl, TimeList)
        assert len(tl.episodes) == 3

    def test_scene_cluster_not_counted_as_signal(self) -> None:
        """scene_cluster 不计入 '其他信号'。"""
        scorer = ConfidenceScorer(min_episode_sec=30, max_episode_sec=600)
        # OCR + scene_cluster 不算高置信度
        episodes = [
            _make_aligned(1, 0.0, ["ocr", "scene_cluster"]),
        ]
        tl = scorer.score(episodes, 120.0, "test.mp4")
        # scene_cluster 被排除在 other_signals 外
        assert tl.episodes[0].confidence == "medium"
