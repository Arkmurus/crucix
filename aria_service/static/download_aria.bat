@echo off
title ARIA — Self-Fixing Launcher
cd /d "%~dp0"

:: ─────────────────────────────────────────────────
:: ARIA Self-Fixing Launcher
:: ─────────────────────────────────────────────────
:: This file contains an embedded Python script that
:: ARIA will analyse and fix using her own coder.
:: 
:: How it works:
::   1. Downloads Python + ARIA code
::   2. Extracts the embedded script below
::   3. Runs ARIA's coder on the script
::   4. Shows what ARIA fixed
:: ─────────────────────────────────────────────────

set "ARIA_DIR=%CD%\aria"
set "PYTHON_URL=https://www.python.org/ftp/python/3.13.3/python-3.13.3-embed-amd64.zip"
set "ARIA_URL=https://github.com/Arkmurus/crucix/archive/refs/heads/main.zip"

echo.
echo  ╔══════════════════════════════════════════════╗
echo  ║        ARIA — Self-Fixing Launcher           ║
echo  ║  Download → Analyse → Fix → Show Result      ║
echo  ╚══════════════════════════════════════════════╝
echo.

:: ── Step 1: Check if already installed ─────────────────
if exist "%ARIA_DIR%\python\python.exe" (
    if exist "%ARIA_DIR%\crucix\aria_service\main.py" (
        echo  ✅ ARIA engine ready.
        goto :run
    )
)

:: ── Step 2: Download Python ────────────────────────────
if not exist "%ARIA_DIR%\python\python.exe" (
    echo  📥 Downloading Python 3.13 (embedded, ~30MB)...
    echo     No admin needed — portable Python.
    echo.
    powershell -Command "& { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -Uri '%PYTHON_URL%' -OutFile '%ARIA_DIR%\python.zip' }"
    if errorlevel 1 (
        echo  ❌ Failed to download Python.
        pause
        exit /b 1
    )
    echo  📦 Extracting...
    powershell -Command "& { Expand-Archive -Path '%ARIA_DIR%\python.zip' -DestinationPath '%ARIA_DIR%\python' -Force }"
    del "%ARIA_DIR%\python.zip"
    
    :: Enable pip for embedded Python
    echo. >> "%ARIA_DIR%\python\python._pth"
    echo import site >> "%ARIA_DIR%\python\python._pth"
    
    :: Install pip
    powershell -Command "& { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -Uri 'https://bootstrap.pypa.io/get-pip.py' -OutFile '%ARIA_DIR%\get-pip.py' }"
    "%ARIA_DIR%\python\python.exe" "%ARIA_DIR%\get-pip.py" --user
    del "%ARIA_DIR%\get-pip.py"
    echo  ✅ Python ready.
) else (
    echo  ✅ Python already downloaded.
)

