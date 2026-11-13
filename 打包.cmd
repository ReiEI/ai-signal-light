@echo off
rem Rebuild all three standalone exes
rem   SignalLight.exe  widget (PySide6)
rem   signal.exe       status writer CLI
rem   fan_agent.exe    thermal agent (elevable, stdlib only)
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] .venv not found. Run install first.
    pause
    exit /b 1
)

".venv\Scripts\python.exe" -m pip install -q pyinstaller

echo.
echo [1/3] SignalLight.exe
".venv\Scripts\python.exe" -m PyInstaller --noconfirm --onefile --windowed ^
    --paths app ^
    --hidden-import state --hidden-import metrics --hidden-import codex ^
    --hidden-import cooling --hidden-import cooling_ui ^
    --hidden-import ecfan --hidden-import powerplan --hidden-import textio ^
    --name "SignalLight" --distpath dist --workpath build\widget app\main.py
if errorlevel 1 goto fail

echo.
echo [2/3] signal.exe
".venv\Scripts\python.exe" -m PyInstaller --noconfirm --onefile --console ^
    --paths app --hidden-import state --hidden-import textio ^
    --name "signal" --distpath dist --workpath build\signal signal.py
if errorlevel 1 goto fail

echo.
echo [3/3] fan_agent.exe
rem Build separately on purpose: it runs elevated, so PySide6 must NOT be bundled
".venv\Scripts\python.exe" -m PyInstaller --noconfirm --onefile --windowed ^
    --paths app --hidden-import ecfan --hidden-import powerplan --hidden-import textio ^
    --name "fan_agent" --distpath dist --workpath build\agent fan_agent.py
if errorlevel 1 goto fail

echo.
echo Build finished:
dir /b dist\*.exe
pause
exit /b 0

:fail
echo.
echo [ERROR] Build failed.
pause
exit /b 1
