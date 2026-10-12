"""UI 底层工具函数（不依赖 Streamlit，可直接单元测试）。"""

from typing import Any, Dict, List, Optional, Tuple

from drama_splitter.exceptions import ConfigValidationError
from drama_splitter.models import Episode, TimeList
from drama_splitter.models.config import AppConfig


def validate_video_path(path: str) -> Tuple[bool, str, str]:
    """校验视频文件路径。

    Args:
        path: 文件路径（可能带有引号或前后空白）。

    Returns:
        (是否有效, 错误信息, 清洗后路径) 元组。
    """
    import os

    if not path or not path.strip():
        return False, "请输入视频文件路径", ""
    path = path.strip().strip('"').strip("'")
    if not os.path.exists(path):
        return False, f"文件不存在: {path}", ""
    if not os.path.isfile(path):
        return False, f"路径不是文件: {path}", ""
    return True, "", path


def validate_naming_template(template: str) -> Tuple[bool, str]:
    """校验文件命名模板。

    Args:
        template: 命名模板字符串。

    Returns:
        (是否有效, 错误信息) 元组。
    """
    import re

    if not template:
        return False, "命名模板不能为空"
    if not re.search(r"\{ep(?::\d+d)?\}", template):
        return False, "命名模板必须包含 {ep} 或 {ep:02d} 占位符"
    return True, ""


def build_config_from_ui(params: Dict[str, Any]) -> AppConfig:
    """从 UI 参数构建 AppConfig。

    Args:
        params: UI 参数字典，可能包含:
            ocr_engine, frame_interval_sec, cut_mode,
            naming_template, fingerprint_threshold,
            min_episode_sec, max_episode_sec, ...

    Returns:
        构建并校验后的 AppConfig。

    Raises:
        ConfigValidationError: 参数不合法。
    """
    # 过滤 None 值
    clean = {k: v for k, v in params.items() if v is not None}
    config = AppConfig.model_validate(clean)
    config.validate()
    return config


def format_timestamp(sec: float) -> str:
    """格式化秒数为 MM:SS。

    Args:
        sec: 秒数。

    Returns:
        格式化字符串，如 "02:35"。
    """
    minutes = int(sec // 60)
    seconds = int(sec % 60)
    return f"{minutes:02d}:{seconds:02d}"


def validate_edited_episodes(
    episodes: List[Episode],
) -> Tuple[bool, str]:
    """检测用户修改后的集清单是否有时间冲突。

    Args:
        episodes: 修改后的 Episode 列表。

    Returns:
        (是否有效, 错误信息) 元组。
    """
    if not episodes:
        return False, "集数列表为空"

    # 按 start_sec 排序
    sorted_eps = sorted(episodes, key=lambda e: e.start_sec)

    for i in range(len(sorted_eps) - 1):
        curr = sorted_eps[i]
        nxt = sorted_eps[i + 1]
        if curr.end_sec > nxt.start_sec + 0.01:
            return False, (
                f"第 {curr.episode} 集 (结束 {format_timestamp(curr.end_sec)}) "
                f"与第 {nxt.episode} 集 (开始 {format_timestamp(nxt.start_sec)}) 重叠"
            )

    return True, ""


def build_timeline_data(
    time_list: TimeList,
) -> Dict[str, Any]:
    """构建时间轴可视化所需的数据结构。

    Args:
        time_list: 时间清单。

    Returns:
        含 episodes 列表和颜色映射的字典。
    """
    color_map = {"high": "#2ca02c", "medium": "#bcbd22", "low": "#d62728"}
    episodes_data = []
    for ep in time_list.episodes:
        episodes_data.append({
            "episode": ep.episode,
            "start_sec": ep.start_sec,
            "end_sec": ep.end_sec,
            "duration_sec": ep.duration_sec,
            "confidence": ep.confidence,
            "signals_hit": ep.signals_hit,
            "color": color_map.get(ep.confidence, "#7f7f7f"),
        })
    return {
        "total_duration_sec": time_list.duration_sec,
        "episodes": episodes_data,
    }


def merge_edits(
    original: TimeList,
    edits: Dict[int, Dict[str, float]],
) -> TimeList:
    """合并用户对时间清单的修改。

    Args:
        original: 原始时间清单。
        edits: 修改字典，key 为 episode 编号，
               value 为 {"start_sec": float, "end_sec": float} 或部分键。

    Returns:
        合并后的新 TimeList。
    """
    episodes = []
    for ep in original.episodes:
        if ep.episode in edits:
            edit = edits[ep.episode]
            new_start = edit.get("start_sec", ep.start_sec)
            new_end = edit.get("end_sec", ep.end_sec)
            episode = Episode(
                episode=ep.episode,
                start_sec=new_start,
                end_sec=new_end,
                duration_sec=new_end - new_start,
                confidence=ep.confidence,
                signals_hit=ep.signals_hit,
            )
        else:
            episode = ep
        episodes.append(episode)

    return TimeList(
        video_name=original.video_name,
        duration_sec=original.duration_sec,
        episodes=episodes,
    )
