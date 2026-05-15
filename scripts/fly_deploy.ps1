# R-F535 (2026-05-15) — PowerShell wrapper for `flyctl deploy` that bakes
# git sha + R-number into the fly.io image at build time. Operator runs
# this on Windows; the Bash sibling fly_deploy.sh covers WSL / Linux.
#
# Use this instead of bare `flyctl deploy`. The CI workflow already does
# the equivalent; this is for manual deploys. See sibling .sh for the
# full backstory.
#
# Usage:
#   ./scripts/fly_deploy.ps1
#   ./scripts/fly_deploy.ps1 --strategy immediate

$ErrorActionPreference = 'Stop'

Set-Location (Join-Path $PSScriptRoot '..')

$GIT_SHA = (git rev-parse HEAD).Trim()
$subject = (git log -1 --pretty=%s).Trim()
$match = [regex]::Match($subject, 'R-F\d+([+ ]+F\d+)*')
if ($match.Success) {
    $R_TAG = $match.Value -replace ' ', ''
} else {
    $R_TAG = 'no-r-tag'
}

Write-Host "[fly_deploy] GIT_SHA=$GIT_SHA"
Write-Host "[fly_deploy] R_TAG=$R_TAG"
Write-Host "[fly_deploy] forwarding extra args: $args"

& flyctl deploy --remote-only --app aria-intel --wait-timeout 900 `
    --build-arg "ARIA_BUILD_GIT_SHA=$GIT_SHA" `
    --build-arg "ARIA_BUILD_R_TAG=$R_TAG" `
    @args
