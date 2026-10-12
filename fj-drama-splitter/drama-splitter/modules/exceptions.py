"""自定义异常类层次"""


class DramaSplitterError(Exception):
    """所有自定义异常的基类"""
    pass


class FileNotFoundError(DramaSplitterError):
    """文件不存在（注意：区别于内置 FileNotFoundError）"""
    pass


class FileFormatError(DramaSplitterError):
    """文件格式错误"""
    pass


class FFmpegError(DramaSplitterError):
    """ffmpeg 相关错误"""
    pass


class SubtitleParseError(DramaSplitterError):
    """字幕解析错误"""
    pass


class AnalysisError(DramaSplitterError):
    """分析过程错误"""
    pass


class CutError(DramaSplitterError):
    """裁剪过程错误"""
    pass
