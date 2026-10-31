@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

rem 优先使用项目自带 venv（含全部依赖，避免缺依赖导致导入静默失败）
if exist ".venv\Scripts\python.exe" (
  set "PY=.venv\Scripts\python.exe"
) else (
  where python >nul 2>nul
  if errorlevel 1 (
    echo 未找到 Python。请先安装 Python 3.11+，或在项目目录创建 .venv 虚拟环境。
    pause
    exit /b 1
  )
  set "PY=python"
)

rem 数据目录优先使用平台集成的 ..\Reader
if exist "..\Reader" (
  set "DATA_DIR=..\Reader"
) else (
  set "DATA_DIR=%LOCALAPPDATA%\GuidedReader"
)

echo 正在启动 Guided Reader，数据目录：%DATA_DIR%
echo 关闭本窗口即退出 Reader。
"%PY%" -m reader_service --data-dir "%DATA_DIR%" %*
if errorlevel 1 pause
