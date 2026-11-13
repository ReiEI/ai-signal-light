@echo off
rem Status writer:  signal.cmd running "title" -d "detail" -t 30
cd /d "%~dp0"
chcp 65001 >nul

if exist "dist\signal.exe" (
    "dist\signal.exe" %*
    exit /b %errorlevel%
)
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" "signal.py" %*
    exit /b %errorlevel%
)
echo [ERROR] dist\signal.exe not found.
exit /b 1
