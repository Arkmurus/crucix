@echo off
title ARIA
cd /d "%~dp0"

:: ─────────────────────────────────────────────────
:: ARIA — one command to run
:: ─────────────────────────────────────────────────
:: Put this folder anywhere on your computer.
:: Open cmd in this folder and type: aria
:: That's it. ARIA starts at http://localhost:8000
:: ─────────────────────────────────────────────────

set "ARIA_DIR=%~dp0"
set "PYTHON_DIR=%ARIA_DIR%python"
set "CODE_DIR=%ARIA_DIR%code"
set "PYTHON_URL=https://www.python.org/ftp/python/3.13.3/python-3.13.3-embed-amd64.zip"
set "CODE_URL=https://github.com/Arkmurus/crucix/archive/refs/heads/main.zip"

:: ── Step 1: Download Python if needed ─────────────────
if not exist "%PYTHON_DIR%\python.exe" (
    echo Downloading Python...
    mkdir "%PYTHON_DIR%" 2>nul
    powershell -Command "& { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -Uri '%PYTHON_URL%' -OutFile '%ARIA_DIR%python.zip' }"
    powershell -Command "& { Expand-Archive -Path '%ARIA_DIR%python.zip' -DestinationPath '%PYTHON_DIR%' -Force }"
    del "%ARIA_DIR%python.zip"
    echo. >> "%PYTHON_DIR%\python._pth"
    echo import site >> "%PYTHON_DIR%\python._pth"
    powershell -Command "& { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -Uri 'https://bootstrap.pypa.io/get-pip.py' -OutFile '%ARIA_DIR%get-pip.py' }"
    "%PYTHON_DIR%\python.exe" "%ARIA_DIR%get-pip.py" --user
    del "%ARIA_DIR%get-pip.py"
)

:: ── Step 2: Download ARIA code if needed ──────────────
if not exist "%CODE_DIR%\aria_service\main.py" (
    echo Downloading ARIA...
    mkdir "%CODE_DIR%" 2>nul
    powershell -Command "& { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -Uri '%CODE_URL%' -OutFile '%ARIA_DIR%code.zip' }"
    powershell -Command "& { Expand-Archive -Path '%ARIA_DIR%code.zip' -DestinationPath '%ARIA_DIR%temp' -Force }"
    if exist "%ARIA_DIR%temp\crucix-main" (
        move "%ARIA_DIR%temp\crucix-main\*" "%CODE_DIR%\" >nul 2>&1
    )
    rmdir /s /q "%ARIA_DIR%temp"
    del "%ARIA_DIR%code.zip"
)

:: ── Step 3: Install dependencies ──────────────────────
if not exist "%ARIA_DIR%.deps_done" (
    echo Installing dependencies (first time only)...
    "%PYTHON_DIR%\python.exe" -m pip install --user -r "%CODE_DIR%\aria_service\requirements.txt" --quiet
    echo done > "%ARIA_DIR%.deps_done"
)

:: ── Step 4: Start ARIA ────────────────────────────────
echo.
echo  ARIA is starting...
echo  Open http://localhost:8000 in your browser
echo  Press Ctrl+C to stop
echo.

cd /d "%CODE_DIR%"
set "ARIA_STATE_BACKEND=sqlite"
set "ARIA_INTERNAL_TOKEN=local-dev-token"
set "ARIA_AUTONOMOUS_ENABLED=0"
set "ARIA_CODER_ENABLED=0"

"%PYTHON_DIR%\python.exe" -m uvicorn aria_service.main:app --host 0.0.0.0 --port 8000
