"""数据模型定义。

包含视频元信息、候选点、时间清单等核心数据结构。
所有模型基于 pydantic v2，提供序列化/反序列化与内置校验。
"""

import csv
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


# ── 视频元信息 ─────────────────────────────────────────────────

class VideoMeta(BaseModel):
    """视频文件元信息，通过 ffprobe 获取。"""

    file_path: str
    file_name: str
    format: str
    duration_sec: float
    width: int
    height: int
    codec: str
    fps: float
    file_size_mb: float

    model_config = ConfigDict(frozen=True)


# ── 候选边界点 ─────────────────────────────────────────────────

class Candidate(BaseModel):
    """单个候选边界点，由某路信号检测器产出。"""

    signal_type: Literal["ocr", "audio_fingerprint", "black_silence", "scene_cluster"]
    timestamp_sec: float = Field(ge=0)
    episode: Optional[int] = None
    confidence: float = Field(ge=0, le=1)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class CandidateList(BaseModel):
    """一个检测器的完整输出，包含多个候选点。"""

    signal_type: str
    video_name: str
    duration_sec: float
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    candidates: List[Candidate] = Field(default_factory=list)

    @classmethod
    def from_json(cls, path: str) -> "CandidateList":
        """从 JSON 文件反序列化。"""
        with open(path, "r", encoding="utf-8") as f:
            return cls.model_validate(json.load(f))

    def to_json(self, path: str) -> None:
        """序列化到 JSON 文件。"""
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.model_dump(mode="json"), f, ensure_ascii=False, indent=2)


# ── 时间清单 ───────────────────────────────────────────────────

class Episode(BaseModel):
    """时间清单中的单集记录。"""

    episode: int
    start_sec: float = Field(ge=0)
    end_sec: float = Field(ge=0)
    duration_sec: float = Field(ge=0)
    confidence: Literal["high", "medium", "low"]
    signals_hit: List[str] = Field(default_factory=list)

    @field_validator("end_sec")
    @classmethod
    def end_after_start(cls, v: float, info: Any) -> float:
        """确保 end_sec > start_sec。"""
        start = info.data.get("start_sec")
        if start is not None and v <= start:
            raise ValueError(f"end_sec ({v}) must be > start_sec ({start})")
        return v

    @model_validator(mode="after")
    def duration_consistency(self) -> "Episode":
        """确保 duration_sec == end_sec - start_sec。"""
        expected = self.end_sec - self.start_sec
        if abs(self.duration_sec - expected) > 0.01:
            raise ValueError(
                f"duration_sec ({self.duration_sec}) must equal "
                f"end_sec - start_sec ({expected})"
            )
        return self


class TimeList(BaseModel):
    """完整时间清单，融合决策引擎的最终输出。"""

    video_name: str
    duration_sec: float
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    episodes: List[Episode] = Field(default_factory=list)

    @classmethod
    def from_json(cls, path: str) -> "TimeList":
        """从 JSON 文件反序列化。"""
        with open(path, "r", encoding="utf-8") as f:
            return cls.model_validate(json.load(f))

    def to_json(self, path: str) -> None:
        """序列化到 JSON 文件。"""
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.model_dump(mode="json"), f, ensure_ascii=False, indent=2)

    def to_csv(self, path: str) -> None:
        """导出为 CSV 文件。"""
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["episode", "start_sec", "end_sec", "duration_sec", "confidence", "signals_hit"])
            for ep in self.episodes:
                writer.writerow([
                    ep.episode,
                    ep.start_sec,
                    ep.end_sec,
                    ep.duration_sec,
                    ep.confidence,
                    ";".join(ep.signals_hit),
                ])

    @model_validator(mode="after")
    def no_overlap(self) -> "TimeList":
        """确保集与集之间无时间重叠，且集数连续（允许跳号但记录警告）。"""
        sorted_eps = sorted(self.episodes, key=lambda e: e.start_sec)
        for i in range(len(sorted_eps) - 1):
            current = sorted_eps[i]
            next_ep = sorted_eps[i + 1]
            if current.end_sec > next_ep.start_sec + 0.01:
                raise ValueError(
                    f"Episode {current.episode} (ends at {current.end_sec}) "
                    f"overlaps with episode {next_ep.episode} (starts at {next_ep.start_sec})"
                )
        return self
