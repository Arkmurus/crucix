# R-F988 — ARIA Coder launcher (PowerShell). Runs in the caller's directory.
#
# Self-locating: resolves the repo from $PSScriptRoot rather than a hardcoded
# "C:\code\crucix", which only ever existed on one machine and broke outright
# when the checkout moved. If you copy this file to a folder on your PATH (the
# documented workflow), $PSScriptRoot no longer points at the repo — set
# ARIA_HOME to the checkout and it wins.
$AriaHome = if ($env:ARIA_HOME) { $env:ARIA_HOME } else { $PSScriptRoot }
$Py = Join-Path $AriaHome ".venv\Scripts\python.exe"
if (-not (Test-Path $Py)) {
    Write-Error "ARIA venv not found at $Py. Create it with: python -m venv .venv; .venv\Scripts\python.exe -m pip install -r aria_service\requirements.txt"
    exit 1
}
$env:PYTHONPATH = "$AriaHome;$env:PYTHONPATH"
& $Py -m aria_cli @args
