@echo off
chcp 936 >nul
cd /d "%~dp0"

echo ============================================
echo   AIR智能体研究系统
echo   多智能体 · 对话式协作
echo ============================================
echo.

set "PYTHON="
if exist ".venv\Scripts\python.exe" (
    set "PYTHON=.venv\Scripts\python.exe"
    echo [信息] 使用虚拟环境 .venv
) else (
    where python >nul 2>nul
    if errorlevel 1 (
        echo [错误] 未找到 Python，请先安装 Python 3.10+ 并加入 PATH
        pause
        exit /b 1
    )
    set "PYTHON=python"
    echo [信息] 使用系统 Python
)

"%PYTHON%" -c "import langgraph, fastapi, uvicorn" >nul 2>nul
if errorlevel 1 (
    echo [提示] 未检测到依赖，正在安装 requirements.txt ...
    "%PYTHON%" -m pip install -r requirements.txt
    if errorlevel 1 (
        echo [错误] 依赖安装失败，请手动执行: pip install -r requirements.txt
        pause
        exit /b 1
    )
)

echo [启动] 正在启动 Web 界面，浏览器将自动打开 http://127.0.0.1:8000
echo        关闭本窗口或按 Ctrl+C 即可停止服务。
echo.
"%PYTHON%" -m src.server

echo.
echo [结束] 服务已停止。
pause
