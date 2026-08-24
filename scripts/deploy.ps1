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

# ---------------------------------------------------------------------------
# R-F3133: run a NATIVE command without Windows PowerShell 5.1 aborting the script.
#
# THE DEFECT (live 2026-07-26, operator's own shell): `.\scripts\deploy.ps1 -Intel`
# died at deploy.ps1:187 with NativeCommandError on flyctl's FIRST line of output
# ("==> Verifying app config"). No Fly release was ever created — the app stayed on
# v2661 — while the operator reasonably read the coloured error as "the build failed".
#
# THE MECHANISM: under Windows PowerShell 5.1, `2>&1` on a native command wraps each
# stderr line in an ErrorRecord. With the script-level $ErrorActionPreference='Stop'
# above, the FIRST such record is a TERMINATING error. flyctl writes ALL of its
# progress to stderr, so the redirection that exists to CAPTURE flyctl's output was
# the very thing killing the script. PowerShell 7 emits plain strings instead, which
# is why the identical script deploys fine from pwsh: the difference is the shell the
# operator launched, not the code — so this reproduces only on their path (§23).
#
# Every native call is affected, not just flyctl. The `finally` restore at the foot of
# this script pipes `git stash pop 2>&1`; throwing THERE would leave the operator's
# shielded WIP stashed while the console showed an unrelated error — the same shape as
# the R-F3122 false ship-mark.
#
# Success is judged by the EXIT CODE ($script:NativeExitCode), never by $? or by
# whether anything reached stderr. Output is returned as plain strings so the caller
# pipes it to the HOST deliberately — preserving R-F1369, which found that letting
# flyctl's output fall into a function's OUTPUT stream made `$result = Deploy-And-Verify`
# truthy even on a FAILED deploy and printed "ALL DEPLOYS VERIFIED LIVE" over a [FAIL].
$script:NativeExitCode = 0
function Invoke-Native {
    param([Parameter(Mandatory = $true)][scriptblock]$Command)

    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & $Command 2>&1 | ForEach-Object { [string]$_ }
        $script:NativeExitCode = $LASTEXITCODE
    } finally {
        # Restore immediately: 'Stop' is correct for the CMDLET logic around these calls.
        $ErrorActionPreference = $prevEAP
    }
}

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
if ($SCRIPT_DIR) {
    $REPO_ROOT = Split-Path -Parent $SCRIPT_DIR
} else {
    # Last resort: ask git for the checkout root. The previous fallback hardcoded
    # "the repo root" — machine-specific AND wrong, because the line below takes
    # its PARENT, so it resolved to "C:\code" rather than to a repo root at all.
    $REPO_ROOT = (& git rev-parse --show-toplevel) -replace '/', '\'
}
if (-not $REPO_ROOT) { throw "deploy.ps1: cannot determine repo root" }
Set-Location $REPO_ROOT

$GIT_SHA = git rev-parse HEAD
$GIT_SHORT = git rev-parse --short=8 HEAD

