@echo off
rem Demo mode: cycle through the five states
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" "app\main.py" --demo
    exit /b 0
)
echo [ERROR] .venv not found. Run install first.
pause
exit /b 1
