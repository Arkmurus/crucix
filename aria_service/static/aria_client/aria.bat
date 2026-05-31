@echo off
title ARIA Client
cd /d "%~dp0"

:: ─────────────────────────────────────────────────
:: ARIA Client — connects to the main ARIA server
:: ─────────────────────────────────────────────────
:: Type 'aria' in cmd to use ARIA's coder.
:: No Python, no install, no downloads.
:: Everything runs on the main server.
:: ─────────────────────────────────────────────────

set "SERVER=https://aria-intel.fly.dev"

echo.
echo  ╔══════════════════════════════════════════════╗
echo  ║              ARIA Terminal Client            ║
echo  ║  Connected to %SERVER%      ║
echo  ╚══════════════════════════════════════════════╝
echo.
echo  Type a description of a code problem below.
echo  ARIA will analyse it and generate a fix.
echo.
echo  Examples:
echo    Add error handling to process_item
echo    Fix AttributeError when data is None
echo    Add retry logic for flaky API calls
echo.
echo  Type 'exit' to quit.
echo.

:loop
echo.
set /p "input=aria^> "

if /i "%input%"=="exit" goto :eof
if /i "%input%"=="quit" goto :eof
if "%input%"=="" goto :loop

echo.
echo  🧠 ARIA is analysing...
echo.

:: Call the main server's demo endpoint
powershell -Command "& {
    $body = @{description='%input%'} | ConvertTo-Json;
    try {
        $resp = Invoke-RestMethod -Uri '%SERVER%/api/aria/coder/demo' -Method Post -Body $body -ContentType 'application/json' -TimeoutSec 30;
        Write-Host '';
        Write-Host '📋 Plan:' -ForegroundColor Cyan;
        Write-Host '  Title: ' $resp.plan.title;
        Write-Host '  Risk:  ' $resp.plan.risk_level;
        Write-Host '';
        Write-Host '📄 Generated Code:' -ForegroundColor Green;
        Write-Host '----------------------------------------';
        Write-Host $resp.code;
        Write-Host '----------------------------------------';
    } catch {
        Write-Host '';
        Write-Host '❌ Error: ' $_.Exception.Message -ForegroundColor Red;
    }
}"

goto :loop
