"""
短剧总集篇自动分集工具 - 启动入口

用法: python main.py
"""

import sys
import os
import webbrowser
import threading
import logging

# 将项目根目录加入 sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from utils.logger import setup_logging
from utils.ffmpeg_check import check_ffmpeg
from config import AppConfig

logger = logging.getLogger(__name__)


def check_environment() -> bool:
    """检查运行环境。

    Returns:
        True 表示环境 OK，False 表示有问题
    """
    # 1. Python 版本检查
    py_version = sys.version_info
    if py_version < (3, 10):
        print(f"❌ Python 版本过低: {py_version.major}.{py_version.minor}，需要 3.10+")
        print("   请升级 Python: https://www.python.org/downloads/")
        return False

    logger.info(f"Python 版本: {py_version.major}.{py_version.minor}.{py_version.micro}")

    # 2. ffmpeg 检查
    ok, msg = check_ffmpeg()
    if not ok:
        print(f"❌ ffmpeg 未找到或不可用:")
        print(f"   {msg}")
        print("   请安装 ffmpeg 并添加到系统 PATH")
        print("   下载地址: https://ffmpeg.org/download.html")
        print("   Windows 安装教程: https://www.wikihow.com/Install-FFmpeg-on-Windows")
        return False

    logger.info(f"ffmpeg: {msg}")
    print(f"✅ {msg}")
    print(f"✅ Python {py_version.major}.{py_version.minor}.{py_version.micro}")

    return True


def open_browser(url: str, delay: float = 1.5):
    """延迟打开浏览器。

    Args:
        url: 要打开的 URL
        delay: 延迟秒数
    """
    import time
    time.sleep(delay)
    webbrowser.open(url)


def main():
    """主入口函数"""
    # 1. 初始化日志系统
    setup_logging(log_dir="logs")
    logger.info("=" * 60)
    logger.info("短剧总集篇自动分集工具 启动")
    logger.info("=" * 60)

    # 2. 检查环境
    print("\n🔍 检查运行环境...")
    if not check_environment():
        print("\n环境检查未通过，请修复上述问题后重新运行。")
        sys.exit(1)

    # 3. 加载配置
    config = AppConfig.from_env()
    logger.info(f"配置加载完成: port={config.server_port}")

    # 4. 创建必要目录
    for d in [config.upload_dir, config.output_dir, config.log_dir]:
        os.makedirs(d, exist_ok=True)

    # 5. 启动 Gradio 应用
    from web.app import DramaSplitterApp

    app = DramaSplitterApp(config=config)

    # 6. 自动打开浏览器
    url = f"http://127.0.0.1:{config.server_port}"
    threading.Thread(target=open_browser, args=(url,), daemon=True).start()

    print(f"\n{'=' * 50}")
    print(f"🎬 短剧总集篇自动分集工具")
    print(f"📱 浏览器访问: {url}")
    print(f"{'=' * 50}")

    app.launch(
        server_name=config.server_name,
        server_port=config.server_port,
        max_file_size=config.max_file_size,
        share=False
    )


if __name__ == '__main__':
    main()
