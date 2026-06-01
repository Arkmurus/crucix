@echo off
title ARIA — Autonomous Research Intelligence Agent
cd /d "%~dp0"

:: ─────────────────────────────────────────────────
:: ARIA — Interactive Terminal
:: ─────────────────────────────────────────────────
:: Type 'aria' in cmd. Talk to me. I'll fix your code.
:: ─────────────────────────────────────────────────

set "SERVER=https://aria-intel.fly.dev"
set "USERNAME=%USERNAME%"

mode con: cols=90 lines=40
color 0A

:start
cls
echo.
echo   ╔══════════════════════════════════════════════════════════════════════╗
echo   ║                                                                      ║
echo   ║     █████  ██████  ██  █████       ARIA v2.0                         ║
echo   ║    ██   ██ ██   ██ ██ ██   ██      Autonomous Research Intelligence  ║
echo   ║    ███████ ██████  ██ ███████      Terminal Client                    ║
echo   ║    ██   ██ ██   ██ ██ ██   ██      Connected to main server          ║
echo   ║    ██   ██ ██   ██ ██ ██   ██      Type 'help' for commands          ║
echo   ║                                                                      ║
echo   ╚══════════════════════════════════════════════════════════════════════╝
echo.

:: Check connection (try PowerShell first, fall back to curl)
powershell -Command "& {
    try {
        $r = Invoke-WebRequest -Uri '%SERVER%/health/live' -TimeoutSec 5 -UseBasicParsing;
        $d = $r.Content | ConvertFrom-Json;
        Write-Host '  ✅ Connected to ARIA server  (' $d.build_rev ')' -ForegroundColor Green;
    } catch {
        Write-Host '  ⚠️  Server unreachable.' -ForegroundColor Yellow;
    }
}" 2>nul || (
    echo   ⚠️  Could not check connection. Make sure you have internet.
)

echo.
echo   Hello %USERNAME%. I'm ARIA. I find and fix bugs in code.
echo   Tell me what you need help with.
echo.

:loop
echo.
set /p "input=aria@%USERNAME%^> "

if /i "%input%"=="exit" goto :eof
if /i "%input%"=="quit" goto :eof
if /i "%input%"=="cls" goto start

if /i "%input%"=="help" (
    echo.
    echo   ─── Commands ─────────────────────────────────────────────
    echo.
    echo    help              Show this help
    echo    status            Check ARIA server status
    echo    fix ^<description^>  Describe a bug, I'll fix it
    echo    code ^<code^>       Show me code, I'll analyse it
    echo    cls               Clear screen
    echo    exit              Quit
    echo.
    echo   ─── Examples ─────────────────────────────────────────────
    echo.
    echo    fix Add error handling to process_item
    echo    fix Fix AttributeError when data is None
    echo    fix Add retry logic for flaky API calls
    echo.
    goto loop
)

if /i "%input%"=="status" (
    echo.
    powershell -Command "& {
        try {
            $r = Invoke-WebRequest -Uri '%SERVER%/health/live' -TimeoutSec 5 -UseBasicParsing;
            $d = $r.Content | ConvertFrom-Json;
            Write-Host '  🟢 Server: ONLINE' -ForegroundColor Green;
            Write-Host '  Build:  ' $d.build_rev;
        } catch {
            Write-Host '  🔴 Server: OFFLINE' -ForegroundColor Red;
        }
    }"
    goto loop
)

if /i "%input:~0,3%"=="fix" (
    set "desc=%input:~4%"
    if "!desc!"=="" (
        echo   Tell me what to fix. Example: fix Add error handling
        goto loop
    )
    goto :fix_code
)

if /i "%input:~0,4%"=="code" (
    echo.
    echo   Paste your code below. Type 'done' on its own line when finished.
    echo.
    set "code_block="
    :read_code
    set /p "code_line=>  "
    if /i "!code_line!"=="done" goto :analyse_code
    set "code_block=!code_block!!code_line!\n"
    goto :read_code
)

:: Default: send as chat message
echo.
echo   🧠 Thinking...
powershell -Command "& {
    $body = @{message='%input%'; user='%USERNAME%'} | ConvertTo-Json;
    try {
        $r = Invoke-RestMethod -Uri '%SERVER%/api/aria/client/chat' -Method Post -Body $body -ContentType 'application/json' -TimeoutSec 30;
        Write-Host '';
        Write-Host $r.response -ForegroundColor Cyan;
    } catch {
        Write-Host '';
        Write-Host '  ❌ ' $_.Exception.Message -ForegroundColor Red;
    }
}"
goto loop

:fix_code
echo.
echo   🔍 Analysing: %desc%
echo.
set "FIX_RESULT="
powershell -Command "& {
    $body = @{description='%desc%'} | ConvertTo-Json;
    try {
        $r = Invoke-RestMethod -Uri '%SERVER%/api/aria/coder/demo' -Method Post -Body $body -ContentType 'application/json' -TimeoutSec 30;
        Write-Host '';
        Write-Host '  📋 Plan' -ForegroundColor Cyan;
        Write-Host '  ────────────────────────────────────────────';
        Write-Host '  Title: ' $r.plan.title;
        Write-Host '  Risk:  ' $r.plan.risk_level;
        if ($r.plan.approach) { Write-Host '  Approach: ' $r.plan.approach; }
        Write-Host '';
        Write-Host '  📄 Generated Code' -ForegroundColor Green;
        Write-Host '  ────────────────────────────────────────────';
        $r.code;
        Write-Host '  ────────────────────────────────────────────';
    } catch {
        Write-Host '';
        Write-Host '  ❌ Error: ' $_.Exception.Message -ForegroundColor Red;
    }
}" 2>nul
if errorlevel 1 (
    echo   ⚠️  PowerShell failed. Trying curl.exe...
    curl.exe -s -X POST "%SERVER%/api/aria/coder/demo" -H "Content-Type: application/json" -d "{\"description\":\"%desc%\"}" 2>nul || (
        echo   ❌ Could not connect to ARIA server.
        echo      Make sure you have internet access.
    )
)
goto loop

:analyse_code
echo.
echo   🔍 Analysing code...
powershell -Command "& {
    $body = @{code='%code_block%'} | ConvertTo-Json;
    try {
        $r = Invoke-RestMethod -Uri '%SERVER%/api/aria/client/analyse' -Method Post -Body $body -ContentType 'application/json' -TimeoutSec 30;
        Write-Host '';
        Write-Host '  📊 Analysis' -ForegroundColor Cyan;
        Write-Host '  ────────────────────────────────────────────';
        $r.analysis;
        Write-Host '';
        if ($r.fixes) {
            Write-Host '  🔧 Suggested Fixes' -ForegroundColor Green;
            Write-Host '  ────────────────────────────────────────────';
            $r.fixes;
        }
    } catch {
        Write-Host '';
        Write-Host '  ❌ ' $_.Exception.Message -ForegroundColor Red;
    }
}"
goto loop
