"""统一异常体系。

所有自定义异常继承自 ``DramaSplitterError``，按层级组织：
环境依赖 → 检测过程 → 融合决策 → 输出裁剪 → 配置校验。
"""


class DramaSplitterError(Exception):
    """所有自定义异常的基类。"""


# ── 环境依赖异常 ──────────────────────────────────────────────

class EnvironmentError(DramaSplitterError):
    """环境依赖缺失。"""


class FFmpegNotFoundError(EnvironmentError):
    """FFmpeg 未安装或不在 PATH 中。"""


class ChromaprintNotFoundError(EnvironmentError):
    """Chromaprint fpcalc 未安装或不在 PATH 中。"""


class FFprobeError(EnvironmentError):
    """ffprobe 读取视频信息失败。"""


# ── 检测过程异常 ──────────────────────────────────────────────

class DetectionError(DramaSplitterError):
    """检测过程异常基类。"""

    def __init__(self, message: str = "", *, signal_type: str = "") -> None:
        super().__init__(message)
        self.signal_type = signal_type


class AllDetectorsFailedError(DetectionError):
    """四路检测器全部失败。"""


class FrameExtractionError(DetectionError):
    """视频抽帧失败。"""


class InsufficientCandidatesError(DetectionError):
    """候选点数量不足以生成时间清单。"""


# ── 融合决策异常 ──────────────────────────────────────────────

class FusionError(DramaSplitterError):
    """融合决策异常基类。"""


class NoBoundariesError(FusionError):
    """未能检测到任何集边界。"""


# ── 输出裁剪异常 ──────────────────────────────────────────────

class OutputError(DramaSplitterError):
    """输出裁剪异常基类。"""


class FFmpegCutError(OutputError):
    """FFmpeg 裁剪单集失败。"""


class InsufficientDiskSpaceError(OutputError):
    """磁盘空间不足，无法完成裁剪。"""


# ── 配置异常 ──────────────────────────────────────────────────

class ConfigError(DramaSplitterError):
    """配置异常基类。"""


class ConfigValidationError(ConfigError):
    """配置参数校验不通过。"""
