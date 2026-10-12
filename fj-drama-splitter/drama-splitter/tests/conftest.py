"""pytest 公共配置和 fixture"""

import pytest
import sys
import os

# 将项目根目录加入路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


@pytest.fixture
def sample_srt_content() -> str:
    """SRT 格式示例内容"""
    return """1
00:00:12,160 --> 00:00:18,640
Tonight, the moon goddess herself shall

2
00:00:15,200 --> 00:00:20,400
bear witness. The silver moon bloodline
"""


@pytest.fixture
def sample_vtt_content() -> str:
    """VTT 格式示例内容"""
    return """WEBVTT
Kind: captions
Language: en

00:00:12.160 --> 00:00:15.190 align:start position:0%
Tonight,<00:00:13.360><c> the</c><00:00:13.599><c> moon</c>

00:00:15.190 --> 00:00:15.200 align:start position:0%
Tonight, the moon goddess herself shall
"""


@pytest.fixture
def tmp_dir(tmp_path):
    """临时目录 fixture"""
    return str(tmp_path)
