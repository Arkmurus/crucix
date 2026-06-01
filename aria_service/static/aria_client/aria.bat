@echo off
title ARIA — Autonomous Research Intelligence Agent
cd /d "%~dp0"

:: ─────────────────────────────────────────────────
:: ARIA — Terminal Client
:: ─────────────────────────────────────────────────
:: Just double-click this file. That's it.
:: If you have Python, you get the full experience.
:: If not, it still works — just paste your token.
:: ─────────────────────────────────────────────────

set "SERVER=https://aria-intel.fly.dev"
set "USERNAME=%USERNAME%"

:: ── Step 1: Try Python client (best experience) ────────────────────────
where python.exe >nul 2>nul
if not errorlevel 1 (
    if not exist "aria.py" (
        echo.
        echo   Downloading ARIA Python client...
        powershell -NoProfile -Command "try{$w=New-Object Net.WebClient;$w.DownloadFile('%SERVER%/download/aria.py', 'aria.py');Write-Host '  Done' -ForegroundColor Green}catch{Write-Host '  Download failed - will use fallback mode' -ForegroundColor Yellow}"
    )
    if exist "aria.py" (
        python aria.py %*
        if not errorlevel 1 exit /b 0
        if errorlevel 1 (
            rem Python client had an error — fall through to PowerShell
        )
    )
)

:: ── Step 2: PowerShell fallback ────────────────────────────────────────
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
echo   ║    ██   ██ ██   ██ ██ ██   ██      Just ask me anything               ║
echo   ║                                                                      ║
echo   ╚══════════════════════════════════════════════════════════════════════╝
echo.

:: Check connection
powershell -NoProfile -Command "try{$r=Invoke-WebRequest -Uri '%SERVER%/health/live' -TimeoutSec 5 -UseBasicParsing;$d=$r.Content|ConvertFrom-Json;Write-Host '  ✅ Connected to ARIA server (' $d.build_rev ')' -ForegroundColor Green}catch{Write-Host '  ⚠️  Server unreachable. Check your internet connection.' -ForegroundColor Yellow;Write-Host '     The server is at: %SERVER%' -ForegroundColor Yellow}" 2>nul

:: Check for token
call :check_token

if "%ARIA_API_TOKEN%"=="" (
    echo.
    echo   ─── First-time setup ──────────────────────────────────────
    echo.
    echo   To use ARIA, you need an access token.
    echo.
    echo   Step 1: Open this link in your browser:
    echo     %SERVER%/token
    echo.
    echo   Step 2: Copy the token you see there
    echo.
    echo   Step 3: Paste it below and press Enter
    echo.
    set /p "ARIA_API_TOKEN=Paste your token here: "
    echo.
    if not "%ARIA_API_TOKEN%"=="" (
        rem Save it for this session
        echo   Token saved for this session.
        echo.
    ) else (
        echo   No token entered. Type 'token' at any time to set one.
        echo.
    )
)

echo.
echo   Hello %USERNAME%! I'm ARIA — your research intelligence agent.
echo   Ask me anything: research, analyse code, investigate, or just chat.
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
    echo    token             Set or change your API token
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
    echo   ─── Examples ─────────────────────────────────────────────
    echo.
    echo    "Research Acme Corp and their supply chain"
    echo    "Analyse this code and find bugs: def foo(): pass"
    echo    "What are the latest defence tenders in Europe?"
    echo    "Explain quantum computing in simple terms"
    echo.
    goto loop
)

if /i "%input%"=="status" (
    echo.
    powershell -NoProfile -Command "try{$r=Invoke-WebRequest -Uri '%SERVER%/health/live' -TimeoutSec 5 -UseBasicParsing;$d=$r.Content|ConvertFrom-Json;Write-Host '  🟢 Server: ONLINE' -ForegroundColor Green;Write-Host '  Build: ' $d.build_rev;Write-Host '  Uptime: ' $d.uptime_seconds 's'}catch{Write-Host '  🔴 Server: OFFLINE' -ForegroundColor Red}"
    goto loop
)

if /i "%input%"=="token" (
    echo.
    echo   ─── Set your API token ────────────────────────────────────
    echo.
    echo   Open this link in your browser to get a token:
    echo     %SERVER%/token
    echo.
    set /p "ARIA_API_TOKEN=Paste your token here: "
    echo.
    if not "%ARIA_API_TOKEN%"=="" (
        echo   Token saved for this session.
    ) else (
        echo   No token entered.
    )
    echo.
    goto loop
)

:: ── Check we have a token before sending ────────────────────────────
call :check_token

if "%ARIA_API_TOKEN%"=="" (
    echo.
    echo   ❌ You need an API token first.
    echo.
    echo   Type 'token' to set one up.
    echo.
    goto loop
)

:: ── Send to ARIA ────────────────────────────────────────────────────
echo.
echo   🧠 ARIA is thinking...
echo.

powershell -NoProfile -Command "$body=@{message='%input%';session_id='client_%USERNAME%'}|ConvertTo-Json;try{$r=Invoke-RestMethod -Uri '%SERVER%/api/aria/chat' -Method Post -Body $body -ContentType 'application/json' -TimeoutSec 120 -Headers @{'Authorization'='Bearer %ARIA_API_TOKEN%'};Write-Host '';if($r.response){Write-Host $r.response -ForegroundColor Cyan}elseif($r.answer){Write-Host $r.answer -ForegroundColor Cyan}else{Write-Host ('Response: '+($r|ConvertTo-Json -Depth 1)) -ForegroundColor Cyan};if($r.tool_used){Write-Host '';Write-Host ('  🔧 Used: '+$r.tool_used) -ForegroundColor Yellow};if($r.cached){Write-Host '  💾 Cached response' -ForegroundColor DarkGray}}catch{Write-Host '';Write-Host '  ❌ Error: '$_.Exception.Message -ForegroundColor Red;Write-Host '';if($_.Exception.Response.StatusCode -eq 401){Write-Host '  Your token is invalid. Type: token' -ForegroundColor Yellow}else{Write-Host '  The server may be busy. Try again in a moment.' -ForegroundColor Yellow}}" 2>nul

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

:check_token
if not "%ARIA_API_TOKEN%"=="" goto :eof
if exist "%USERPROFILE%\.aria\config.json" (
    for /f "tokens=2 delims=:," %%a in ('type "%USERPROFILE%\.aria\config.json" ^| findstr "api_token"') do (
        set "ARIA_API_TOKEN=%%~a"
    )
)
goto :eof
