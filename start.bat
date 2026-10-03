@echo off
chcp 936 >nul
setlocal enabledelayedexpansion
cd /d "%~dp0"

REM ============================================================================
REM  AIR 智能体研究系统 - 一键启动
REM
REM    start.bat                 生产模式: 自动构建前端 (缺失或过期) 并启动
REM    start.bat --no-build      跳过前端构建检查 (只改后端时最快)
REM    start.bat --no-browser    启动但不自动打开浏览器
REM    start.bat dev             开发模式: 后端 + vite 开发服务器 (改前端即时生效)
REM    start.bat --help          显示本说明
REM ============================================================================

set "MODE=prod"
set "DO_BUILD=1"
set "NO_BROWSER=0"
REM 带参数调用视为脚本化调用, 结束时不再等待按键
if not "%~1"=="" set "AIR_NO_PAUSE=1"

:parse
if "%~1"=="" goto parsed
if /i "%~1"=="dev" goto arg_dev
if /i "%~1"=="--dev" goto arg_dev
if /i "%~1"=="--no-build" goto arg_nobuild
if /i "%~1"=="nobuild" goto arg_nobuild
if /i "%~1"=="--no-browser" goto arg_nobrowser
if /i "%~1"=="--help" goto usage
if /i "%~1"=="-h" goto usage
echo [警告] 未识别的参数: %~1
shift
goto parse

:arg_dev
set "MODE=dev"
shift
goto parse

:arg_nobuild
set "DO_BUILD=0"
shift
goto parse

:arg_nobrowser
set "NO_BROWSER=1"
shift
goto parse

:usage
echo 用法:
echo    start.bat                 生产模式: 自动构建前端 (缺失或过期) 并启动
echo    start.bat --no-build      跳过前端构建检查 (只改后端时最快)
echo    start.bat --no-browser    启动但不自动打开浏览器
echo    start.bat dev             开发模式: 后端 + vite 开发服务器 (改前端即时生效)
echo.
echo 首次使用请先运行 setup_env.bat 搭建环境。
call :do_pause
exit /b 0

:parsed
echo ============================================
echo   AIR 智能体研究系统
if "%MODE%"=="dev" (
    echo   开发模式: 后端 8000 + 前端 5173
) else (
    echo   多智能体 · 对话式协作
)
echo ============================================
echo.

REM ---------- [1/4] 定位 Python ----------
set "PYTHON="
if exist ".venv\Scripts\python.exe" (
    set "PYTHON=.venv\Scripts\python.exe"
    echo [1/4] 使用虚拟环境 .venv
) else (
    where python >nul 2>nul
    if errorlevel 1 (
        echo [错误] 未找到 Python。请先运行 setup_env.bat 搭建环境,
        echo        或安装 Python 3.10+ 并勾选 "Add python.exe to PATH"。
call :do_pause
        exit /b 1
    )
    set "PYTHON=python"
    echo [1/4] 未发现 .venv, 使用系统 Python ^(建议先运行 setup_env.bat^)
)

REM ---------- [2/4] 检查依赖 ----------
"%PYTHON%" -c "import langgraph, fastapi, uvicorn" >nul 2>nul
if errorlevel 1 (
    echo [2/4] 未检测到依赖, 正在安装 requirements.txt ...
    "%PYTHON%" -m pip install -r requirements.txt
    if errorlevel 1 (
        echo [错误] 依赖安装失败。可改用镜像重试: setup_env.bat --mirror
call :do_pause
        exit /b 1
    )
) else (
    echo [2/4] 依赖检查通过
)

REM ---------- [3/4] 前端 ----------
REM 服务端只提供构建产物 src\web\dist\index.html; 缺产物时返回 503 与构建指引,
REM 不会再把引用 /src/main.ts 的源码模板当页面返回 (那种页面按钮全是死的)。
if "%MODE%"=="dev" goto frontend_dev

if "%DO_BUILD%"=="0" goto skip_build

