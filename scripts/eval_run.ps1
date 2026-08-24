# scripts/eval_run.ps1 — R-F505 eval seed-load + coverage probe
#
# Usage from the repo root:
#   .\scripts\eval_run.ps1
#
# Reads token from .aria-token (gitignored). No prompts, no pasting.

$ErrorActionPreference = 'Stop'

$tokenPath = Join-Path $PSScriptRoot '..\.aria-token'
if (-not (Test-Path $tokenPath)) {
    Write-Host "ERROR: $tokenPath not found. Create it with the token value." -ForegroundColor Red
    exit 1
}
$t = (Get-Content $tokenPath -Raw).Trim()
if ($t.Length -lt 32) {
    Write-Host "ERROR: token in .aria-token looks too short ($($t.Length) chars). Expected 64." -ForegroundColor Red
    exit 1
}

Write-Host "=== Health probe ===" -ForegroundColor Cyan
curl.exe -s "https://aria-intel.fly.dev/api/aria/health/live" -H "Authorization: Bearer $t"
Write-Host ""

Write-Host "`n=== Eval seed/load (loads R-F505 27 new entries, may take 10-30s) ===" -ForegroundColor Cyan
curl.exe -s -X POST "https://aria-intel.fly.dev/api/aria/eval/seed/load" -H "Authorization: Bearer $t"
Write-Host ""

Write-Host "`n=== Eval coverage (current state vs 500 target) ===" -ForegroundColor Cyan
curl.exe -s "https://aria-intel.fly.dev/api/aria/eval/coverage" -H "Authorization: Bearer $t"
Write-Host ""