# ---- WORKTREE GUARD (R-F3205) ----
#
# A git worktree CANNOT build the aria-intel image, and the failure is silent and
# expensive: `.dockerignore` (R-F589) un-ignores .git/HEAD, .git/refs and
# .git/packed-refs so main.py's `_resolve_git_head_from_image` can derive build_rev
# at runtime when a deploy was invoked WITHOUT --build-arg. In a worktree `.git` is a
# FILE ("gitdir: .../.git/worktrees/<name>"), so `.git/refs` does not exist and
# aria_service/Dockerfile:156 `COPY .git/refs` dies at cache-key computation:
#
#     failed to compute cache key: "/.git/refs": not found
#
# VERIFIED 2026-07-27 on a scratch worktree: .git is a file, .git/refs absent, and
# the refs actually live in `git rev-parse --git-common-dir`, not the worktree gitdir.
#
# Nothing reaches Fly on that failure  - the script then polls up to 36 times for a
# version that will never change, so the operator sees a five-minute hang and a
# timeout rather than the one-line cause. Refuse immediately with the fix instead.
#
# This is a GUARD, not the cure. The cure is to stop baking git internals into the
# image (pass build_rev in and remove the .dockerignore exceptions), which is a change
# to the build path for every app and every CI deploy  - deliberately not attempted
# here. Until then: work in a worktree, deploy from the primary checkout.
$_gitPath = Join-Path $REPO_ROOT ".git"
if (Test-Path $_gitPath -PathType Leaf) {
    $_mainTree = (git rev-parse --path-format=absolute --git-common-dir 2>$null)
    if ($_mainTree) { $_mainTree = Split-Path -Parent $_mainTree }
    Write-Host "=== [FAIL] WORKTREE: this checkout cannot build the image (R-F3205) ===" -ForegroundColor Red
    Write-Host "  $REPO_ROOT is a git WORKTREE  - .git is a file, so .git/refs does not"
    Write-Host "  exist and 'COPY .git/refs' (aria_service/Dockerfile) fails at build time."
    Write-Host "  Nothing would reach Fly; the deploy would hang polling for a version"
    Write-Host "  that never changes."
    Write-Host ""
    Write-Host "  Deploy from the primary checkout instead:"
    if ($_mainTree) { Write-Host "    cd $_mainTree" }
    Write-Host "    git fetch origin; git merge --ff-only origin/main"
    # Rebuild the flag list explicitly. An inline -replace inside an interpolated
    # string parses under pwsh 7 but NOT under Windows PowerShell 5.1 (the operator's
    # shell)  - the same 5.1-only class R-F3133 was about.
    $_flags = @()
    if ($Intel)   { $_flags += "-Intel" }
    if ($Web)     { $_flags += "-Web" }
    if ($Wa)      { $_flags += "-Wa" }
    if ($Searxng) { $_flags += "-Searxng" }
    if ($CleanHead) { $_flags += "-CleanHead" }
    Write-Host ("    .\scripts\deploy.ps1 " + ($_flags -join " "))
    Write-Host ""
    Write-Host "  (Worktrees remain the right way to WORK in parallel  - only the deploy"
    Write-Host "   has to run from the primary checkout.)"
    exit 1
}

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

# ---- TREE INTEGRITY GATE (R-F2919) ----
# The image is built from the WORKING TREE, so a tracked file that is missing from
# disk is silently ABSENT from the image. The push guard proves HEAD matches
# origin/main; it says nothing about whether the files are actually there.
#
# On 2026-07-23 this shipped a broken image: Kaspersky quarantined
# aria_service/static/aria_client/aria.bat out of a fresh clone ~10s after checkout,
# the deploy proceeded, and /static/aria_client/aria.bat served 404 in production
# while every other check passed. The behaviour is INTERMITTENT — a later clone was
# clean — which is exactly why this cannot be left to an antivirus setting or to
# remembering which directory is excluded. `git ls-files -d` answers "is anything
# missing?" in milliseconds and does not care why.
$MISSING = @(git ls-files -d)
if ($MISSING.Count -gt 0) {
    Write-Host "  [FAIL] TREE INTEGRITY: $($MISSING.Count) tracked file(s) MISSING from the working tree."
    Write-Host "         The image is built from this tree, so these would be absent in production:"
    foreach ($m in $MISSING | Select-Object -First 20) { Write-Host "           - $m" }
    if ($MISSING.Count -gt 20) { Write-Host "           ... and $($MISSING.Count - 20) more" }
    Write-Host ""
    Write-Host "         Most likely cause: antivirus quarantine (seen with .bat/.cmd/.ps1 in"
    Write-Host "         freshly-cloned trees). Restore and re-verify before deploying:"
    Write-Host "           git checkout HEAD -- <path>   # then confirm it is still there"
    Write-Host "         If it vanishes again, deploy from an AV-excluded checkout."
    exit 1
}
Write-Host "  [PASS] tree integrity: no tracked file is missing ($(@(git ls-files).Count) tracked)"
Write-Host ""

