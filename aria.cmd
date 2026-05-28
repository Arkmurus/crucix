@echo off
REM R-F988 ARIA Coder launcher (Windows). Runs in the CALLER directory so
REM ARIA edits whatever project you are standing in. Copy this file to a
REM folder on your PATH (or add C:\code\crucix to PATH) to call aria anywhere.
setlocal
set "PYTHONPATH=C:\code\crucix;%PYTHONPATH%"
"C:\code\crucix\.venv\Scripts\python.exe" -m aria_cli %*
endlocal
