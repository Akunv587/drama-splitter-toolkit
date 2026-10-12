"""基础时间清单生成器单元测试。"""

import pytest

from drama_splitter.exceptions import InsufficientCandidatesError
from drama_splitter.fusion.basic_builder import BasicTimeListBuilder
from drama_splitter.models import Candidate, CandidateList


def _make_candidate_list(
    candidates: list[tuple[int, float]],
    duration: float = 540.0,
    video_name: str = "test.mp4",
) -> CandidateList:
    return CandidateList(
        signal_type="ocr",
        video_name=video_name,
        duration_sec=duration,
        candidates=[
            Candidate(
                signal_type="ocr",
                timestamp_sec=ts,
                episode=ep,
                confidence=0.9,
            )
            for ep, ts in candidates
        ],
    )


def test_build_three_episodes() -> None:
    """3 个 OCR 候选点（集数 1@0s, 2@180s, 3@360s, 视频 540s）。"""
    cl = _make_candidate_list([(1, 0.0), (2, 180.0), (3, 360.0)])
    builder = BasicTimeListBuilder()
    tl = builder.build(cl)

    assert len(tl.episodes) == 3
    assert tl.episodes[0].episode == 1
    assert tl.episodes[0].start_sec == 0.0
    assert tl.episodes[0].end_sec == 180.0

    assert tl.episodes[1].episode == 2
    assert tl.episodes[1].start_sec == 180.0
    assert tl.episodes[1].end_sec == 360.0

    # 最后一集 end_sec = 视频总时长
    assert tl.episodes[2].episode == 3
    assert tl.episodes[2].end_sec == 540.0


def test_insufficient_candidates_raises() -> None:
    """候选点 < 2 个时抛出异常。"""
    cl = _make_candidate_list([(1, 0.0)])
    builder = BasicTimeListBuilder()
    with pytest.raises(InsufficientCandidatesError):
        builder.build(cl)


def test_episode_start_from_zero_corrected() -> None:
    """集数从 0 开始时修正为 1。"""
    cl = _make_candidate_list([(0, 0.0), (1, 180.0)], duration=360.0)
    builder = BasicTimeListBuilder()
    tl = builder.build(cl)
    assert tl.episodes[0].episode == 1  # 修正为 1
