@echo off
title ARIA — One-Click Launcher
cd /d "%~dp0"

:: ─────────────────────────────────────────────────
:: ARIA One-Click Launcher
:: ─────────────────────────────────────────────────
:: This script downloads everything needed to run
:: ARIA on your computer. No admin rights needed.
:: ─────────────────────────────────────────────────

set "ARIA_VERSION=v1.0"
set "PYTHON_URL=https://www.python.org/ftp/python/3.13.3/python-3.13.3-embed-amd64.zip"
set "ARIA_URL=https://github.com/Arkmurus/crucix/archive/refs/heads/main.zip"
set "ARIA_DIR=%CD%\aria"

echo.
echo  ╔══════════════════════════════════════════════╗
echo  ║           ARIA — One-Click Setup             ║
echo  ║  Autonomous Research Intelligence Agent       ║
echo  ╚══════════════════════════════════════════════╝
echo.
echo  This will download and install ARIA on your computer.
echo  No admin rights needed. Nothing is installed system-wide.
echo.

:: ── Step 1: Check if already installed ─────────────────
if exist "%ARIA_DIR%\python\python.exe" (
    if exist "%ARIA_DIR%\crucix\aria_service\main.py" (
        echo  ✅ ARIA is already downloaded.
        goto :run
    )
)

:: ── Step 2: Create directories ─────────────────────────
echo  📁 Creating workspace...
if not exist "%ARIA_DIR%" mkdir "%ARIA_DIR%"
if not exist "%ARIA_DIR%\python" mkdir "%ARIA_DIR%\python"
if not exist "%ARIA_DIR%\crucix" mkdir "%ARIA_DIR%\crucix"

:: ── Step 3: Download Python (embedded, no install) ─────
if not exist "%ARIA_DIR%\python\python.exe" (
    echo.
    echo  📥 Downloading Python 3.13 (embedded, ~30MB)...
    echo     This is a portable Python — no installation needed.
    echo.
    powershell -Command "& { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -Uri '%PYTHON_URL%' -OutFile '%ARIA_DIR%\python.zip' }"
    if errorlevel 1 (
        echo  ❌ Failed to download Python.
        echo     Check your internet connection and try again.
        pause
        exit /b 1
    )
    echo  📦 Extracting Python...
    powershell -Command "& { Expand-Archive -Path '%ARIA_DIR%\python.zip' -DestinationPath '%ARIA_DIR%\python' -Force }"
    del "%ARIA_DIR%\python.zip"
    
    :: Fix embedded Python to work with pip
    echo. >> "%ARIA_DIR%\python\python._pth"
    echo import site >> "%ARIA_DIR%\python\python._pth"
    
    :: Download get-pip.py
    powershell -Command "& { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -Uri 'https://bootstrap.pypa.io/get-pip.py' -OutFile '%ARIA_DIR%\get-pip.py' }"
    "%ARIA_DIR%\python\python.exe" "%ARIA_DIR%\get-pip.py" --user
    del "%ARIA_DIR%\get-pip.py"
    
    echo  ✅ Python downloaded and configured.
) else (
    echo  ✅ Python already downloaded.
)

:: ── Step 4: Download ARIA code ─────────────────────────
if not exist "%ARIA_DIR%\crucix\aria_service\main.py" (
    echo.
    echo  📥 Downloading ARIA code (~5MB)...
    powershell -Command "& { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -Uri '%ARIA_URL%' -OutFile '%ARIA_DIR%\aria.zip' }"
    if errorlevel 1 (
        echo  ❌ Failed to download ARIA code.
        pause
        exit /b 1
    )
    echo  📦 Extracting...
    powershell -Command "& { Expand-Archive -Path '%ARIA_DIR%\aria.zip' -DestinationPath '%ARIA_DIR%\temp' -Force }"
    if exist "%ARIA_DIR%\temp\crucix-main" (
        move "%ARIA_DIR%\temp\crucix-main\*" "%ARIA_DIR%\crucix\" >nul 2>&1
    )
    rmdir /s /q "%ARIA_DIR%\temp"
    del "%ARIA_DIR%\aria.zip"
    echo  ✅ ARIA code downloaded.
) else (
    echo  ✅ ARIA code already downloaded.
)

:: ── Step 5: Install dependencies ───────────────────────
echo.
echo  📦 Installing Python dependencies (first time only)...
echo     This may take a few minutes for the first run.
"%ARIA_DIR%\python\python.exe" -m pip install --user -r "%ARIA_DIR%\crucix\aria_service\requirements.txt" --quiet
if errorlevel 1 (
    echo  ⚠️  Some dependencies may not have installed cleanly.
    echo     ARIA will still work, but some features may be limited.
)

:: ── Step 6: Create launcher shortcut ───────────────────
echo.
echo  🔗 Creating desktop shortcut...
set "SHORTCUT=%USERPROFILE%\Desktop\ARIA.lnk"
if not exist "%SHORTCUT%" (
    powershell -Command "& { $ws = New-Object -ComObject WScript.Shell; $s = $ws.CreateShortcut('%SHORTCUT%'); $s.TargetPath = '%~f0'; $s.WorkingDirectory = '%~dp0'; $s.Description = 'ARIA Autonomous Agent'; $s.Save() }" >nul 2>&1
    echo     Shortcut created on your desktop.
)

:: ── Run ARIA ───────────────────────────────────────────
:run
echo.
echo  🚀 Starting ARIA...
echo.
echo     Server: http://localhost:8000
echo     Health: http://localhost:8000/health/live
echo     Demo:   http://localhost:8000
echo.
echo     Press Ctrl+C to stop ARIA.
echo.

:: Set environment variables for local dev
set "ARIA_STATE_BACKEND=sqlite"
set "ARIA_INTERNAL_TOKEN=local-dev-token"
set "ARIA_AUTONOMOUS_ENABLED=0"
set "ARIA_CODER_ENABLED=0"

:: Start ARIA
cd /d "%ARIA_DIR%\crucix"
"%ARIA_DIR%\python\python.exe" -m uvicorn aria_service.main:app --host 0.0.0.0 --port 8000

:: If ARIA stops, keep the window open
echo.
echo  👋 ARIA has stopped.
pause
