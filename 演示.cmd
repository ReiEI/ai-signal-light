@echo off
rem Demo mode: run through the five states once, then hand back to the
rem real status. It used to loop forever, which meant you could never get
rem back to the widget doing its actual job.
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
