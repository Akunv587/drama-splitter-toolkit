"""数据模型与配置体系单元测试。"""

import tempfile
from pathlib import Path

import pytest
from pydantic import ValidationError

from drama_splitter.exceptions import ConfigValidationError
from drama_splitter.models import Candidate, CandidateList, Episode, TimeList, VideoMeta
from drama_splitter.models.config import AppConfig


# ── VideoMeta ──────────────────────────────────────────────────

def test_video_meta_valid() -> None:
    meta = VideoMeta(
        file_path="/path/to/video.mp4",
        file_name="video.mp4",
        format="mp4",
        duration_sec=120.5,
        width=1920,
        height=1080,
        codec="h264",
        fps=30.0,
        file_size_mb=150.0,
    )
    assert meta.duration_sec == 120.5
    assert meta.width == 1920


# ── Candidate ──────────────────────────────────────────────────

def test_candidate_valid() -> None:
    c = Candidate(
        signal_type="ocr",
        timestamp_sec=10.0,
        episode=1,
        confidence=0.9,
    )
    assert c.signal_type == "ocr"
    assert c.episode == 1


def test_candidate_confidence_out_of_range() -> None:
    with pytest.raises(ValidationError):
        Candidate(signal_type="ocr", timestamp_sec=0.0, confidence=1.5)

    with pytest.raises(ValidationError):
        Candidate(signal_type="ocr", timestamp_sec=0.0, confidence=-0.1)


def test_candidate_negative_timestamp() -> None:
    with pytest.raises(ValidationError):
        Candidate(signal_type="ocr", timestamp_sec=-1.0, confidence=0.5)


# ── CandidateList JSON 往返 ────────────────────────────────────

def test_candidate_list_json_roundtrip() -> None:
    cl = CandidateList(
        signal_type="ocr",
        video_name="test.mp4",
        duration_sec=3600.0,
        candidates=[
            Candidate(signal_type="ocr", timestamp_sec=0.0, episode=1, confidence=0.9),
            Candidate(signal_type="ocr", timestamp_sec=180.0, episode=2, confidence=0.8),
        ],
    )
    with tempfile.NamedTemporaryFile(suffix=".json", mode="w", delete=False, encoding="utf-8") as f:
        path = f.name
    try:
        cl.to_json(path)
        loaded = CandidateList.from_json(path)
        assert loaded.signal_type == "ocr"
        assert len(loaded.candidates) == 2
        assert loaded.candidates[0].episode == 1
    finally:
        Path(path).unlink(missing_ok=True)


# ── Episode ────────────────────────────────────────────────────

def test_episode_valid() -> None:
    ep = Episode(
        episode=1,
        start_sec=0.0,
        end_sec=180.0,
        duration_sec=180.0,
        confidence="high",
        signals_hit=["ocr", "audio_fingerprint"],
    )
    assert ep.episode == 1
    assert ep.duration_sec == 180.0


def test_episode_end_before_start() -> None:
    with pytest.raises(ValidationError, match="end_sec"):
        Episode(
            episode=1,
            start_sec=100.0,
            end_sec=50.0,
            duration_sec=50.0,
            confidence="high",
        )


def test_episode_duration_mismatch() -> None:
    with pytest.raises(ValidationError, match="duration_sec"):
        Episode(
            episode=1,
            start_sec=0.0,
            end_sec=180.0,
            duration_sec=999.0,  # 不匹配
            confidence="high",
        )


# ── TimeList JSON 往返 ─────────────────────────────────────────

def test_time_list_json_roundtrip() -> None:
    tl = TimeList(
        video_name="test.mp4",
        duration_sec=540.0,
        episodes=[
            Episode(episode=1, start_sec=0.0, end_sec=180.0, duration_sec=180.0, confidence="high", signals_hit=["ocr"]),
            Episode(episode=2, start_sec=180.0, end_sec=360.0, duration_sec=180.0, confidence="medium", signals_hit=["ocr"]),
            Episode(episode=3, start_sec=360.0, end_sec=540.0, duration_sec=180.0, confidence="high", signals_hit=["ocr", "audio_fingerprint"]),
        ],
    )
    with tempfile.NamedTemporaryFile(suffix=".json", mode="w", delete=False, encoding="utf-8") as f:
        path = f.name
    try:
        tl.to_json(path)
        loaded = TimeList.from_json(path)
        assert len(loaded.episodes) == 3
        assert loaded.episodes[0].confidence == "high"
    finally:
        Path(path).unlink(missing_ok=True)


