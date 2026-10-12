@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ========================================
echo   短剧总集篇自动分集工具
echo ========================================
echo.

:: 检查 Python
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [错误] 未找到 Python，请先安装 Python 3.10+
    pause
    exit /b 1
)

:: 检查依赖
python -c "import gradio" >nul 2>&1
if %errorlevel% neq 0 (
    echo [提示] 检测到缺少依赖，正在安装...
    pip install -r requirements.txt
    if %errorlevel% neq 0 (
        echo [错误] 依赖安装失败，请手动执行: pip install -r requirements.txt
        pause
        exit /b 1
    )
)

:: 检查 ffmpeg
ffmpeg -version >nul 2>&1
if %errorlevel% neq 0 (
    echo [警告] 未检测到 ffmpeg，视频裁剪功能将不可用
    echo         请安装 ffmpeg 并加入 PATH: https://ffmpeg.org/download.html
    echo.
)

:: 启动
echo [启动] 正在启动 Gradio 界面...
echo         浏览器即将自动打开
echo         按 Ctrl+C 停止服务
echo.

:: 后台启动 Python 服务
start "短剧分集工具" /B python main.py

:: 等待服务就绪后打开浏览器
echo [等待] 服务启动中（约 5 秒）...
:wait_loop
timeout /t 1 /nobreak >nul
powershell -Command "try { (Invoke-WebRequest -Uri 'http://127.0.0.1:7860' -TimeoutSec 2 -UseBasicParsing).StatusCode } catch { exit 1 }" >nul 2>&1
if %errorlevel% equ 0 goto open_browser
goto wait_loop

:open_browser
start http://127.0.0.1:7860
echo [完成] 浏览器已打开

:: 保持窗口不关闭
pause >nul
