@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
echo AI Video Workbench V1.0 - starting server.py (close this window to stop)
python server.py
pause
