@echo off
rem AI Signal Light - launcher
rem Prefer the packaged exe; fall back to source + venv.
cd /d "%~dp0"

rem Skip the trailing pause when nobody is watching (CI, script, pipe).
rem See the note in install.cmd.
if not defined NO_PAUSE (
    powershell -NoProfile -ExecutionPolicy Bypass -Command ^
        "if ([Console]::IsInputRedirected) { exit 1 } else { exit 0 }"
    if errorlevel 1 set "NO_PAUSE=1"
)

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
if not defined NO_PAUSE pause
exit /b 1
