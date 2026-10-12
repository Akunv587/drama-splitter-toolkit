# pyinstaller 打包脚本 (Windows PowerShell)
$ErrorActionPreference = "Stop"

Write-Host "=== 安装依赖 ==="
pip install -e ".[dev]"

Write-Host "=== 生成测试视频 ==="
python tests/fixtures/generate_test_video.py

Write-Host "=== 运行测试 ==="
pytest --tb=short

Write-Host "=== PyInstaller 打包 ==="
pyinstaller drama-splitter.spec --clean --noconfirm

Write-Host "=== 验证打包结果 ==="
.\dist\drama-splitter\drama-splitter.exe --help

Write-Host ""
Write-Host "打包完成: dist\drama-splitter\"
Write-Host "可执行文件: dist\drama-splitter\drama-splitter.exe"
