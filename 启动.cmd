@echo off
rem AI Signal Light - launcher
rem Prefer the packaged exe; fall back to source + venv.
cd /d "%~dp0"

if exist "dist\SignalLight.exe" (
    start "" "dist\SignalLight.exe"
    exit /b 0
)
if exist ".venv\Scripts\pythonw.exe" (
    start "" ".venv\Scripts\pythonw.exe" "app\main.py"
    exit /b 0
)
echo [ERROR] dist\SignalLight.exe not found, and .venv is missing.
echo Run install first, or put SignalLight.exe into the dist folder.
pause
exit /b 1
