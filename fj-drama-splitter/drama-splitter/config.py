"""应用配置管理

使用 @dataclass 封装所有可配置参数，支持从环境变量覆盖默认值。
"""

import os
import inspect
from dataclasses import dataclass


@dataclass
class AppConfig:
    """应用配置（所有可调参数集中管理）"""

    # 闪白检测参数
    white_threshold: int = 220
    spike_threshold: int = 80
    max_flash_duration: float = 1.0
    merge_distance: float = 2.0
    min_episode_duration: float = 30.0
    target_sample_fps: int = 15

    # 音频静音检测参数
    silence_threshold: float = 0.01
    min_silence_duration: float = 0.3
    max_silence_duration: float = 5.0
    frame_length: int = 2048
    hop_length: int = 512

    # 交叉验证参数
    match_window: float = 3.0

    # 裁剪参数
    cut_max_workers: int = max(1, (os.cpu_count() or 2) - 1)

    # 路径配置
    upload_dir: str = "uploads"
    output_dir: str = "outputs"
    log_dir: str = "logs"

    # Gradio 配置
    server_name: str = "0.0.0.0"
    server_port: int = 7860
    max_file_size: int = 2 * 1024 * 1024 * 1024  # 2GB

    @classmethod
    def from_env(cls) -> "AppConfig":
        """从环境变量加载配置（覆盖默认值）。

        环境变量命名规则: DRAMA_{参数名大写}
        例如: DRAMA_WHITE_THRESHOLD=200
        """
        params = {}
        sig = inspect.signature(cls)
        for name, param in sig.parameters.items():
            env_val = os.environ.get(f"DRAMA_{name.upper()}")
            if env_val is not None:
                # 根据默认值类型进行转换
                default = param.default
                if isinstance(default, bool):
                    params[name] = env_val.lower() in ("true", "1", "yes")
                elif isinstance(default, int):
                    params[name] = int(env_val)
                elif isinstance(default, float):
                    params[name] = float(env_val)
                else:
                    params[name] = env_val
        return cls(**params)
