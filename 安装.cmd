@echo off
rem First-time setup: create venv, install deps, run selftest
cd /d "%~dp0"

if not exist ".venv" (
    python -m venv .venv
    if errorlevel 1 goto err
)

".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install PySide6 psutil
".venv\Scripts\python.exe" selftest.py

echo.
echo Done. Double-click the launcher cmd to start.
pause
exit /b 0

:err
echo [ERROR] Failed to create venv. Make sure Python 3.10+ is installed.
pause
exit /b 1
