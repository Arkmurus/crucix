@echo off
REM R-F988 ARIA Coder launcher (Windows). Runs in the CALLER directory so
REM ARIA edits whatever project you are standing in.
REM
REM Self-locating: %~dp0 is this script's own folder, replacing a hardcoded
REM "C:\code\crucix" that only existed on one machine. If you copy this file to
REM a folder on your PATH (the documented workflow), %~dp0 no longer points at
REM the repo — set ARIA_HOME to the checkout and it wins.
setlocal
if defined ARIA_HOME (set "ARIA_ROOT=%ARIA_HOME%") else (set "ARIA_ROOT=%~dp0")
if "%ARIA_ROOT:~-1%"=="\" set "ARIA_ROOT=%ARIA_ROOT:~0,-1%"
if not exist "%ARIA_ROOT%\.venv\Scripts\python.exe" goto :novenv
set "PYTHONPATH=%ARIA_ROOT%;%PYTHONPATH%"
"%ARIA_ROOT%\.venv\Scripts\python.exe" -m aria_cli %*
endlocal
exit /b %ERRORLEVEL%

:novenv
echo ARIA venv not found at "%ARIA_ROOT%\.venv\Scripts\python.exe".
echo Create it with: python -m venv .venv
echo Then: .venv\Scripts\python.exe -m pip install -r aria_service\requirements.txt
endlocal
exit /b 1
