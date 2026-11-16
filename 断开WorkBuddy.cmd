@echo off
setlocal
cd /d "%~dp0"

rem Skip the trailing pause when nobody is watching (CI, script, pipe).
rem See the note in install.cmd.
if not defined NO_PAUSE (
    powershell -NoProfile -ExecutionPolicy Bypass -Command ^
        "if ([Console]::IsInputRedirected) { exit 1 } else { exit 0 }"
    if errorlevel 1 set "NO_PAUSE=1"
)

set "PY=%~dp0.venv\Scripts\python.exe"
if not exist "%PY%" (
  echo [!] venv missing.
  if not defined NO_PAUSE pause
  exit /b 1
)
"%PY%" install_hooks.py --remove
echo.
if not defined NO_PAUSE pause
exit /b 0
