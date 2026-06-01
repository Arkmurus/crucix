@echo off
title ARIA — Autonomous Research Intelligence Agent
cd /d "%~dp0"

:: ─────────────────────────────────────────────────
:: ARIA — Terminal Client (lightweight)
:: ─────────────────────────────────────────────────
:: Put this folder anywhere on your computer.
:: Open cmd in this folder and type: aria
:: Connected to the main ARIA server — full intelligence.
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
echo   ║     █████  ██████  ██  █████       ARIA v2.1                         ║
echo   ║    ██   ██ ██   ██ ██ ██   ██      Autonomous Research Intelligence  ║
echo   ║    ███████ ██████  ██ ███████      Terminal Client                    ║
echo   ║    ██   ██ ██   ██ ██ ██   ██      Connected to main server          ║
echo   ║    ██   ██ ██   ██ ██ ██   ██      No install needed                 ║
echo   ║                                                                      ║
echo   ╚══════════════════════════════════════════════════════════════════════╝
echo.

:: Check connection
powershell -NoProfile -Command "try{$r=Invoke-WebRequest -Uri '%SERVER%/health/live' -TimeoutSec 5 -UseBasicParsing;$d=$r.Content|ConvertFrom-Json;Write-Host '  ✅ Connected to ARIA server (' $d.build_rev ')' -ForegroundColor Green}catch{Write-Host '  ⚠️  Server unreachable. Check your internet connection.' -ForegroundColor Yellow;Write-Host '     The server is at: %SERVER%' -ForegroundColor Yellow}" 2>nul || (echo   ⚠️  Could not check connection. Make sure you have internet.)

echo.
echo   Hello %USERNAME%. I'm ARIA — your research intelligence agent.
echo   Ask me anything. I research, analyse, investigate, and answer.
echo.
echo   Type 'help' for commands, or just type your question.
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
    echo    setup             Get instructions for API token
    echo    cls               Clear screen
    echo    exit              Quit
    echo.
    echo   ─── What I can do ────────────────────────────────────────
    echo.
    echo    Just type your question. Full ARIA intelligence:
    echo.
    echo    🌐  Research companies, people, and markets
    echo    🔍  Search the web for current information
    echo    📄  Analyse code and find bugs
    echo    📊  Investigate supply chains and procurement
    echo    📋  Review documents and contracts
    echo    💬  Chat about any topic
    echo.
    goto loop
)

if /i "%input%"=="status" (
    echo.
    powershell -NoProfile -Command "try{$r=Invoke-WebRequest -Uri '%SERVER%/health/live' -TimeoutSec 5 -UseBasicParsing;$d=$r.Content|ConvertFrom-Json;Write-Host '  🟢 Server: ONLINE' -ForegroundColor Green;Write-Host '  Build: ' $d.build_rev;Write-Host '  Uptime: ' $d.uptime_seconds 's'}catch{Write-Host '  🔴 Server: OFFLINE' -ForegroundColor Red}"
    goto loop
)

if /i "%input%"=="setup" (
    echo.
    echo   ─── Setup ────────────────────────────────────────────────
    echo.
    echo   To use ARIA, you need an API token.
    echo.
    echo   Option 1: Get a token from the ARIA web interface
    echo     Open https://intel.arkmurus.com in your browser
    echo     Log in or create an account
    echo     Go to Settings -^> API Tokens -^> Create New Token
    echo.
    echo   Option 2: Use the Python setup wizard (recommended)
    where python.exe >nul 2>nul
    if not errorlevel 1 (
        echo     Type: python aria.py --setup
        echo.
    ) else (
        echo     Python is not installed. Install Python 3 from python.org
        echo     then run: python aria.py --setup
        echo.
    )
    echo   Once you have a token, set it as an environment variable:
    echo     set ARIA_API_TOKEN=your_token_here
    echo.
    goto loop
)

:: ── Check for token ────────────────────────────────────────────────
if "%ARIA_API_TOKEN%"=="" (
    if exist "%USERPROFILE%\.aria\config.json" (
        for /f "tokens=2 delims=:," %%a in ('type "%USERPROFILE%\.aria\config.json" ^| findstr "api_token"') do (
            set "ARIA_API_TOKEN=%%~a"
        )
    )
)

if "%ARIA_API_TOKEN%"=="" (
    echo.
    echo   ❌ No API token found.
    echo.
    echo   ARIA requires authentication. You need an API token.
    echo.
    echo   Type 'setup' for instructions on getting a token.
    echo.
    goto loop
)

:: ── Send to real ARIA chat endpoint ──────────────────────────────
echo.
echo   🧠 ARIA is thinking...
echo.

powershell -NoProfile -Command "$body=@{message='%input%';session_id='client_%USERNAME%'}|ConvertTo-Json;try{$r=Invoke-RestMethod -Uri '%SERVER%/api/aria/chat' -Method Post -Body $body -ContentType 'application/json' -TimeoutSec 120 -Headers @{'Authorization'='Bearer %ARIA_API_TOKEN%'};Write-Host '';if($r.response){Write-Host $r.response -ForegroundColor Cyan}elseif($r.answer){Write-Host $r.answer -ForegroundColor Cyan}else{Write-Host ('Response: '+($r|ConvertTo-Json -Depth 1)) -ForegroundColor Cyan};if($r.tool_used){Write-Host '';Write-Host ('  🔧 Used: '+$r.tool_used) -ForegroundColor Yellow};if($r.cached){Write-Host '  💾 Cached response' -ForegroundColor DarkGray}}catch{Write-Host '';Write-Host '  ❌ Error: '$_.Exception.Message -ForegroundColor Red;Write-Host '';if($_.Exception.Response.StatusCode -eq 401){Write-Host '  Authentication failed. Type: setup' -ForegroundColor Yellow}else{Write-Host '  The server may be busy. Try again in a moment.' -ForegroundColor Yellow}}" 2>nul

if errorlevel 1 (
    echo   ⚠️  Request failed. Trying fallback method...
    where curl.exe >nul 2>nul
    if not errorlevel 1 (
        curl.exe -s -X POST "%SERVER%/api/aria/chat" -H "Content-Type: application/json" -H "Authorization: Bearer %ARIA_API_TOKEN%" -d "{\"message\":\"%input%\",\"session_id\":\"client_%USERNAME%\"}" --max-time 120 2>nul || (
            echo   ❌ Could not connect to ARIA server.
            echo      Make sure you have internet access.
            echo      Server: %SERVER%
        )
    ) else (
        echo   ❌ Could not connect to ARIA server.
        echo      Make sure you have internet access.
        echo      Server: %SERVER%
    )
)

goto loop
