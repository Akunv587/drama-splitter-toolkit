"""配置模型与加载逻辑。

从 ``config.yaml`` 加载配置，支持 CLI 参数覆盖与参数校验。
"""

from __future__ import annotations

import re
from typing import Any, Dict, Literal, Optional

import yaml
from pydantic import BaseModel, Field, model_validator

from drama_splitter.exceptions import ConfigValidationError

# 文件命名模板中 {ep} 或 {ep:0Nd} 占位符的正则
_EP_PATTERN = re.compile(r"\{ep(?::\d+d)?\}")


class AppConfig(BaseModel):
    """应用全局配置，字段与 ``config.yaml`` 一一对应。"""

    # OCR
    ocr_engine: Literal["rapidocr", "paddleocr"] = "rapidocr"

    # 抽帧
    frame_interval_sec: float = Field(default=0.5, gt=0)
    frame_width: int = Field(default=640, gt=0)

    # 裁剪
    cut_mode: Literal["lossless", "reencode"] = "lossless"
    naming_template: str = "第{ep:02d}集.mp4"

    # 输出
    output_dir: str = "./output"
    cleanup_frames: bool = True

    # 音频指纹
    fingerprint_template_sec: float = Field(default=20.0, gt=0)
    fingerprint_threshold: float = Field(default=0.85, ge=0, le=1)

    # 黑帧静音
    black_duration: float = Field(default=0.3, gt=0)
    black_pix_threshold: float = Field(default=0.10, ge=0, le=1)
    silence_noise: str = "-40dB"
    silence_duration: float = Field(default=0.3, gt=0)

    # 融合
    cluster_window_sec: float = Field(default=3.0, gt=0)
    min_episode_sec: float = Field(default=60.0, gt=0)
    max_episode_sec: float = Field(default=300.0, gt=0)

    @classmethod
    def from_yaml(cls, path: str) -> "AppConfig":
        """从 YAML 文件加载配置并执行校验。"""
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        config = cls.model_validate(data)
        config.validate()
        return config

    def merge_cli_args(self, **kwargs: Any) -> "AppConfig":
        """使用 CLI 参数覆盖配置。

        值为 ``None`` 的参数不覆盖，保留原有值。
        """
        updates = {k: v for k, v in kwargs.items() if v is not None}
        if not updates:
            return self
        merged = self.model_copy(update=updates)
        merged.validate()
        return merged

    def validate(self) -> None:
        """执行配置参数校验，不通过时抛出 ``ConfigValidationError``。"""
        errors: list[str] = []

        if self.frame_interval_sec <= 0:
            errors.append("frame_interval_sec must be > 0")

        if self.min_episode_sec >= self.max_episode_sec:
            errors.append(
                f"min_episode_sec ({self.min_episode_sec}) "
                f"must be < max_episode_sec ({self.max_episode_sec})"
            )

        if not (0 <= self.fingerprint_threshold <= 1):
            errors.append(f"fingerprint_threshold ({self.fingerprint_threshold}) must be in [0, 1]")

        if not (0 <= self.black_pix_threshold <= 1):
            errors.append(f"black_pix_threshold ({self.black_pix_threshold}) must be in [0, 1]")

        if not _EP_PATTERN.search(self.naming_template):
            errors.append(
                f"naming_template ({self.naming_template}) "
                f"must contain '{{ep}}' or '{{ep:02d}}' placeholder"
            )

        if self.ocr_engine not in ("rapidocr", "paddleocr"):
            errors.append(f"ocr_engine ({self.ocr_engine}) must be 'rapidocr' or 'paddleocr'")

        if self.cut_mode not in ("lossless", "reencode"):
            errors.append(f"cut_mode ({self.cut_mode}) must be 'lossless' or 'reencode'")

        if errors:
            raise ConfigValidationError("\n".join(errors))
