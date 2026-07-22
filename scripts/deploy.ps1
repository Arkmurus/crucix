<#
.SYNOPSIS
R-F1150 — ARIA bulletproof deploy script for Windows (PowerShell 5.1 compatible).

Mirrors scripts/deploy.sh exactly: push guard, build_rev verification,
polling, health checks.

Usage:
  .\scripts\deploy.ps1 [-Intel] [-Web] [-Wa] [-All] [-CleanHead]

Examples:
  .\scripts\deploy.ps1 -All          # deploy all three apps
  .\scripts\deploy.ps1 -Intel        # aria-intel only
  .\scripts\deploy.ps1 -Web -Wa      # aria-web + aria-wa only
  .\scripts\deploy.ps1 -Intel -CleanHead   # deploy EXACTLY committed HEAD

-CleanHead (R-F2591): before deploying, stash any uncommitted working-tree
changes so the image is built from committed HEAD ONLY, then restore them
afterwards (always, via finally). Use this to deploy collision-safely when a
parallel agent has uncommitted WIP in the shared tree — it replaces the manual
stash-shield dance. A pop conflict never loses work (changes stay in git stash).

Prereqs: flyctl installed + authenticated, git available.
#>

param(
    [switch]$Intel,
    [switch]$Web,
    [switch]$Wa,
    [switch]$Searxng,
    [switch]$All,
    [switch]$CleanHead
)

$ErrorActionPreference = "Stop"

# Default: --all if no flags given
if (-not $Intel -and -not $Web -and -not $Wa -and -not $Searxng) {
    $All = $true
}
if ($All) {
    $Intel = $true; $Web = $true; $Wa = $true; $Searxng = $true
}

# R-F1163: $PSScriptRoot and $MyInvocation are empty when invoked via
# the run tool. Detect repo root by walking up from the script path.
$SCRIPT_DIR = Split-Path -Parent $PSCommandPath
if (-not $SCRIPT_DIR) { $SCRIPT_DIR = Split-Path -Parent $MyInvocation.MyCommand.Path }
if (-not $SCRIPT_DIR) { $SCRIPT_DIR = "C:\code\crucix" }  # fallback
$REPO_ROOT = Split-Path -Parent $SCRIPT_DIR
Set-Location $REPO_ROOT

$GIT_SHA = git rev-parse HEAD
$GIT_SHORT = git rev-parse --short=8 HEAD

# ---- PUSH GUARD (R-F1122) ----
Write-Host "=== ARIA bulletproof deploy (R-F1150, Windows) ==="
Write-Host "  commit: $GIT_SHA ($GIT_SHORT)"
Write-Host "  apps:   intel=$Intel web=$Web wa=$Wa searxng=$Searxng"
Write-Host ""

$ORIGIN_SHA = git rev-parse origin/main 2>$null
if (-not $ORIGIN_SHA) {
    Write-Host "  [FAIL] Cannot check origin/main (no remote or not fetched). Push manually first."
    Write-Host "         Run: git push origin main"
    exit 1
}
if ($GIT_SHA -ne $ORIGIN_SHA) {
    Write-Host "  [FAIL] PUSH GUARD: HEAD ($GIT_SHORT) != origin/main ($($ORIGIN_SHA.Substring(0,8)))."
    Write-Host "         You committed but did NOT push. The deploy would succeed locally but"
    Write-Host "         origin/main would diverge from what is live - your work would NOT be"
    Write-Host "         backed up on GitHub."
    Write-Host ""
    Write-Host "         Fix: git push origin main"
    Write-Host "         Then re-run this script."
    exit 1
}
Write-Host "  [PASS] push guard: HEAD matches origin/main ($GIT_SHORT)"
Write-Host ""

# ---- R-number tag ----
$LAST_TAG = git tag --list 'deploy-*' --sort=-version:refname | Select-Object -First 1
if ($LAST_TAG) {
    $R_NUMBERS = git log "$LAST_TAG..HEAD" --pretty=%s | Select-String -Pattern 'R-F[0-9]+' -AllMatches | ForEach-Object { $_.Matches.Value } | Sort-Object -Unique
} else {
    $R_NUMBERS = git log --pretty=%s | Select-String -Pattern 'R-F[0-9]+' -AllMatches | ForEach-Object { $_.Matches.Value } | Sort-Object -Unique
}
$R_TAG = if ($R_NUMBERS) { ($R_NUMBERS -join '+') } else { "no-r-tag" }
Write-Host "  r-tags: $R_TAG"
Write-Host ""

# ---- Helper: get current deployed version ----
function Get-CurrentVersion {
    param([string]$App)
    try {
        $raw = flyctl status -a $App --json 2>$null
        if (-not $raw) { return 0 }
        $json = $raw | ConvertFrom-Json
        return [int]$json.Version
    } catch {
        return 0
    }
}

