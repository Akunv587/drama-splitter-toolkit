"""日志配置模块。

提供 ``setup_logging`` 函数，同时输出到终端（rich 格式）和文件（JSON 格式）。
"""

import logging
import os
from logging.handlers import RotatingFileHandler
from typing import List


def setup_logging(log_dir: str, verbose: bool = False) -> None:
    """配置 root logger。

    同时输出到：
    - 终端：使用 ``rich.logging.RichHandler``（彩色格式化）
    - 文件：``{log_dir}/run.log``，JSON 格式，每行一条记录

    Args:
        log_dir: 日志文件输出目录。
        verbose: 为 ``True`` 时日志级别设为 DEBUG，否则为 INFO。
    """
    root = logging.getLogger()
    level = logging.DEBUG if verbose else logging.INFO
    root.setLevel(level)

    # 避免重复添加 handler
    if _has_handler(root.handlers, "rich") and _has_handler(root.handlers, "file"):
        return

    # 终端 handler（rich）
    if not _has_handler(root.handlers, "rich"):
        try:
            from rich.logging import RichHandler

            console_handler = RichHandler(rich_tracebacks=True, markup=True)
            console_handler.setLevel(level)
            console_handler.set_name("rich")
            root.addHandler(console_handler)
        except ImportError:
            # 回退到标准 StreamHandler
            stream_handler = logging.StreamHandler()
            stream_handler.setLevel(level)
            stream_handler.set_name("rich")
            stream_handler.setFormatter(
                logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
            )
            root.addHandler(stream_handler)

    # 文件 handler（JSON 格式）
    if not _has_handler(root.handlers, "file"):
        os.makedirs(log_dir, exist_ok=True)
        log_path = os.path.join(log_dir, "run.log")
        file_handler = RotatingFileHandler(
            log_path, maxBytes=10 * 1024 * 1024, backupCount=3, encoding="utf-8"
        )
        file_handler.setLevel(level)
        file_handler.set_name("file")
        file_handler.setFormatter(_JsonFormatter())
        root.addHandler(file_handler)


def _has_handler(handlers: List[logging.Handler], name: str) -> bool:
    """检查 handler 列表中是否已有指定名称的 handler。"""
    return any(h.get_name() == name for h in handlers)


class _JsonFormatter(logging.Formatter):
    """JSON 行格式化器，每行一条 JSON 记录。"""

    def format(self, record: logging.LogRecord) -> str:
        import json
        from datetime import datetime, timezone

        log_entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "line": record.lineno,
        }
        if record.exc_info and record.exc_info[1]:
            log_entry["exception"] = str(record.exc_info[1])
        return json.dumps(log_entry, ensure_ascii=False)
