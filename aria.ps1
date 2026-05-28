# R-F988 — ARIA Coder launcher (PowerShell). Runs in the caller's directory.
# Add C:\code\crucix to $env:PATH (or dot-source this) to call `aria` anywhere.
$env:PYTHONPATH = "C:\code\crucix;$env:PYTHONPATH"
& "C:\code\crucix\.venv\Scripts\python.exe" -m aria_cli @args