# ---- Helper: get live build_rev for aria-intel ----
function Get-IntelBuildRev {
    try {
        $resp = Invoke-WebRequest -Uri "https://aria-intel.fly.dev/health/live" -TimeoutSec 12 -UseBasicParsing -ErrorAction Stop
        $text = $resp.Content
        $m = [regex]::Match($text, 'sha ([a-f0-9]+)')
        if ($m.Success) {
            return $m.Groups[1].Value
        }
    } catch {}
    return $null
}

# ---- Deploy one app and verify ----
function Deploy-And-Verify {
    param([string]$App, [string]$Config, [int]$TimeoutSeconds)

    Write-Host "--- $App : deploying directly via flyctl ---"
    $preVer = Get-CurrentVersion $App
    Write-Host "  pre-deploy version: $preVer"

    # Build args
    $buildArgs = @(
        "--build-arg", "ARIA_BUILD_GIT_SHA=$GIT_SHA",
        "--build-arg", "ARIA_BUILD_R_TAG=$R_TAG"
    )

    # R-F1179: run flyctl deploy DIRECTLY (not via Start-Process) so the
    # calling tool can track the process and wait for completion. The
    # --wait-timeout flag tells flyctl to block until the build finishes
    # (up to 15 min for cold builds with Playwright Chromium).
    Write-Host "  Running: flyctl deploy --config $Config --app $App --wait-timeout ${TimeoutSeconds}s"
    # R-F1369: pipe flyctl output to the HOST stream. Run bare, flyctl's stdout
    # lands in this function's OUTPUT stream, so the caller's
    # `$result = Deploy-And-Verify ...` captured a truthy array even when the
    # function returned $false — `-not $result` was always $false, a FAILED
    # app never incremented $failures, and the script printed
    # "ALL DEPLOYS VERIFIED LIVE" over a [FAIL] (live incident 2026-06-06:
    # aria-web build died on a DNS blip; the final banner still said ALL PASS).
    flyctl deploy --remote-only --config $Config --app $App --wait-timeout $TimeoutSeconds @buildArgs 2>&1 | ForEach-Object { Write-Host $_ }
    $rc = $LASTEXITCODE
    if ($rc -ne 0) {
        Write-Host "  [WARN] flyctl exited $rc - verifying anyway (it sometimes deploys then errors on wait)"
    }

    # ---- VERIFY: poll up to 5 minutes (cold boot can take 2-3 min) ----
    $ok = $false
    $i = 1
    while ($i -le 60) {
        Start-Sleep -Seconds 5
        $nowVer = Get-CurrentVersion $App
        $versionBumped = ($nowVer -gt $preVer)

        if ($App -eq "aria-intel") {
            $liveSha = Get-IntelBuildRev
            if ($liveSha -eq $GIT_SHORT) {
                Write-Host "  [PASS] $App LIVE - build_rev=$liveSha matches commit (version $nowVer)"
                $ok = $true; break
            }
            $shaDisplay = '?'
            if ($liveSha) { $shaDisplay = $liveSha }
            Write-Host "  poll $i/36: version $preVer->$nowVer, live build_rev=$shaDisplay (want $GIT_SHORT)"
        } else {
            # R-F1330 — verify via a HEALTH endpoint, not the bare root. aria-wa's
            # "/" returns 404 (the listener has no root route), so the old "/" probe
            # ALWAYS false-negatived aria-wa even when the deploy succeeded — causing
            # retry churn and phantom "deploy failed" (the real reason aria-wa deploys
            # looked broken). Verified live 2026-06-04: aria-wa /health=200 (/=404);
            # aria-web /healthz=200. Probe known health paths, accept the first 200.
            $code = 000
            foreach ($hp in @('/health', '/healthz', '/')) {
                try {
                    $code = (Invoke-WebRequest -Uri "https://$App.fly.dev$hp" -TimeoutSec 8 -UseBasicParsing -ErrorAction Stop).StatusCode
                } catch { $code = 000 }
                if ($code -eq 200) { break }
            }
            if ($versionBumped -and $code -eq 200) {
                Write-Host "  [PASS] $App LIVE - version $preVer->$nowVer, HTTP $code"
                $ok = $true; break
            }
            Write-Host "  poll $i/36: version $preVer->$nowVer (bumped=$versionBumped), HTTP $code"
        }
        $i++
    }

    if ($ok) {
        return $true
    }
    Write-Host "  [FAIL] $App NOT VERIFIED LIVE - the server did NOT advance to your commit."
    Write-Host "         Do NOT report this deployed. Re-run, or check 'flyctl logs -a $App'."
    return $false
}

# ---- R-F2591: CleanHead shield — stash dirty tree so the image == committed HEAD ----
$stashed = $false
if ($CleanHead) {
    $dirty = git status --porcelain
    if ($dirty) {
        Write-Host "  [CleanHead] working tree dirty - stashing so the image is built from committed HEAD ($GIT_SHORT) only"
        git stash push -u -m "deploy-cleanhead-$GIT_SHORT" 2>&1 | Select-Object -Last 1 | ForEach-Object { Write-Host "    $_" }
        $stashed = $true
    } else {
        Write-Host "  [CleanHead] working tree already clean - nothing to shield"
    }
    Write-Host ""
}

