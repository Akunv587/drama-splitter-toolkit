"""核心数据模型定义

使用 @dataclass 定义所有数据模型，枚举类型用于常量管理。
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class ConfidenceLevel(str, Enum):
    """置信度等级：只有 HIGH 和 LOW，不存在 MEDIUM"""
    HIGH = "HIGH"
    LOW = "LOW"


class TaskStatusEnum(str, Enum):
    """任务状态"""
    PENDING = "pending"
    ANALYZING = "analyzing"
    AWAITING_CONFIRM = "awaiting_confirm"
    CUTTING = "cutting"
    COMPLETED = "completed"
    FAILED = "failed"


class SignalSource(str, Enum):
    """信号源类型"""
    FLASH = "flash"
    SILENCE = "silence"
    SUBTITLE = "subtitle"


@dataclass
class SubtitleItem:
    """字幕条目"""
    index: int
    start: float          # 秒
    end: float            # 秒
    text: str


@dataclass
class WordTimestamp:
    """逐词时间戳（VTT 专用）"""
    word: str
    start: float          # 秒


@dataclass
class MergedPoint:
    """交叉验证后的分割点"""
    timestamp: float
    confidence: str       # ConfidenceLevel.HIGH or ConfidenceLevel.LOW
    sources: list[str]    # ['flash', 'silence', 'subtitle']


@dataclass
class EpisodeSegment:
    """分集清单条目"""
    episode_num: int
    start_time: float     # 秒
    end_time: float       # 秒
    confidence: str       # ConfidenceLevel
    has_subtitle_conflict: bool
    conflict_detail: str | None = None


@dataclass
class CutResult:
    """裁剪结果"""
    episode_num: int
    output_path: str
    success: bool
    error: str | None = None


@dataclass
class TaskStatus:
    """任务状态"""
    task_id: str
    status: str           # TaskStatusEnum
    video_path: str | None = None
    audio_path: str | None = None
    subtitle_path: str | None = None
    drama_name: str | None = None
    episodes: list[EpisodeSegment] | None = None
    cut_results: list[CutResult] | None = None
    error_message: str | None = None
    # 进度信息
    progress_percent: int = 0
    progress_message: str = ""
    progress_flash: int = 0       # 闪白检测进度 0-100
    progress_silence: int = 0     # 静音检测进度 0-100
    progress_subtitle: int = 0    # 字幕解析进度 0-100
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
