@echo off
REM ARIA Portable Launcher — for non-admin users on Windows
REM
REM This uses Python's embeddable distribution (no install, no admin).
REM Download Python embeddable from python.org, extract to a folder,
REM and point this script to it.
REM
REM Usage:
REM   run_aria_portable.bat [--port 8000]
REM
REM First time setup (one-time, no admin needed):
REM   1. Download Python 3.13 embeddable from:
REM      https://www.python.org/ftp/python/3.13.3/python-3.13.3-embed-amd64.zip
REM   2. Extract the ZIP to a folder, e.g. C:\Users\You\python-embed
REM   3. Run: set PYTHON_EMBED=C:\Users\You\python-embed
REM   4. Run this script

setlocal enabledelayedexpansion

REM Check if Python is available
where python >nul 2>nul
if %ERRORLEVEL% equ 0 (
    echo [ARIA] Found system Python
    set PYTHON_CMD=python
    goto :run
)

REM Check for embedded Python
if defined PYTHON_EMBED (
    if exist "%PYTHON_EMBED%\python.exe" (
        echo [ARIA] Found embedded Python at %PYTHON_EMBED%
        set PYTHON_CMD=%PYTHON_EMBED%\python.exe
        goto :run
    )
)

REM No Python found — guide the user
echo [ARIA] Python not found.
echo.
echo You need Python 3.13 to run ARIA. Two options:
echo.
echo Option A: Install Python (may need admin):
echo   1. Download from https://www.python.org/downloads/
echo   2. Run the installer (check "Add Python to PATH")
echo   3. Run this script again
echo.
echo Option B: Use embedded Python (no admin needed):
echo   1. Download from https://www.python.org/ftp/python/3.13.3/python-3.13.3-embed-amd64.zip
echo   2. Extract to a folder, e.g. C:\Users\%USERNAME%\python-embed
echo   3. Run: set PYTHON_EMBED=C:\Users\%USERNAME%\python-embed
echo   4. Run this script again
echo.
echo Option C: Use the hosted version (nothing to install):
echo   Open https://aria-intel.fly.dev in your browser
echo.
pause
exit /b 1

:run
echo [ARIA] Starting ARIA...
echo [ARIA] Health check: http://localhost:8000/health/live
echo [ARIA] Press Ctrl+C to stop
echo.

%PYTHON_CMD% scripts\aria_local_launcher.py %*

if %ERRORLEVEL% neq 0 (
    echo.
    echo [ARIA] Failed to start. Try:
    echo   python scripts\aria_local_launcher.py --skip-deps
    echo.
    pause
)
