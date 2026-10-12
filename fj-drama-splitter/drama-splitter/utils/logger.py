"""统一日志配置

提供 setup_logging() 函数初始化日志系统：
- 控制台输出 INFO 及以上
- 文件输出 DEBUG 及以上，按天轮转，保留 7 天
"""

import logging
import os
from logging.handlers import TimedRotatingFileHandler


def setup_logging(log_dir: str = "logs", level: int = logging.DEBUG) -> None:
    """初始化统一日志配置。

    Args:
        log_dir: 日志文件目录
        level: 根 logger 的日志级别，默认 DEBUG
    """
    os.makedirs(log_dir, exist_ok=True)

    formatter = logging.Formatter(
        '%(asctime)s [%(name)s] %(levelname)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    # 控制台 handler（INFO 及以上）
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)

    # 文件 handler（DEBUG 及以上，按天轮转，保留 7 天）
    file_handler = TimedRotatingFileHandler(
        os.path.join(log_dir, "drama_splitter.log"),
        when='midnight',
        backupCount=7,
        encoding='utf-8',
        delay=True,         # 延迟打开文件，避免启动时文件锁定冲突
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)

    # 包装 doRollover 以忽略轮转失败（避免 PermissionError 导致日志中断）
    _original_do_rollover = file_handler.doRollover
    def _safe_do_rollover():
        try:
            _original_do_rollover()
        except PermissionError:
            pass  # 文件被其他进程占用时静默跳过
    file_handler.doRollover = _safe_do_rollover

    # 配置根 logger
    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)