:: ── Step 3: Download ARIA code ─────────────────────────
if not exist "%ARIA_DIR%\crucix\aria_service\main.py" (
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

:: ── Step 4: Install dependencies ───────────────────────
echo  📦 Installing Python dependencies (first time only)...
"%ARIA_DIR%\python\python.exe" -m pip install --user -r "%ARIA_DIR%\crucix\aria_service\requirements.txt" --quiet 2>nul

:: ── Step 5: Extract the embedded script ────────────────
echo.
echo  📄 Extracting script for ARIA to fix...
set "SCRIPT_FILE=%TEMP%\aria_target.py"

:: Find the marker line and extract everything after it
:: Uses LastIndexOf to find the LAST occurrence (the actual separator)
powershell -Command "& $c=Get-Content '%~f0' -Raw; $m='X7K9M2P4_END_OF_BATCH'; $i=$c.LastIndexOf($m); if($i -ge 0){ $s=$c.Substring($i+$m.Length).Trim(); [IO.File]::WriteAllText('%SCRIPT_FILE%',$s); Write-Host 'Script extracted' } else { Write-Host 'Script marker not found'; exit 1 }"
if errorlevel 1 (
    echo  ❌ Failed to extract script.
    pause
    exit /b 1
)

:: ── Step 6: Run ARIA coder on the script ──────────────
:run
echo.
echo  🧠 ARIA is analysing the script...
echo     (This uses ARIA's autonomous AST engine — no internet needed)
echo.

cd /d "%ARIA_DIR%\crucix"

:: Set environment for local use
set "ARIA_STATE_BACKEND=sqlite"
set "ARIA_INTERNAL_TOKEN=local-dev-token"
set "ARIA_AUTONOMOUS_ENABLED=0"
set "ARIA_CODER_ENABLED=0"

:: Run ARIA's coder directly on the target script
"%ARIA_DIR%\python\python.exe" -c "
import sys, json, ast
sys.path.insert(0, 'aria_service')

from intel.autonomous_coder import AutonomousCoder
from autonomous.gap_detector import Gap, GapType, GapSeverity

# Read the target script
with open(r'%SCRIPT_FILE%', 'r') as f:
    code = f.read()

print()
print('=' * 60)
print('  ARIA CODER — ANALYSING SCRIPT')
print('=' * 60)
print()

# Show the original code
print('📄 Original script:')
print('-' * 40)
for i, line in enumerate(code.split(chr(10)), 1):
    print(f'{i:4d}: {line}')
print('-' * 40)
print()

# Run ARIA's coder on it
import asyncio

async def fix_it():
    coder = AutonomousCoder()
    
    # Detect what needs fixing
    fixes = []
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        fixes.append(('Syntax Error', str(e)))
    
    # Check for missing error handling
    has_try = 'try:' in code
    if not has_try:
        fixes.append(('Missing Error Handling', 'No try/except block found'))
    
    # Check for missing logging
    has_logging = 'logger.' in code or 'logging.' in code
    if not has_logging:
        fixes.append(('Missing Logging', 'No logging statements found'))
    
    # Check for bare excepts
    if 'except:' in code and 'except Exception' not in code:
        fixes.append(('Bare Except', 'Use \"except Exception\" instead of bare except'))
    
    # Check for missing docstrings
    has_docstring = '\"\"\"' in code
    if not has_docstring:
        fixes.append(('Missing Docstring', 'No module docstring found'))
    
    if not fixes:
        print('✅ ARIA found no issues with this script!')
        return
    
    print(f'🔍 ARIA found {len(fixes)} issue(s):')
    print()
    for i, (issue, detail) in enumerate(fixes, 1):
        print(f'  {i}. {issue}')
        print(f'     {detail}')
    print()
    
    # Apply fixes
    print('🔧 ARIA is fixing...')
    print()
    
    for issue, detail in fixes:
        gap = Gap(
            gap_id=f'fix_{issue.lower().replace(\" \", \"_\")}',
            gap_type=GapType.MODULE_BUG,
            severity=GapSeverity.MEDIUM,
            title=issue,
            description=detail,
            module='aria_target',
        )
        plan = await coder.generate_fix_plan(gap, code)
        result = await coder.write_code(plan, code, 'aria_target.py')
        if result.get('code') and result['code'] != code:
            print(f'  ✅ Fixed: {issue}')
            code = result['code']
        else:
            print(f'  ⚠️  Could not auto-fix: {issue}')
    
    # Show the fixed code
    print()
    print('📄 Fixed script:')
    print('-' * 40)
    for i, line in enumerate(code.split(chr(10)), 1):
        print(f'{i:4d}: {line}')
    print('-' * 40)
    print()
    print('✅ ARIA analysis complete!')
    print()

asyncio.run(fix_it())
"

echo.
echo  ╔══════════════════════════════════════════════╗
echo  ║        ARIA is ready on your computer        ║
echo  ║                                              ║
echo  ║  Open http://localhost:8000 in your browser  ║
echo  ║  to use the full ARIA demo + coder           ║
echo  ╚══════════════════════════════════════════════╝
echo.
echo  Press any key to start ARIA server, or close this window.
pause >nul

:: ── Step 7: Start ARIA server ─────────────────────────
echo.
echo  🚀 Starting ARIA server...
echo     http://localhost:8000
echo     Press Ctrl+C to stop
echo.

"%ARIA_DIR%\python\python.exe" -m uvicorn aria_service.main:app --host 0.0.0.0 --port 8000

echo.
echo  👋 ARIA stopped.
pause
exit /b 0

:: ─────────────────────────────────────────────────
:: Embedded Python script — ARIA will fix this code
:: The script starts after the marker line below.
:: ─────────────────────────────────────────────────
X7K9M2P4_END_OF_BATCH
def process_data(items):
    result = []
    for item in items:
        value = item["price"] * item["quantity"]
        result.append(value)
    return result

def lookup_user(user_id):
    data = database.get(user_id)
    return data["name"]

def save_report(report):
    file = open(report["filename"], "w")
    file.write(report["content"])
    file.close()
