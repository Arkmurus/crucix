@echo off
REM R-F1310 aria-forever - self-healing launcher. Runs the ARIA CLI under the
REM supervisor: a crash or stall auto-restarts her with a recovery turn; a
REM clean /exit stops supervision. Use exactly like aria.cmd.
setlocal
set "PYTHONPATH=C:\code\crucix;%PYTHONPATH%"
"C:\code\crucix\.venv\Scripts\python.exe" -m aria_cli.supervisor %*
endlocal
