# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置。

打包为单目录可执行文件 (--onedir)，入口为 CLI 模块。
"""

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_dynamic_libs, collect_data_files

# ── 基本信息 ────────────────────────────────────────────────────

app_name = "drama-splitter"
entry_script = str(Path("src/drama_splitter/cli.py").resolve())

a = Analysis(
    [entry_script],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[
        # RapidOCR
        "rapidocr_onnxruntime",
        # 音频指纹
        "pyacoustid",
        # 场景检测
        "scenedetect.detectors.content_detector",
        # ONNX Runtime
        "onnxruntime.capi._pybind_state",
        # Pydantic v2
        "pydantic.deprecated.decorator",
        # Streamlit (optional, for Web UI)
        "streamlit",
        "matplotlib",
        "matplotlib.backends.backend_agg",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "matplotlib.tests",
        "matplotlib.testing",
        "PySide6",
        "PyQt5",
        "PyQt6",
        "tkinter",
        "pytest",
        "setuptools",
        "pip",
        "wheel",
    ],
    noarchive=False,
)

# ── 动态库和资源文件 ────────────────────────────────────────────

# RapidOCR ONNX 模型文件
try:
    rapidocr_datas = collect_data_files("rapidocr_onnxruntime")
    a.datas += rapidocr_datas
except Exception:
    pass

# ONNX Runtime 动态库
try:
    onnx_binaries = collect_dynamic_libs("onnxruntime")
    a.binaries += onnx_binaries
except Exception:
    pass

# ── 可执行文件 ──────────────────────────────────────────────────

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name=app_name,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name=app_name,
)
