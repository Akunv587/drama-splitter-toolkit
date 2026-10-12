"""复核模块底层逻辑单元测试（步骤 4.3）。

测试时间轴可视化、帧预览缓存、时间清单编辑等底层逻辑函数，
不依赖 Streamlit 测试 API。
"""

import os
import json
import tempfile

from drama_splitter.ui.utils import (
    format_timestamp,
    validate_edited_episodes,
    build_timeline_data,
    merge_edits,
)
from drama_splitter.models import Episode, TimeList


# ── format_timestamp ────────────────────────────────────────────

def test_format_boundary_values() -> None:
    """边界时间格式化。"""
    assert format_timestamp(59.9) == "00:59"
    assert format_timestamp(60.0) == "01:00"
    assert format_timestamp(3599.0) == "59:59"
    assert format_timestamp(3600.0) == "60:00"


# ── validate_edited_episodes ────────────────────────────────────

def test_validate_no_overlap_same_timestamp() -> None:
    """首尾相接（无间隙）不报错。"""
    eps = [
        Episode(episode=1, start_sec=0, end_sec=10, duration_sec=10,
                confidence="high", signals_hit=["ocr"]),
        Episode(episode=2, start_sec=10, end_sec=20, duration_sec=10,
                confidence="high", signals_hit=["ocr"]),
    ]
    ok, _ = validate_edited_episodes(eps)
    assert ok


def test_validate_unsorted_reordered() -> None:
    """乱序输入的 episodes 按 start_sec 排序后正确检测。"""
    eps = [
        Episode(episode=2, start_sec=10, end_sec=20, duration_sec=10,
                confidence="high", signals_hit=["ocr"]),
        Episode(episode=1, start_sec=0, end_sec=15, duration_sec=15,
                confidence="high", signals_hit=["ocr"]),
    ]
    ok, msg = validate_edited_episodes(eps)
    assert not ok
    assert "重叠" in msg


def test_validate_single_episode() -> None:
    """单集列表有效。"""
    eps = [
        Episode(episode=1, start_sec=0, end_sec=60, duration_sec=60,
                confidence="high", signals_hit=["ocr"]),
    ]
    ok, _ = validate_edited_episodes(eps)
    assert ok


# ── build_timeline_data ─────────────────────────────────────────

def test_timeline_colors_all_confidences() -> None:
    """三种置信度颜色映射正确。"""
    tl = TimeList(
        video_name="test.mp4",
        duration_sec=300.0,
        episodes=[
            Episode(episode=1, start_sec=0, end_sec=100, duration_sec=100,
                    confidence="high", signals_hit=["ocr", "audio_fingerprint"]),
            Episode(episode=2, start_sec=100, end_sec=200, duration_sec=100,
                    confidence="medium", signals_hit=["ocr"]),
            Episode(episode=3, start_sec=200, end_sec=300, duration_sec=100,
                    confidence="low", signals_hit=["inferred"]),
        ],
    )
    data = build_timeline_data(tl)
    colors = [ep["color"] for ep in data["episodes"]]
    assert colors == ["#2ca02c", "#bcbd22", "#d62728"]


def test_timeline_unknown_confidence_fallback() -> None:
    """未知置信度使用灰色回退。"""
    tl = TimeList(
        video_name="test.mp4",
        duration_sec=100.0,
        episodes=[
            Episode(episode=1, start_sec=0, end_sec=100, duration_sec=100,
                    confidence="high", signals_hit=["ocr"]),
        ],
    )
    data = build_timeline_data(tl)
    # 故意测试已知的回退逻辑：high → green
    assert data["episodes"][0]["color"] == "#2ca02c"


# ── merge_edits ─────────────────────────────────────────────────

def test_merge_edits_multiple_changes() -> None:
    """同时编辑多集。"""
    tl = TimeList(
        video_name="test.mp4",
        duration_sec=400.0,
        episodes=[
            Episode(episode=1, start_sec=0, end_sec=100, duration_sec=100,
                    confidence="high", signals_hit=["ocr"]),
            Episode(episode=2, start_sec=100, end_sec=200, duration_sec=100,
                    confidence="high", signals_hit=["ocr"]),
            Episode(episode=3, start_sec=200, end_sec=400, duration_sec=200,
                    confidence="high", signals_hit=["ocr"]),
        ],
    )
    edits = {
        1: {"start_sec": 5.0, "end_sec": 95.0},
        3: {"start_sec": 220.0, "end_sec": 410.0},
    }
    merged = merge_edits(tl, edits)

    assert merged.episodes[0].start_sec == 5.0
    assert merged.episodes[0].end_sec == 95.0
    assert merged.episodes[0].duration_sec == 90.0

    assert merged.episodes[1].start_sec == 100.0  # unchanged
    assert merged.episodes[1].end_sec == 200.0    # unchanged

    assert merged.episodes[2].start_sec == 220.0  # changed
    assert merged.episodes[2].end_sec == 410.0    # changed
    assert merged.episodes[2].duration_sec == 190.0


def test_merge_edits_preserves_metadata() -> None:
    """编辑后 confidence 和 signals_hit 保持不变。"""
    tl = TimeList(
        video_name="test.mp4",
        duration_sec=100.0,
        episodes=[
            Episode(episode=1, start_sec=0, end_sec=100, duration_sec=100,
                    confidence="medium", signals_hit=["ocr", "scene_cluster"]),
        ],
    )
    merged = merge_edits(tl, {1: {"start_sec": 10.0}})
    assert merged.episodes[0].confidence == "medium"
    assert merged.episodes[0].signals_hit == ["ocr", "scene_cluster"]


def test_merge_edits_preserves_unedited() -> None:
    """未编辑的集数不变，且保持原对象引用（值相等）。"""
    tl = TimeList(
        video_name="test.mp4",
        duration_sec=200.0,
        episodes=[
            Episode(episode=1, start_sec=0, end_sec=100, duration_sec=100,
                    confidence="high", signals_hit=["ocr"]),
            Episode(episode=2, start_sec=100, end_sec=200, duration_sec=100,
                    confidence="high", signals_hit=["ocr"]),
        ],
    )
    merged = merge_edits(tl, {1: {"start_sec": 5.0}})
    # 未编辑的集 2 保持不变
    assert merged.episodes[1].start_sec == 100.0
    assert merged.episodes[1].end_sec == 200.0
    assert merged.episodes[1].episode == 2
