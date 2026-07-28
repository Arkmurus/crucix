#!/usr/bin/env bash
# R-F1079 / R-F1116 — ARIA bulletproof deploy script.
#
# Deploys DIRECTLY via flyctl (never relies on CI / the [deploy] tag, which do
# NOT reliably auto-deploy) and PROVES the new build is actually serving before
# it reports success. "Deployed" is impossible to fake: the script fails loud
# (non-zero exit) unless the live app advanced to a new version AND, for
# aria-intel, the live build_rev matches the commit you deployed.
#
# This is the standing fix for the false-deploy class (operator: "no more manual
# deployment from me — she does it"). ARIA: use THIS for every deploy.
#
# Usage:
#   ./scripts/deploy.sh [--intel] [--web] [--wa] [--all]   # default: --all
#
# Prereqs: flyctl installed + authenticated (flyctl auth whoami), git available.

set -uo pipefail   # NOTE: not -e — we handle failures explicitly so verify always runs

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

DEPLOY_INTEL=false; DEPLOY_WEB=false; DEPLOY_WA=false
if [[ $# -eq 0 ]]; then
    DEPLOY_INTEL=true; DEPLOY_WEB=true; DEPLOY_WA=true
else
    for arg in "$@"; do
        case "$arg" in
            --intel) DEPLOY_INTEL=true ;;
            --web)   DEPLOY_WEB=true ;;
            --wa)    DEPLOY_WA=true ;;
            --all)   DEPLOY_INTEL=true; DEPLOY_WEB=true; DEPLOY_WA=true ;;
            --help)  echo "Usage: $0 [--intel] [--web] [--wa] [--all]"; exit 0 ;;
            *) echo "Unknown option: $arg"; exit 1 ;;
        esac
    done
fi

GIT_SHA=$(git rev-parse HEAD)
GIT_SHORT=$(git rev-parse --short=8 HEAD)

# R-F1122 — PUSH GUARD: refuse to deploy if HEAD != origin/main.
# flyctl deploy builds from the LOCAL tree, so a deploy SUCCEEDS even when
# you never pushed — which is the trap: the live server runs your code while
# origin/main stays behind and your work is NOT backed up on GitHub.
# This guard catches that BEFORE the deploy starts.
ORIGIN_SHA=$(git rev-parse origin/main 2>/dev/null || echo "")
if [[ -z "$ORIGIN_SHA" ]]; then
    echo "  ⚠️  Cannot check origin/main (no remote or not fetched). Push manually first."
    echo "     Run: git push origin main"
    exit 1
fi
if [[ "$GIT_SHA" != "$ORIGIN_SHA" ]]; then
    echo "  ❌ PUSH GUARD: HEAD ($GIT_SHORT) != origin/main ($(echo "$ORIGIN_SHA" | head -c 8))."
    echo "     You committed but did NOT push. The deploy would succeed locally but"
    echo "     origin/main would diverge from what is live — your work would NOT be"
    echo "     backed up on GitHub."
    echo ""
    echo "     Fix: git push origin main"
    echo "     Then re-run this script."
    exit 1
fi
echo "  ✅ push guard: HEAD matches origin/main ($GIT_SHORT)"

# ---- TREE INTEGRITY GATE (R-F2919) ----
# The image is built from the WORKING TREE, so a tracked file missing from disk is
# silently ABSENT from the image. The push guard proves HEAD matches origin/main; it
# says nothing about whether the files are actually present.
#
# 2026-07-23: antivirus quarantined aria_service/static/aria_client/aria.bat out of a
# fresh clone ~10s after checkout; the deploy proceeded and that path served 404 in
# production while every other check passed. The behaviour is INTERMITTENT, which is
# why it must not be left to an AV setting or to remembering which directory is
# excluded. `git ls-files -d` answers "is anything missing?" and does not care why.
MISSING=$(git ls-files -d)
if [[ -n "$MISSING" ]]; then
    echo "  ❌ TREE INTEGRITY: tracked file(s) MISSING from the working tree:"
    echo "$MISSING" | head -20 | sed 's/^/       - /'
    echo "     The image is built from this tree, so these would be absent in production."
    echo "     Restore and re-verify:  git checkout HEAD -- <path>"
    echo "     If it vanishes again, deploy from an AV-excluded checkout."
    exit 1
fi
echo "  ✅ tree integrity: no tracked file is missing"

# R-F3357 - the newest deploy tag STRICTLY BEHIND HEAD. R-F3247 renamed the
# under-claim's symptom ("no-r-tag" -> "no-new-r-numbers") without changing the
# condition, so a tag sitting AT HEAD still empties the range. Measured live
# 2026-07-28: intel deployed first and tagged the commit, so the web deploy
# minutes later served "no-new-r-numbers" while shipping R-F3351 + R-F3352.
# See deploy.ps1 for the full note; both writers must carry this.
LAST_TAG=""
for _t in $(git tag --list 'deploy-*' --sort=-version:refname); do
    if [ "$(git rev-list --count "${_t}..HEAD" 2>/dev/null || echo 0)" -gt 0 ]; then
        LAST_TAG="$_t"; break
    fi