# ---- R-number tag ----
# R-F3247 - the banner is a CLAIM ABOUT WHAT IS IN THE BUILD, so it may not
# name R-numbers that never shipped. Two measured defects, both live today:
#
#   OVER-CLAIM. The scan matches any 'R-F<n>' in a commit SUBJECT, so the
#   bookkeeping commit "chore: reserve R-F3226..R-F3229" put R-F3226 and
#   R-F3229 in the banner although only their RESERVATIONS were committed -
#   and it missed 3227/3228, which the range only implies. The live banner
#   read "...+R-F3229 - sha 4598730c" for a build that did not contain it.
#
#   UNDER-CLAIM. When the newest deploy-* tag is at or ahead of HEAD (a peer
#   deploys, or two deploys share a commit) the range is empty and this
#   rendered "no-r-tag" - which reads as "this build ships nothing", on a
#   build containing everything. Observed live as "no-r-tag - sha 30cd35ca".
#
# Registry bookkeeping is excluded by SUBJECT PREFIX, narrowly: a 'chore:' that
# does real work still counts, and 'test:'/'docs:' R-numbers are real shipped
# changes (e.g. "test: R-F3236 - fix blocking-dialog string false positive").
# R-F3357 — pick the newest deploy tag that is STRICTLY BEHIND HEAD.
#
# R-F3247 named the UNDER-CLAIM above but only renamed its symptom: "no-r-tag"
# became "no-new-r-numbers", which reads the same way ("this build ships
# nothing") on a build containing everything. The CONDITION was untouched, and
# its own guard asserts the new string is present rather than that the banner
# names the build's contents — a wording assertion, so the rename passed as a fix.
#
# Reproduced live 2026-07-28: deploying intel then web from one commit tagged
# HEAD during the FIRST deploy, so the second saw `$LAST_TAG..HEAD` empty and
# aria-web served "521e32d2ce03 - no-new-r-numbers" while actually shipping
# R-F3351 and R-F3352. Anyone probing /api/health to learn what is live on web
# would have been told nothing shipped.
#
# Taking the newest tag with at least one commit before HEAD reports what this
# build introduced since the last DISTINCT deploy point, which is the question
# the banner exists to answer.
$LAST_TAG = $null
foreach ($_t in @(git tag --list 'deploy-*' --sort=-version:refname)) {
    if ([int](git rev-list --count "$_t..HEAD") -gt 0) { $LAST_TAG = $_t; break }
}
# R-F3371 — the banner states what the build CONTAINS, so a commit contributes
# its OWN R-number and only if it actually ships something. Two over-claims were
# measured live on 2026-07-28, both from taking every R-number in every subject:
#
#   MENTIONED-NOT-SHIPPED. "fix: R-F3365 - wedge #5: R-F3347 fixed one lifespan
#   entry, not the class" put R-F3347 in the banner. R-F3347 shipped days
#   earlier; the commit only cites it. Subjects here are "<type>: R-F#### - ...",
#   so the FIRST R-number is the one the commit ships and the rest are prose.
#
#   SHIPS-NOTHING. "docs: R-F3368 - record the measured suite baseline" put
#   R-F3368 in the banner for a commit touching only docs/, CLAUDE.md and the
#   R-number registry — none of which is in the image. Session records did the
#   same, re-announcing R-numbers that were already live.
#
# R-F3247 removed the reservation-commit case and R-F3357 the empty-range case;
# this is the same family, and the same rule underneath: the banner is a claim
# about the BUILD, not a summary of what people wrote in commit messages.
$_RANGE = if ($LAST_TAG) { "$LAST_TAG..HEAD" } else { "HEAD" }
$_SHIPS_NOTHING = '^(docs/|memory/|[^/]*\.md$|data/r_number_reservations\.json$)'
$_OWN = @()
foreach ($_c in @(git log $_RANGE --pretty=%H)) {
    $_subj = (git log -1 --pretty=%s $_c)
    if ($_subj -match '^chore:\s*(reserve|mark|ship)') { continue }   # R-F3247
    $_files = @(git diff-tree --no-commit-id --name-only -r $_c)
    if (-not ($_files | Where-Object { $_ -notmatch $_SHIPS_NOTHING })) { continue }
    $_m = [regex]::Match($_subj, 'R-F[0-9]+')
    if ($_m.Success) { $_OWN += $_m.Value }
}
$R_NUMBERS = $_OWN | Sort-Object -Unique
$R_TAG = if ($R_NUMBERS) { ($R_NUMBERS -join '+') }
         elseif ($LAST_TAG) { "no-new-r-numbers" }
         else { "no-r-tag" }
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

