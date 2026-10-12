#!/bin/bash
# pyinstaller 打包脚本 (Linux/macOS)
set -e

echo "=== 安装依赖 ==="
pip install -e ".[dev]"

echo "=== 生成测试视频 ==="
python tests/fixtures/generate_test_video.py

echo "=== 运行测试 ==="
pytest --tb=short

echo "=== PyInstaller 打包 ==="
pyinstaller drama-splitter.spec --clean --noconfirm

echo "=== 验证打包结果 ==="
./dist/drama-splitter/drama-splitter --help

echo ""
echo "打包完成: dist/drama-splitter/"
echo "可执行文件: dist/drama-splitter/drama-splitter"