where npm >nul 2>nul
if errorlevel 1 (
    echo [错误] 未检测到 npm, 无法构建前端。请安装 Node.js 18+ 后执行:
    echo            cd src\web ^&^& npm install ^&^& npm run build
    echo        或用开发模式启动 ^(需要已安装 Node.js^): start.bat dev
call :do_pause
    exit /b 1
)

set "NEED_BUILD=0"
set "STALE=0"
for /f "usebackq delims=" %%r in (`powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\frontend_needs_build.ps1"`) do set "STALE=%%r"
if "!STALE!"=="1" (
    set "NEED_BUILD=1"
    echo [3/4] 前端产物缺失或已过期, 需要构建
) else (
    echo [3/4] 前端构建产物是最新的
)
if "!NEED_BUILD!"=="0" goto start_server

echo       正在构建前端 ^(首次会 npm install, 可能较慢^) ...
pushd "src\web"
if not exist "node_modules" (
    call npm install
    if errorlevel 1 (
        popd
        echo [错误] npm install 失败。可手动执行: cd src\web ^&^& npm install
call :do_pause
        exit /b 1
    )
)
call npm run build
set "BUILD_RC=!errorlevel!"
popd
if not "!BUILD_RC!"=="0" (
    echo [错误] 前端构建失败 ^(退出码 !BUILD_RC!^)。请手动查看报错:
    echo            cd src\web ^&^& npm run build
call :do_pause
    exit /b 1
)
if not exist "src\web\dist\index.html" (
    echo [错误] 构建结束但仍没有 src\web\dist\index.html
call :do_pause
    exit /b 1
)
echo       前端构建完成
goto start_server

:skip_build
if not exist "src\web\dist\index.html" (
    echo [错误] 指定了 --no-build 但缺少构建产物 src\web\dist\index.html
    echo        请先执行: cd src\web ^&^& npm install ^&^& npm run build
call :do_pause
    exit /b 1
)
echo [3/4] 跳过前端构建检查 ^(--no-build^)
goto start_server

:frontend_dev
echo [3/4] 开发模式: 后端提供源码模板, 前端由 vite 提供 ^(改代码即时生效^)
if not exist "src\web\node_modules" (
    where npm >nul 2>nul
    if errorlevel 1 (
        echo [错误] 未检测到 npm, 开发模式需要 Node.js
call :do_pause
        exit /b 1
    )
    echo       正在安装前端依赖 ...
    pushd "src\web"
    call npm install
    popd
)
set "AIR_WEB_DEV=1"
set "NO_BROWSER=1"

:start_server
echo [4/4] 启动后端 http://127.0.0.1:8000
echo       关闭本窗口或按 Ctrl+C 即可停止服务。
echo.

if "%MODE%"=="dev" (
    start "AIR 前端 (vite dev)" cmd /k "cd /d %~dp0src\web && npm run dev"
    echo       前端开发服务器: http://127.0.0.1:5173 ^(已打开单独窗口^)
    echo       浏览器请访问: http://127.0.0.1:5173
    timeout /t 3 /nobreak >nul
    start "" "http://127.0.0.1:5173"
    "%PYTHON%" -m src.server --no-browser
    echo.
    echo [结束] 后端已停止。前端窗口请手动关闭。
call :do_pause
    exit /b 0
)

if "%NO_BROWSER%"=="1" (
    "%PYTHON%" -m src.server --no-browser
) else (
    "%PYTHON%" -m src.server
)

echo.
if errorlevel 1 (
    echo [错误] 服务异常退出 ^(退出码 %errorlevel%^)。常见原因:
    echo        - 8000 端口被占用: 关闭占用进程后重试
    echo        - 未构建前端: 执行 cd src\web ^&^& npm run build
    echo        - 依赖缺失: 重新运行 setup_env.bat
)
echo [结束] 服务已停止。
call :do_pause

exit /b 0

:do_pause
if "%AIR_NO_PAUSE%"=="1" exit /b 0
pause
exit /b 0
