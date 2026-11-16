@echo off
rem Demo mode: cycle through the five states
cd /d "%~dp0"

rem Skip the trailing pause when nobody is watching (CI, script, pipe).
rem See the note in install.cmd.
if not defined NO_PAUSE (
    powershell -NoProfile -ExecutionPolicy Bypass -Command ^
        "if ([Console]::IsInputRedirected) { exit 1 } else { exit 0 }"
    if errorlevel 1 set "NO_PAUSE=1"
)

if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" "app\main.py" --demo
    exit /b 0
)
echo [ERROR] .venv not found. Run install first.
if not defined NO_PAUSE pause
exit /b 1