done
# R-F3371 - a commit contributes its OWN R-number, and only if it ships
# something. Two over-claims measured live: a code commit CITING an older
# R-number put it in the banner, and a docs-only commit announced an R-number for
# a build it changed nothing in. See deploy.ps1 for the full note; both writers
# must carry this (R-F3247, R-F3357 are the same family).
_SHIPS_NOTHING='^(docs/|memory/|[^/]*\.md$|data/r_number_reservations\.json$)'
if [[ -n "$LAST_TAG" ]]; then _RANGE="$LAST_TAG..HEAD"; else _RANGE="HEAD"; fi
R_NUMBERS=$(
    for _c in $(git log "$_RANGE" --pretty=%H); do
        _subj=$(git log -1 --pretty=%s "$_c")
        echo "$_subj" | grep -qE '^chore:[[:space:]]*(reserve|mark|ship)' && continue
        _files=$(git diff-tree --no-commit-id --name-only -r "$_c")
        [ -z "$(echo "$_files" | grep -vE "$_SHIPS_NOTHING" | head -1)" ] && continue
        echo "$_subj" | grep -oE 'R-F[0-9]+' | head -1
    done | sort -u | tr '\n' '+' | sed 's/+$//'
)
# R-F3247 - "no-r-tag" reads as "this build ships nothing"; when a deploy tag
# exists the honest statement is that nothing is NEW since it.
if [ -n "${R_NUMBERS}" ]; then R_TAG="${R_NUMBERS}"
elif [ -n "${LAST_TAG:-}" ]; then R_TAG="no-new-r-numbers"
else R_TAG="no-r-tag"; fi

echo "=== ARIA bulletproof deploy (R-F1116) ==="
echo "  commit: $GIT_SHA ($GIT_SHORT)"
echo "  r-tags: $R_TAG"
echo "  apps:   intel=$DEPLOY_INTEL web=$DEPLOY_WEB wa=$DEPLOY_WA"
echo ""

# Current deployed version for an app (integer), or 0 if unknown.
# Uses the top-level "Version" field from `flyctl status --json` — NOT the table
# (the table's first number is the machine ID, not the version).
current_version() {
    local v
    v=$(flyctl status -a "$1" --json 2>/dev/null \
        | grep -oE '"Version"[[:space:]]*:[[:space:]]*[0-9]+' \
        | grep -oE '[0-9]+' | tail -1)
    echo "${v:-0}"
}

# Live build_rev short-sha for aria-intel (empty if unreachable).
intel_build_rev() {
    curl -s -m 12 https://aria-intel.fly.dev/health/live 2>/dev/null \
      | grep -oE 'sha [a-f0-9]+' | awk '{print $2}' | head -1
}

# R-F3299: does the LIVE sha CONTAIN our commit? Mirrors deploy.ps1's
# Test-LiveShaContainsHead. The exact-sha test below reports a FAILED deploy
# whenever a peer ships a commit that INCLUDES ours: our code is serving, the sha
# on the wire is theirs, and the loop burns its full poll budget before printing
# red on a deploy that succeeded. Ancestry is the honest test, and an unrelated or
# OLDER sha still fails because ancestry is real containment.
#
# An object we do not hold is NOT a pass. We fetch once (a peer's commit is often
# simply not fetched yet, which is the exact case this exists to resolve) and if
# it is still unknown we return failure: "cannot verify" is not "verified".
live_sha_contains_head() {
    local live="$1"
    [[ -z "$live" ]] && return 1
    if ! git cat-file -e "${live}^{commit}" 2>/dev/null; then
        git fetch origin --quiet 2>/dev/null || true
        git cat-file -e "${live}^{commit}" 2>/dev/null || return 1
    fi
    git merge-base --is-ancestor "$GIT_SHA" "$live" 2>/dev/null
}

