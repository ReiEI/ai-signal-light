@echo off
rem Rebuild all three standalone exes
rem   SignalLight.exe  widget (PySide6)
rem   signal.exe       status writer CLI
rem   fan_agent.exe    thermal agent (elevable, stdlib only)
rem
rem Pure ASCII on purpose: cmd.exe parses batch files with the system ANSI
rem code page (GBK on Chinese Windows), and UTF-8 text breaks the parser.
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] .venv not found. Run install first.
    pause
    exit /b 1
)

rem The widget exe must not be running: PyInstaller writes dist\SignalLight.exe
rem directly, and Windows refuses to overwrite a file that is being executed
rem (PermissionError [WinError 5]). Detect it up front instead of failing
rem three minutes into the build.
rem
rem Uses PowerShell rather than "tasklist | find": on a Chinese Windows,
rem tasklist output is GBK and the bundled find.exe is not GNU find, so the
rem pipeline is unreliable. PowerShell gives a clean exit code.
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "if (Get-Process SignalLight -ErrorAction SilentlyContinue) { exit 1 } else { exit 0 }"
if errorlevel 1 (
    echo [ERROR] SignalLight.exe is running - the build cannot overwrite it.
    echo         Close it from the tray icon, then run this again.
    pause
    exit /b 1
)

rem Pinned to the version in requirements-dev.txt. An unpinned install would
rem silently pick up a newer pyinstaller and change build behaviour.
".venv\Scripts\python.exe" -m pip install -q "pyinstaller==6.22.3"
if errorlevel 1 (
    echo [ERROR] Failed to install pyinstaller.
    pause
    exit /b 1
)

echo.
echo [1/4] SignalLight.exe
".venv\Scripts\python.exe" -m PyInstaller --noconfirm --onefile --windowed ^
    --paths app ^
    --icon "assets\signallight.ico" ^
    --add-data "assets\signallight.ico;assets" ^
    --hidden-import state --hidden-import theme --hidden-import metrics --hidden-import codex ^
    --hidden-import cooling --hidden-import cooling_ui ^
    --hidden-import ecfan --hidden-import powerplan --hidden-import textio ^
    --name "SignalLight" --distpath dist --workpath build\widget app\main.py
if errorlevel 1 goto fail

echo.
echo [2/4] signal.exe
".venv\Scripts\python.exe" -m PyInstaller --noconfirm --onefile --console ^
    --paths app --hidden-import state --hidden-import textio ^
    --icon "assets\signallight.ico" ^
    --name "signal" --distpath dist --workpath build\signal signal_cli.py
if errorlevel 1 goto fail

echo.
echo [3/4] fan_agent.exe
rem Build separately on purpose: it runs elevated, so PySide6 must NOT be bundled
".venv\Scripts\python.exe" -m PyInstaller --noconfirm --onefile --windowed ^
    --paths app --hidden-import ecfan --hidden-import powerplan --hidden-import textio ^
    --icon "assets\signallight.ico" ^
    --name "fan_agent" --distpath dist --workpath build\agent fan_agent.py
if errorlevel 1 goto fail

echo.
echo [4/4] Verifying build output...
".venv\Scripts\python.exe" verify_dist.py
if errorlevel 1 goto badbundle

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

:badbundle
echo.
echo [ERROR] The build produced broken output - see the report above.
echo         Do not publish these files. Fix the cause and rebuild.
pause
exit /b 1
