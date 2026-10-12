"""UI 工具函数和 Streamlit 应用单元测试。"""

import os
import pytest

from drama_splitter.ui.utils import (
    validate_video_path,
    validate_naming_template,
    build_config_from_ui,
    format_timestamp,
    validate_edited_episodes,
    build_timeline_data,
    merge_edits,
)
from drama_splitter.exceptions import ConfigValidationError
from drama_splitter.models import Episode, TimeList
from drama_splitter.models.config import AppConfig


class TestValidateVideoPath:
    def test_empty_path(self) -> None:
        ok, msg, clean = validate_video_path("")
        assert not ok
        assert "路径" in msg
        assert clean == ""

    def test_nonexistent_path(self) -> None:
        ok, msg, clean = validate_video_path("/nonexistent/video.mp4")
        assert not ok
        assert clean == ""

    def test_valid_path(self, tmp_path) -> None:
        p = tmp_path / "test.mp4"
        p.touch()
        ok, msg, clean = validate_video_path(str(p))
        assert ok
        assert clean == str(p)

    def test_strips_quotes(self, tmp_path) -> None:
        """复制自 Windows 资源管理器的路径带引号应被清洗。"""
        p = tmp_path / "my video.mp4"
        p.touch()
        ok, msg, clean = validate_video_path(f'"{p}"')
        assert ok
        assert clean == str(p)

    def test_strips_whitespace(self, tmp_path) -> None:
        p = tmp_path / "video.mp4"
        p.touch()
        ok, msg, clean = validate_video_path(f'  {p}  ')
        assert ok
        assert clean == str(p)


class TestValidateNamingTemplate:
    def test_empty_template(self) -> None:
        ok, msg = validate_naming_template("")
        assert not ok

    def test_missing_ep_placeholder(self) -> None:
        ok, msg = validate_naming_template("output.mp4")
        assert not ok

    def test_valid_with_ep(self) -> None:
        ok, msg = validate_naming_template("第{ep}集.mp4")
        assert ok

    def test_valid_with_ep_formatted(self) -> None:
        ok, msg = validate_naming_template("E{ep:02d}.mp4")
        assert ok


class TestBuildConfigFromUi:
    def test_valid_params(self) -> None:
        config = build_config_from_ui({
            "ocr_engine": "rapidocr",
            "frame_interval_sec": 0.5,
            "cut_mode": "lossless",
            "naming_template": "E{ep:02d}.mp4",
        })
        assert isinstance(config, AppConfig)
        assert config.ocr_engine == "rapidocr"

    def test_invalid_ocr_engine(self) -> None:
        # Pydantic v2 catches at model_validate time
        from pydantic import ValidationError as PydanticValidationError
        with pytest.raises((ConfigValidationError, PydanticValidationError)):
            build_config_from_ui({"ocr_engine": "invalid"})


class TestFormatTimestamp:
    def test_zero(self) -> None:
        assert format_timestamp(0.0) == "00:00"

    def test_minutes(self) -> None:
        assert format_timestamp(125.0) == "02:05"

    def test_hours(self) -> None:
        assert format_timestamp(3661.0) == "61:01"


class TestValidateEditedEpisodes:
    def test_valid(self) -> None:
        episodes = [
            Episode(episode=1, start_sec=0, end_sec=60, duration_sec=60,
                    confidence="high", signals_hit=["ocr"]),
            Episode(episode=2, start_sec=60, end_sec=120, duration_sec=60,
                    confidence="high", signals_hit=["ocr"]),
        ]
        ok, msg = validate_edited_episodes(episodes)
        assert ok

    def test_overlap(self) -> None:
        episodes = [
            Episode(episode=1, start_sec=0, end_sec=100, duration_sec=100,
                    confidence="high", signals_hit=["ocr"]),
            Episode(episode=2, start_sec=50, end_sec=150, duration_sec=100,
                    confidence="high", signals_hit=["ocr"]),
        ]
        ok, msg = validate_edited_episodes(episodes)
        assert not ok
        assert "重叠" in msg

    def test_empty(self) -> None:
        ok, msg = validate_edited_episodes([])
        assert not ok


class TestBuildTimelineData:
    def test_three_episodes(self) -> None:
        tl = TimeList(
            video_name="test.mp4",
            duration_sec=360.0,
            episodes=[
                Episode(episode=1, start_sec=0, end_sec=120, duration_sec=120,
                        confidence="high", signals_hit=["ocr"]),
                Episode(episode=2, start_sec=120, end_sec=240, duration_sec=120,
                        confidence="medium", signals_hit=["ocr"]),
                Episode(episode=3, start_sec=240, end_sec=360, duration_sec=120,
                        confidence="low", signals_hit=["inferred"]),
            ],
        )
        data = build_timeline_data(tl)
        assert data["total_duration_sec"] == 360.0
        assert len(data["episodes"]) == 3
        assert data["episodes"][0]["color"] == "#2ca02c"  # green
        assert data["episodes"][2]["color"] == "#d62728"   # red


class TestMergeEdits:
    def test_partial_edit(self) -> None:
        tl = TimeList(
            video_name="test.mp4",
            duration_sec=120.0,
            episodes=[
                Episode(episode=1, start_sec=0, end_sec=60, duration_sec=60,
                        confidence="high", signals_hit=["ocr"]),
                Episode(episode=2, start_sec=60, end_sec=120, duration_sec=60,
                        confidence="high", signals_hit=["ocr"]),
            ],
        )
        edits = {1: {"start_sec": 5.0}}
        merged = merge_edits(tl, edits)
        assert merged.episodes[0].start_sec == 5.0
        assert merged.episodes[0].end_sec == 60.0  # unchanged
        assert merged.episodes[1].start_sec == 60.0  # unchanged

    def test_no_edits(self) -> None:
        tl = TimeList(
            video_name="test.mp4",
            duration_sec=60.0,
            episodes=[
                Episode(episode=1, start_sec=0, end_sec=60, duration_sec=60,
                        confidence="high", signals_hit=["ocr"]),
            ],
        )
        merged = merge_edits(tl, {})
        assert merged.episodes[0].start_sec == 0.0