# Deploy one app and PROVE it landed. Returns 0 only when verified live.
deploy_and_verify() {
    local app="$1" config="$2" timeout="$3"
    echo "--- $app: deploying directly via flyctl ---"
    local pre_ver; pre_ver=$(current_version "$app")
    echo "  pre-deploy version: ${pre_ver:-0}"

    flyctl deploy --remote-only --config "$config" --app "$app" \
        --wait-timeout "$timeout" \
        --build-arg ARIA_BUILD_GIT_SHA="$GIT_SHA" \
        --build-arg ARIA_BUILD_R_TAG="$R_TAG"
    local rc=$?
    if [[ $rc -ne 0 ]]; then
        echo "  ⚠️  flyctl exited $rc — verifying anyway (it sometimes deploys then errors on wait)"
    fi

    # VERIFY: the app must advance to a NEW version, and aria-intel must serve
    # the exact commit build_rev. Poll up to ~3 min. Fail loud otherwise.
    local ok=false
    for i in $(seq 1 36); do
        local now_ver; now_ver=$(current_version "$app")
        local version_bumped=false
        [[ -n "$now_ver" && "$now_ver" =~ ^[0-9]+$ && -n "$pre_ver" && "$now_ver" -gt "$pre_ver" ]] && version_bumped=true

        if [[ "$app" == "aria-intel" ]]; then
            local live_sha; live_sha=$(intel_build_rev)
            if [[ "$live_sha" == "$GIT_SHORT" ]]; then
                echo "  ✅ $app LIVE — build_rev=$live_sha matches commit (version $now_ver)"
                ok=true; break
            fi
            # R-F3299: a peer's commit that CONTAINS ours means our code is live.
            if live_sha_contains_head "$live_sha"; then
                echo "  ✅ $app LIVE (ancestor) — serving $live_sha, which CONTAINS your commit $GIT_SHORT."
                echo "     A peer deployed past you. Your code IS live; the exact sha is not."
                ok=true; break
            fi
            echo "  poll $i/36: version $pre_ver->$now_ver, live build_rev=${live_sha:-?} (want $GIT_SHORT)"
        else
            # aria-web / aria-wa don't expose the git sha reliably — require a
            # version bump AND a healthy response.
            # R-F1330 — probe a HEALTH endpoint, not "/". aria-wa's "/" is 404 (no
            # root route), so the old "/" probe false-negatived every aria-wa deploy
            # even when it succeeded (retry churn / phantom "deploy failed"). Verified
            # live 2026-06-04: aria-wa /health=200 (/=404); aria-web /healthz=200.
            local code=000 hp
            for hp in /health /healthz /; do
                code=$(curl -s -o /dev/null -w '%{http_code}' -m 8 "https://$app.fly.dev$hp" 2>/dev/null || echo 000)
                [[ "$code" == "200" ]] && break
            done
            if [[ "$version_bumped" == true && "$code" == "200" ]]; then
                echo "  ✅ $app LIVE — version $pre_ver->$now_ver, HTTP $code"
                ok=true; break
            fi
            echo "  poll $i/36: version $pre_ver->$now_ver (bumped=$version_bumped), HTTP $code"
        fi
        sleep 5
    done

    if [[ "$ok" == true ]]; then
        return 0
    fi
    echo "  ❌ $app NOT VERIFIED LIVE — the server did NOT advance to your commit."
    echo "     Do NOT report this deployed. Re-run, or check 'flyctl logs -a $app'."
    return 1
}

# Write the expected SHA for the live health check script (R-F1125)
echo "$GIT_SHORT" > "$REPO_ROOT/.last_deploy_sha"

FAILURES=0
$DEPLOY_INTEL && { deploy_and_verify "aria-intel" "fly.toml"     900 || ((FAILURES++)); }
$DEPLOY_WEB   && { deploy_and_verify "aria-web"   "fly.web.toml" 600 || ((FAILURES++)); }
$DEPLOY_WA    && { deploy_and_verify "aria-wa"    "fly.wa.toml"  600 || ((FAILURES++)); }

echo ""
if [[ $FAILURES -eq 0 ]]; then
    git tag "deploy-$(date +%Y%m%d-%H%M%S)" "$GIT_SHA" 2>/dev/null || true
    echo "=== ✅ ALL DEPLOYS VERIFIED LIVE (commit $GIT_SHORT is serving) ==="

    # R-F1125 — run the live health regression suite
    echo ""
    echo "=== Running live health regression suite ==="
    # R-F2868 — check ONLY the tiers this run deployed (mirrors deploy.ps1). This
    # was `--app all`, so a single-tier deploy checked apps it never touched.
    HEALTH_APPS=""
    $DEPLOY_INTEL && HEALTH_APPS="${HEALTH_APPS}intel,"
    $DEPLOY_WEB   && HEALTH_APPS="${HEALTH_APPS}web,"
    $DEPLOY_WA    && HEALTH_APPS="${HEALTH_APPS}wa,"
    HEALTH_APPS="${HEALTH_APPS%,}"
    [[ -z "$HEALTH_APPS" ]] && HEALTH_APPS="intel"
    python "$REPO_ROOT/scripts/live_health_check.py" --app "$HEALTH_APPS"
    HEALTH_RC=$?
    if [[ $HEALTH_RC -ne 0 ]]; then
        echo "=== ❌ Live health regression suite FAILED — deploy succeeded but health checks failed. ==="
        echo "    Check flyctl logs -a <app> for details."
        exit 1
    fi
    echo "=== ✅ Live health regression suite PASSED ==="
    exit 0
else
    echo "=== ❌ $FAILURES deploy(s) NOT verified live — NOT shipped. Fix + re-run. ==="
    exit 1
fi
