@echo off
chcp 936 >nul
setlocal enabledelayedexpansion
cd /d "%~dp0"

set "PIP_ARGS="
if /i "%~1"=="--mirror" set "PIP_ARGS=-i https://pypi.tuna.tsinghua.edu.cn/simple"
if /i "%~1"=="-m" set "PIP_ARGS=-i https://pypi.tuna.tsinghua.edu.cn/simple"

echo ============================================
echo   AIR 智能体研究系统 - 一键环境搭建
echo ============================================
echo.

REM ---------- [1/5] 检测 Python (需要 3.10+) ----------
set "PYTHON="
where python >nul 2>nul
if not errorlevel 1 (
    python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
    if not errorlevel 1 set "PYTHON=python"
)
if not defined PYTHON (
    where py >nul 2>nul
    if not errorlevel 1 (
        py -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
        if not errorlevel 1 set "PYTHON=py -3"
    )
)
if not defined PYTHON (
    echo [错误] 未检测到 Python 3.10 或更高版本
    echo        请先安装: https://www.python.org/downloads/
    echo        安装时务必勾选 "Add python.exe to PATH"
    pause
    exit /b 1
)
for /f "delims=" %%v in ('%PYTHON% --version 2^>^&1') do set "PYVER=%%v"
echo [1/5] 检测到 !PYVER!

REM ---------- [2/5] 创建虚拟环境 .venv ----------
if exist ".venv\Scripts\python.exe" (
    echo [2/5] 已存在虚拟环境 .venv，跳过创建
) else (
    echo [2/5] 正在创建虚拟环境 .venv ...
    %PYTHON% -m venv .venv
    if errorlevel 1 (
        echo [错误] 虚拟环境创建失败
        pause
        exit /b 1
    )
    echo       正在升级 pip ...
    ".venv\Scripts\python.exe" -m pip install --upgrade pip %PIP_ARGS%
)

set "VPY=.venv\Scripts\python.exe"

REM ---------- [3/5] 安装依赖 ----------
echo [3/5] 安装 requirements.txt 依赖 (首次约 3-10 分钟) ...
"%VPY%" -m pip install -r requirements.txt %PIP_ARGS%
if errorlevel 1 (
    echo.
    echo [错误] 依赖安装失败。若为网络超时，可改用国内镜像重试:
    echo        setup_env.bat --mirror
    echo        或手动执行:
    echo        .venv\Scripts\python.exe -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
    pause
    exit /b 1
)

REM ---------- [4/5] 生成 .env ----------
if exist ".env" (
    echo [4/5] 已存在 .env，跳过生成
) else (
    copy /y ".env.example" ".env" >nul
    if errorlevel 1 (
        echo [警告] .env 生成失败，请手动复制 .env.example 为 .env
    ) else (
        echo [4/5] 已根据 .env.example 生成 .env，请填写其中的 API Key
    )
)

REM ---------- [5/5] 验证关键依赖 ----------
echo [5/5] 验证关键依赖 ...
"%VPY%" -c "import langgraph, fastapi, uvicorn, chromadb, pymupdf, matplotlib; print('       OK: langgraph / fastapi / uvicorn / chromadb / pymupdf / matplotlib')"
if errorlevel 1 (
    echo [错误] 关键依赖导入失败，请检查上方报错
    pause
    exit /b 1
)

echo.
echo ============================================
echo   环境搭建完成！
echo.
echo   下一步:
echo     1. 用编辑器打开 .env，填写 OPENAI_API_KEY 等配置
echo        ^(详见 README.md 的「环境变量配置」章节^)
echo     2. 双击 start.bat 启动 Web 界面 http://127.0.0.1:8000
echo ============================================
pause
endlocal
