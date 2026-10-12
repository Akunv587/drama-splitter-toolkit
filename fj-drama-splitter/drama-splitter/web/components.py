"""Gradio 界面可复用 UI 组件"""

import gradio as gr


def create_status_indicator(status: str) -> str:
    """创建状态指示 HTML。

    Args:
        status: 状态文字

    Returns:
        HTML 字符串
    """
    color_map = {
        "pending": "gray",
        "analyzing": "blue",
        "awaiting_confirm": "orange",
        "cutting": "purple",
        "completed": "green",
        "failed": "red",
    }
    color = color_map.get(status, "gray")
    return f'<span style="color:{color};font-weight:bold">● {status}</span>'


def create_confidence_badge(confidence: str) -> str:
    """创建置信度标记 HTML。

    Args:
        confidence: 置信度 ('HIGH' 或 'LOW')

    Returns:
        HTML 字符串
    """
    if confidence == "HIGH":
        return "🟢 HIGH"
    return "🔴 LOW"


def create_conflict_badge(has_conflict: bool) -> str:
    """创建冲突标记 HTML。

    Args:
        has_conflict: 是否有冲突

    Returns:
        HTML 字符串
    """
    if has_conflict:
        return "❌ 有冲突"
    return "✓ 无冲突"
