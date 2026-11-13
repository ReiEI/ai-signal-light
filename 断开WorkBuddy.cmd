@echo off
setlocal
cd /d "%~dp0"
set "PY=%~dp0.venv\Scripts\python.exe"
if not exist "%PY%" (
  echo [!] venv missing.
  pause
  exit /b 1
)
"%PY%" install_hooks.py --remove
echo.
pause
