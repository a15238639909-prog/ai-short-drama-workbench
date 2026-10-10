@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist "runtime\python\python.exe" (
  echo 请下载 Windows 便携包并完整解压，GitHub 源码包不包含 Python 运行环境。
  pause
  exit /b 1
)
"runtime\python\python.exe" -E -s -B "portable_start.py"
if errorlevel 1 pause