# ---- R-F2900: get live build_rev for a NODE app (aria-web / aria-wa) ----
# These serve it from /api/health as "<sha> · <r-tags>". Returns $null when the
# app exposes no build_rev, which the caller treats as "weaker check only".
function Get-NodeBuildRev {
    param([string]$App)
    foreach ($hp in @('/api/health', '/healthz', '/health')) {
        try {
            $resp = Invoke-WebRequest -Uri "https://$App.fly.dev$hp" -TimeoutSec 8 -UseBasicParsing -ErrorAction Stop
            $m = [regex]::Match($resp.Content, '"build_rev"\s*:\s*"([a-f0-9]+)')
            if ($m.Success) { return $m.Groups[1].Value }
        } catch {}
    }
    return $null
}

# ---- R-F3299: does the LIVE sha CONTAIN our commit? ----
# The exact-sha test reports [FAIL] whenever a peer ships a commit that INCLUDES
# ours: our code is serving, the sha on the wire is theirs, and the poll runs its
# full 5 minutes before printing red on a deploy that actually succeeded. This is
# the shared deploy path, so it cry-wolfs for every agent and every operator, and
# a deploy check people have learned to ignore is worse than no check at all.
# Same root as R-F1478, which race-proofed the post-deploy health check by taking
# an --expected-sha instead of trusting a mutable file.
#
# Ancestry is the honest test: our commit is live iff it is an ancestor of what is
# serving. The property that must NOT be weakened is that an UNRELATED sha still
# fails, and it does: ancestry is a real containment check, not a looser match.
function Test-LiveShaContainsHead {
    param([string]$LiveSha)
    if (-not $LiveSha) { return $false }

    # Ancestry can only be judged for an object we actually hold. An unknown sha
    # is NOT a pass: "cannot verify" and "verified" are different answers, and
    # conflating them is how a failed deploy gets reported as shipped.
    # Piped to Out-Null deliberately (R-F1369): Invoke-Native emits to the OUTPUT
    # stream, and anything left there would be returned as this function's value,
    # making it truthy regardless of the answer.
    Invoke-Native { git cat-file -e "$LiveSha^{commit}" } | Out-Null
    if ($script:NativeExitCode -ne 0) {
        Invoke-Native { git fetch origin --quiet } | Out-Null
        Invoke-Native { git cat-file -e "$LiveSha^{commit}" } | Out-Null
        if ($script:NativeExitCode -ne 0) { return $false }
    }

    Invoke-Native { git merge-base --is-ancestor $GIT_SHA $LiveSha } | Out-Null
    return ($script:NativeExitCode -eq 0)
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
    # R-F3133: via Invoke-Native so flyctl's stderr progress cannot terminate the script
    # under Windows PowerShell 5.1. Still piped to Write-Host, never to the output stream.
    Invoke-Native { flyctl deploy --remote-only --config $Config --app $App --wait-timeout $TimeoutSeconds @buildArgs } | ForEach-Object { Write-Host $_ }
    $rc = $script:NativeExitCode
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
            # R-F3299: a peer's commit that CONTAINS ours means our code is live.
            if (Test-LiveShaContainsHead $liveSha) {
                Write-Host "  [PASS-ANCESTOR] $App LIVE - serving $liveSha, which CONTAINS your commit $GIT_SHORT."
                Write-Host "                  A peer deployed past you. Your code IS live; the exact sha is not."
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
            # R-F2900 — a version bump is NOT proof YOUR commit shipped. When a
            # parallel agent deploys mid-poll (ARIA's own ci_deploy does), the
            # version bumps and this reported [PASS] while the server ran THEIR
            # build — observed live 2026-07-23: a failed aria-web build was
            # declared deployed because v337->338 landed from another deploy.
            # aria-intel has been immune since R-F1478 because it matches
            # build_rev; do the same here whenever the app exposes one.
            $liveRev = Get-NodeBuildRev $App
            if ($liveRev) {
                # Prefix compare, NOT -eq: the node apps publish a 12-char sha
                # ("7f387e7e031e") while $GIT_SHORT is --short=8. An equality
                # test here never matches and polls to a false FAIL.
                if ($liveRev.StartsWith($GIT_SHORT) -and $code -eq 200) {
                    Write-Host "  [PASS] $App LIVE - build_rev=$liveRev matches commit (version $nowVer)"
                    $ok = $true; break
                }
                # R-F3299: same containment rule for the node apps. HTTP 200 is
                # still required, so a healthy-but-older server cannot pass.
                if (($code -eq 200) -and (Test-LiveShaContainsHead $liveRev)) {
                    Write-Host "  [PASS-ANCESTOR] $App LIVE - serving $liveRev, which CONTAINS your commit $GIT_SHORT."
                    Write-Host "                  A peer deployed past you. Your code IS live; the exact sha is not."
                    $ok = $true; break
                }
                Write-Host "  poll $i/36: version $preVer->$nowVer, live build_rev=$liveRev (want $GIT_SHORT), HTTP $code"
            } else {
                # No build_rev surface on this app — fall back to the weaker
                # bump+200 check, but SAY so, so a green line is not read as
                # commit-level proof.
                if ($versionBumped -and $code -eq 200) {
                    Write-Host "  [PASS-WEAK] $App version $preVer->$nowVer, HTTP $code (no build_rev endpoint - NOT commit-verified)"
                    $ok = $true; break
                }
                Write-Host "  poll $i/36: version $preVer->$nowVer (bumped=$versionBumped), HTTP $code, no build_rev"
            }
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
        Invoke-Native { git stash push -u -m "deploy-cleanhead-$GIT_SHORT" } | Select-Object -Last 1 | ForEach-Object { Write-Host "    $_" }
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
        # R-F3133: `$?` was the wrong test even before the 5.1 abort — it reflects the
        # last pipeline's success flag, not git's exit code. Judge by the exit code.
        Invoke-Native { git tag $tagName $GIT_SHA } | Out-Null
        if ($script:NativeExitCode -ne 0) { Write-Host "  [WARN] git tag failed (non-fatal)" }
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
        # R-F3133: Python's logging writes to stderr, so an un-wrapped call here would
        # abort the script under 5.1 AFTER a successful deploy — losing the health
        # verdict and the CleanHead restore below.
        $healthPython = Join-Path $REPO_ROOT ".venv\Scripts\python.exe"
        if (-not (Test-Path -LiteralPath $healthPython)) {
            throw "Project Python not found at $healthPython; cannot run live health checks"
        }
        Invoke-Native { & $healthPython "$REPO_ROOT/scripts/live_health_check.py" --app ($healthApps -join ',') --expected-sha $GIT_SHORT } | ForEach-Object { Write-Host $_ }
        if ($script:NativeExitCode -ne 0) {
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
        Invoke-Native { git stash pop } | Select-Object -Last 3 | ForEach-Object { Write-Host "    $_" }
        if ($script:NativeExitCode -ne 0) {
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
                Invoke-Native { git stash drop } | Select-Object -Last 1 | ForEach-Object { Write-Host "    $_" }
            }
        }
    }
}
exit $exitCode