def test_time_list_csv_roundtrip() -> None:
    tl = TimeList(
        video_name="test.mp4",
        duration_sec=360.0,
        episodes=[
            Episode(episode=1, start_sec=0.0, end_sec=180.0, duration_sec=180.0, confidence="high", signals_hit=["ocr"]),
            Episode(episode=2, start_sec=180.0, end_sec=360.0, duration_sec=180.0, confidence="medium", signals_hit=["ocr"]),
        ],
    )
    with tempfile.NamedTemporaryFile(suffix=".csv", mode="w", delete=False, encoding="utf-8") as f:
        path = f.name
    try:
        tl.to_csv(path)
        content = Path(path).read_text()
        assert "episode" in content
        assert "1,0.0,180.0" in content
    finally:
        Path(path).unlink(missing_ok=True)


def test_time_list_overlap_detection() -> None:
    with pytest.raises(ValidationError, match="overlap"):
        TimeList(
            video_name="test.mp4",
            duration_sec=540.0,
            episodes=[
                Episode(episode=1, start_sec=0.0, end_sec=200.0, duration_sec=200.0, confidence="high"),
                Episode(episode=2, start_sec=180.0, end_sec=360.0, duration_sec=180.0, confidence="medium"),
            ],
        )


# ── AppConfig ──────────────────────────────────────────────────

def test_app_config_from_yaml() -> None:
    """测试从 config.yaml 正确加载配置。"""
    config = AppConfig.from_yaml("config.yaml")
    assert config.ocr_engine == "rapidocr"
    assert config.frame_interval_sec == 0.5
    assert config.cut_mode == "lossless"
    # {ep:02d} 中包含 ep 占位符，用正则验证
    import re
    assert re.search(r"\{ep(?::\d+d)?\}", config.naming_template)


def test_app_config_defaults() -> None:
    config = AppConfig()
    assert config.ocr_engine == "rapidocr"
    assert config.frame_interval_sec == 0.5
    assert config.cut_mode == "lossless"


def test_app_config_validate_invalid_ocr_engine() -> None:
    """pydantic 在构造时即校验 Literal 类型，抛出 ValidationError。"""
    with pytest.raises(ValidationError):
        AppConfig(ocr_engine="invalid_engine")  # type: ignore[arg-type]


def test_app_config_validate_invalid_cut_mode() -> None:
    """pydantic 在构造时即校验 Literal 类型，抛出 ValidationError。"""
    with pytest.raises(ValidationError):
        AppConfig(cut_mode="invalid")  # type: ignore[arg-type]


def test_app_config_validate_min_max_episode() -> None:
    config = AppConfig(min_episode_sec=300, max_episode_sec=60)
    with pytest.raises(ConfigValidationError, match="min_episode_sec"):
        config.validate()


def test_app_config_validate_fingerprint_threshold() -> None:
    """pydantic Field(ge=0, le=1) 在构造时校验，抛出 ValidationError。"""
    with pytest.raises(ValidationError):
        AppConfig(fingerprint_threshold=1.5)

    with pytest.raises(ValidationError):
        AppConfig(fingerprint_threshold=-0.5)


def test_app_config_validate_naming_template_missing_ep() -> None:
    config = AppConfig(naming_template="video.mp4")
    with pytest.raises(ConfigValidationError, match="naming_template"):
        config.validate()


def test_merge_cli_args_none_not_overridden() -> None:
    """None 值不覆盖原有配置。"""
    config = AppConfig(ocr_engine="rapidocr", frame_interval_sec=0.5)
    merged = config.merge_cli_args(ocr_engine=None, frame_interval_sec=1.0)
    assert merged.ocr_engine == "rapidocr"  # 未被覆盖
    assert merged.frame_interval_sec == 1.0  # 被覆盖