# ---- Write last deploy SHA ----
Set-Content -Path "$REPO_ROOT/.last_deploy_sha" -Value $GIT_SHORT -NoNewline

$exitCode = 0
try {
    # ---- Execute deploys ----
    # R-F1369: judge ONLY the function's final return value (last element), never
    # the whole output stream — any stray stdout from a helper would otherwise make
    # a $false return look truthy and swallow the failure (see Deploy-And-Verify).
    $failures = 0
    if ($Intel) {
        $result = @(Deploy-And-Verify "aria-intel" "fly.toml" 900)[-1]
        if ($result -ne $true) { $failures++ }
    }
    if ($Web) {
        $result = @(Deploy-And-Verify "aria-web" "fly.web.toml" 600)[-1]
        if ($result -ne $true) { $failures++ }
    }
    if ($Wa) {
        $result = @(Deploy-And-Verify "aria-wa" "fly.wa.toml" 600)[-1]
        if ($result -ne $true) { $failures++ }
    }
    if ($Searxng) {
        $result = @(Deploy-And-Verify "aria-searxng" "searxng/fly.toml" 300)[-1]
        if ($result -ne $true) { $failures++ }
    }

    Write-Host ""
    if ($failures -eq 0) {
        $tagName = "deploy-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
        git tag $tagName $GIT_SHA 2>$null
        if (-not $?) { Write-Host "  [WARN] git tag failed (non-fatal)" }
        Write-Host "=== [PASS] ALL DEPLOYS VERIFIED LIVE (commit $GIT_SHORT is serving) ==="

        # ---- Live health regression suite ----
        Write-Host ""
        Write-Host "=== Running live health regression suite ==="
        # R-F1478: pass the sha THIS deploy actually shipped, so the check verifies the
        # real deployed commit and is immune to a concurrent ci_deploy overwriting
        # .last_deploy_sha mid-deploy (which false-failed every manual deploy).
        #
        # R-F2868: check ONLY the tiers this run deployed. This was `--app all`, so a
        # single-tier deploy asserted the new sha against apps it never touched and
        # printed a red FAIL on a perfectly correct deploy (observed on R-F2867: web
        # went 326->327 and served fine, while intel — still on the previous commit,
        # healthy — failed the sha check). A gate that fails on a good deploy gets
        # ignored, and then it cannot report the real failure it exists to catch.
        $healthApps = @()
        if ($Intel) { $healthApps += 'intel' }
        if ($Web)   { $healthApps += 'web' }
        if ($Wa)    { $healthApps += 'wa' }
        if ($healthApps.Count -eq 0) { $healthApps = @('intel') }
        python "$REPO_ROOT/scripts/live_health_check.py" --app ($healthApps -join ',') --expected-sha $GIT_SHORT
        if ($LASTEXITCODE -ne 0) {
            Write-Host "=== [FAIL] Live health regression suite FAILED - deploy succeeded but health checks failed. ==="
            Write-Host "    Check flyctl logs -a (app) for details."
            $exitCode = 1
        } else {
            Write-Host "=== [PASS] Live health regression suite PASSED ==="
            $exitCode = 0
        }
    } else {
        Write-Host "=== [FAIL] $failures deploy(s) NOT verified live - NOT shipped. Fix + re-run. ==="
        $exitCode = 1
    }
}
finally {
    # R-F2591/R-F2594: ALWAYS restore the shielded WIP, even if a deploy threw.
    if ($stashed) {
        Write-Host ""
        Write-Host "  [CleanHead] restoring stashed working-tree changes..."
        git stash pop 2>&1 | Select-Object -Last 3 | ForEach-Object { Write-Host "    $_" }
        if ($LASTEXITCODE -ne 0) {
            # R-F2594: distinguish a benign untracked-file COLLISION (a parallel
            # agent re-created a stashed untracked file mid-deploy) from a real
            # MERGE conflict. On a pure collision the tracked changes DID apply via
            # 3-way merge and the untracked files already exist in the tree, so the
            # kept stash is fully redundant — drop it, else every deploy under
            # concurrent editing leaks a dead backup stash. Only genuine unmerged
            # paths need manual resolution.
            $mergeConflict = @(git diff --name-only --diff-filter=U 2>$null) | Where-Object { $_ }
            if ($mergeConflict.Count -gt 0) {
                Write-Host "  [CleanHead][WARN] MERGE conflict on pop ($($mergeConflict.Count) file(s)) - resolve manually; stash KEPT as backup:"
                $mergeConflict | Select-Object -First 5 | ForEach-Object { Write-Host "      $_" }
            } else {
                Write-Host "  [CleanHead] pop hit untracked-file collision only (no merge conflict); working tree is complete - dropping redundant backup stash"
                git stash drop 2>&1 | Select-Object -Last 1 | ForEach-Object { Write-Host "    $_" }
            }
        }
    }
}
exit $exitCode
