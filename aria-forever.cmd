@echo off
REM R-F1310 aria-forever - self-healing launcher. Runs the ARIA CLI under the
REM supervisor: a crash or stall auto-restarts her with a recovery turn; a
REM clean /exit stops supervision. Use exactly like aria.cmd.
REM
REM Self-locating via %~dp0 (see aria.cmd). ARIA_HOME overrides when this file
REM is copied to a folder on your PATH.
setlocal
if defined ARIA_HOME (set "ARIA_ROOT=%ARIA_HOME%") else (set "ARIA_ROOT=%~dp0")
if "%ARIA_ROOT:~-1%"=="\" set "ARIA_ROOT=%ARIA_ROOT:~0,-1%"
if not exist "%ARIA_ROOT%\.venv\Scripts\python.exe" (
  echo ARIA venv not found at "%ARIA_ROOT%\.venv\Scripts\python.exe".
  exit /b 1
)
set "PYTHONPATH=%ARIA_ROOT%;%PYTHONPATH%"
"%ARIA_ROOT%\.venv\Scripts\python.exe" -m aria_cli.supervisor %*
endlocal
