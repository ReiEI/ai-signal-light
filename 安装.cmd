@echo off
rem First-time setup: locate a usable Python, create venv, install deps, selftest.
rem
rem All content is pure ASCII on purpose: cmd.exe parses batch files with the
rem system ANSI code page (GBK on Chinese Windows), and UTF-8 text breaks the
rem parser mid-script.
rem
rem Why not just call "python -m venv"? Three real reasons:
rem   1) "python" on PATH may be the Microsoft Store alias stub. It exists,
rem      it is on PATH, and it exits with code 9009 doing nothing.
rem   2) Python may be installed but not on PATH. The "py" launcher finds it.
rem   3) An existing .venv can be broken (machine changed, Python upgraded,
rem      folder moved) while the folder still exists - "if not exist" would
rem      happily reuse it and fail later with a confusing error.
rem So the real logic lives in check_env.py; this script only finds a
rem runnable interpreter and hands over.

setlocal
cd /d "%~dp0"

rem Optional argument: path to a python.exe. Use it when Python is installed
rem but not on PATH:
rem     install.cmd "C:\Users\you\AppData\Local\Programs\Python\Python313\python.exe"
if "%~1"=="" goto autodetect
if not exist "%~1" goto badarg
"%~1" -c "import sys" >nul 2>&1
if errorlevel 1 goto badarg
set "PY=%~1"
goto havepy

:badarg
echo [ERROR] "%~1" is not a working Python interpreter.
goto nopython

rem ---- 1) find an interpreter that can actually execute Python code --------
:autodetect
set "PY="
set "PYARGS="

rem The py launcher is the most reliable source on Windows: it knows where
rem every registered Python lives, including ones absent from PATH.
py -3 -c "import sys" >nul 2>&1
if not errorlevel 1 (
    set "PY=py"
    set "PYARGS=-3"
)

if not defined PY (
    python -c "import sys" >nul 2>&1
    if not errorlevel 1 set "PY=python"
)

if not defined PY (
    python3 -c "import sys" >nul 2>&1
    if not errorlevel 1 set "PY=python3"
)

rem Fallback for the most common real-world case: Python IS installed but
rem was never added to PATH (the checkbox was left unticked). Without this
rem the user would get nothing but an error - not even the diagnostic
rem report - because the report itself needs an interpreter to run.
if not defined PY (
    for %%P in (
        "%USERPROFILE%\AppData\Local\Programs\Python\Python313\python.exe"
        "%USERPROFILE%\AppData\Local\Programs\Python\Python312\python.exe"
        "%USERPROFILE%\AppData\Local\Programs\Python\Python311\python.exe"
        "%USERPROFILE%\AppData\Local\Programs\Python\Python310\python.exe"
        "C:\Python313\python.exe"
        "C:\Python312\python.exe"
        "C:\Python311\python.exe"
        "C:\Python310\python.exe"
    ) do (
        if not defined PY (
            if exist %%P (
                %%P -c "import sys" >nul 2>&1
                if not errorlevel 1 set "PY=%%~P"
            )
        )
    )
)

if not defined PY goto nopython

:havepy

rem ---- 2) environment check + create venv ----------------------------------
echo [1/3] Checking Python environment...
"%PY%" %PYARGS% check_env.py --create-venv
if errorlevel 1 goto envfail

rem ---- 3) install dependencies ---------------------------------------------
echo.
echo [2/3] Installing dependencies (PySide6 is large, this takes a while)...
".venv\Scripts\python.exe" -m pip install --upgrade pip -q

rem Optional mirror, for slow links to pypi.org (mainland China):
rem     set PIPMIRROR=-i https://pypi.tuna.tsinghua.edu.cn/simple
rem     install.cmd
rem Left empty by default - a wrong or unreachable mirror is worse than a
rem slow-but-working official source.
if not defined PIPMIRROR (
    echo     (using the default pypi.org - set PIPMIRROR for a faster mirror)
)
".venv\Scripts\python.exe" -m pip install -r requirements.txt %PIPMIRROR%
if errorlevel 1 goto deperr

echo.
echo [3/3] Running self-test...
".venv\Scripts\python.exe" selftest.py
if errorlevel 1 goto testfail

echo.
echo Done. Double-click the launcher cmd to start.
pause
exit /b 0

:nopython
echo.
echo [ERROR] No usable Python interpreter was found.
echo.
echo Tried, in order:
echo   1) py -3       (Windows Python launcher)
echo   2) python      (PATH)
echo   3) python3     (PATH)
echo.
echo Fix it with one of these:
echo.
echo   A) Install official Python 3.13 x64:
echo      https://www.python.org/downloads/windows/
echo      IMPORTANT: tick "Add python.exe to PATH" during setup.
echo.
echo   B) Already installed but not on PATH? Run this from a normal cmd:
echo      "%USERPROFILE%\AppData\Local\Programs\Python\Python313\python.exe" -m venv .venv
echo.
echo   C) The Microsoft Store version does NOT work - it is only a stub.
echo.
pause
exit /b 1

:envfail
echo.
echo [ERROR] Environment check failed. See the report above for details.
pause
exit /b 1

:deperr
echo.
echo [ERROR] Dependency install failed.
echo.
echo PySide6 is a 250+ MB download across 7 packages. If this failed on a
echo timeout or a connection reset, pypi.org is probably the bottleneck.
echo Use a mirror:
echo.
echo   set PIPMIRROR=-i https://pypi.tuna.tsinghua.edu.cn/simple
echo   install.cmd
echo
echo or set it permanently, then run install.cmd again:
echo   .venv\Scripts\python.exe -m pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple
echo
pause
exit /b 1

:testfail
echo.
echo [ERROR] Self-test failed. The installation may be incomplete.
echo Run ".venv\Scripts\python.exe selftest.py" to see which checks failed.
pause
exit /b 1
