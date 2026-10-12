"""时间格式转换工具

提供各种时间格式的转换、解析和校验功能。
"""

import re
from modules.models import SubtitleItem


def seconds_to_timestamp(seconds: float) -> str:
    """将秒数转为 HH:MM:SS 格式字符串。

    Args:
        seconds: 秒数（浮点数）

    Returns:
        HH:MM:SS 格式字符串，如 142.3 → '00:02:22'

    Examples:
        >>> seconds_to_timestamp(142.3)
        '00:02:22'
        >>> seconds_to_timestamp(3661.5)
        '01:01:01'
    """
    seconds = max(0, int(seconds))
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    return f"{h:02d}:{m:02d}:{s:02d}"


def timestamp_to_seconds(ts: str) -> float:
    """将时间戳字符串转为秒数。

    支持格式：
    - HH:MM:SS
    - MM:SS
    - SRT 格式 HH:MM:SS,mmm（逗号分隔毫秒）
    - VTT 格式 HH:MM:SS.mmm（点号分隔毫秒）

    Args:
        ts: 时间戳字符串

    Returns:
        秒数（浮点数）

    Examples:
        >>> timestamp_to_seconds('00:02:22')
        142.0
        >>> timestamp_to_seconds('02:30')
        150.0
        >>> timestamp_to_seconds('00:02:22,160')
        142.16
        >>> timestamp_to_seconds('00:02:22.160')
        142.16
    """
    ts = ts.strip()

    # 检查是否包含毫秒（逗号或点号）
    ms = 0.0
    if ',' in ts:
        ts, ms_part = ts.split(',', 1)
        ms = float(ms_part) / 1000
    elif '.' in ts and ts.count(':') >= 2:
        # VTT 格式：HH:MM:SS.mmm
        parts = ts.split(':')
        if '.' in parts[-1]:
            sec_part, ms_part = parts[-1].split('.', 1)
            ts = f"{parts[0]}:{parts[1]}:{sec_part}"
            ms = float(ms_part) / 1000

    parts = ts.split(':')
    if len(parts) == 3:
        h, m, s = int(parts[0]), int(parts[1]), float(parts[2])
    elif len(parts) == 2:
        h, m, s = 0, int(parts[0]), float(parts[1])
    else:
        raise ValueError(f"无法解析时间格式: {ts}")

    return h * 3600 + m * 60 + s + ms


def vtt_timestamp_to_seconds(ts: str) -> float:
    """将 VTT 时间戳 '00:01:23.456' 转为秒数。

    注意 VTT 用点号分隔毫秒，SRT 用逗号。

    Args:
        ts: VTT 格式时间戳，如 '00:01:23.456'

    Returns:
        秒数（浮点数）

    Examples:
        >>> vtt_timestamp_to_seconds('00:01:23.456')
        83.456
    """
    ts = ts.strip()
    parts = ts.split(':')
    if len(parts) != 3:
        raise ValueError(f"VTT 时间戳应为 HH:MM:SS.mmm 格式: {ts}")

    h = int(parts[0])
    m = int(parts[1])
    s = float(parts[2])  # parts[2] 如 "23.456"
    return h * 3600 + m * 60 + s


def parse_manual_timestamps(text: str) -> list[float]:
    """解析用户粘贴的起始时间列表。

    输入格式：'00:00:00, 00:02:30, 00:05:00'
    支持 HH:MM:SS 和 MM:SS 两种格式。

    Args:
        text: 逗号分隔的时间戳字符串

    Returns:
        秒数列表

    Examples:
        >>> parse_manual_timestamps('00:00:00, 00:02:30, 00:05:00')
        [0.0, 150.0, 300.0]
    """
    parts = [p.strip() for p in text.split(',') if p.strip()]
    timestamps: list[float] = []
    for part in parts:
        segments = part.split(':')
        if len(segments) == 3:
            h, m, s = segments
            total = int(h) * 3600 + int(m) * 60 + float(s)
        elif len(segments) == 2:
            m, s = segments
            total = int(m) * 60 + float(s)
        else:
            raise ValueError(f"无法解析时间格式: {part}")
        timestamps.append(total)
    return timestamps


def parse_episode_end_format(text: str) -> list[tuple[int, float]]:
    """解析"第N集结束：HH:MM:SS.mmm"格式的分集结束时间。

    从用户粘贴的文本中提取每集结束时间，返回 (集号, 结束秒数) 列表。

    Args:
        text: 包含"第N集结束：时间戳"的文本

    Returns:
        [(集号, 结束时间秒数), ...]，按集号升序排列

    Examples:
        >>> parse_episode_end_format('第1集结束：00:02:03.270\\n第2集结束：00:04:01.589')
        [(1, 123.27), (2, 241.589)]
    """
    pattern = re.compile(
        r'第\s*(\d+)\s*集\s*结\s*束\s*[：:]\s*\n?\s*(\d{1,2}:\d{2}:\d{2}\.?\d*)'
    )
    matches = pattern.findall(text)
    if not matches:
        raise ValueError(
            "未识别到分集结束时间。请使用格式：\n"
            "第1集结束：00:02:03.270\n"
            "第2集结束：00:04:01.589"
        )

    result: list[tuple[int, float]] = []
    for ep_num_str, time_str in matches:
        ep_num = int(ep_num_str)
        total = timestamp_to_seconds(time_str)
        result.append((ep_num, total))

    result.sort(key=lambda x: x[0])
    return result


def format_duration(seconds: float) -> str:
    """将秒数格式化为可读时长。

    Args:
        seconds: 秒数

    Returns:
        格式化时长，如 '2\'22"'

    Examples:
        >>> format_duration(142.3)
        '2\\'22"'
    """
    seconds = max(0, int(seconds))
    m = seconds // 60
    s = seconds % 60
    return f"{m}'{s:02d}\""


def check_subtitle_conflict(cut_ts: float, subs: list[SubtitleItem]) -> bool:
    """检查切割点是否落在某条字幕的 [start, end] 区间内（台词冲突）。

    Args:
        cut_ts: 切割点时间戳（秒）
        subs: 字幕条目列表，每个元素有 .start 和 .end 属性

    Returns:
        True 表示切割点落在台词中间（有冲突），False 表示无冲突
    """
    if not subs:
        return False
    for sub in subs:
        if sub.start < cut_ts < sub.end:
            return True
    return False


def find_nearest_silence_gap(
    cut_ts: float,
    gaps: list[tuple[float, float]],
    tolerance: float = 3.0
) -> tuple[float, bool]:
    """在 cut_ts 附近 ±tolerance 范围内找到最近的字幕静默区间中点。

    Args:
        cut_ts: 切割点时间戳（秒）
        gaps: 静默区间列表 [(gap_start, gap_end), ...]
        tolerance: 搜索容差范围（秒），默认 3.0

    Returns:
        (adjusted_ts, is_adjusted)
        - adjusted_ts: 调整后的时间戳
        - is_adjusted: 是否实际做了调整
    """
    if not gaps:
        return cut_ts, False

    best_gap = None
    best_distance = float('inf')

    for gs, ge in gaps:
        mid = (gs + ge) / 2
        distance = abs(cut_ts - mid)
        if distance <= tolerance and distance < best_distance:
            best_distance = distance
            best_gap = mid

    if best_gap is not None:
        return best_gap, True
    return cut_ts, False
