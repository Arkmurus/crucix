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
LAST_TAG=$(git tag --list 'deploy-*' --sort=-version:refname | head -1 || echo "")
if [[ -n "$LAST_TAG" ]]; then
    R_NUMBERS=$(git log "$LAST_TAG..HEAD" --pretty=%s | grep -oE 'R-F[0-9]+' | sort -u | tr '\n' '+' | sed 's/+$//')
else
    R_NUMBERS=$(git log --pretty=%s | grep -oE 'R-F[0-9]+' | sort -u | tr '\n' '+' | sed 's/+$//')
fi
R_TAG="${R_NUMBERS:-no-r-tag}"

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
            echo "  poll $i/36: version $pre_ver->$now_ver, live build_rev=${live_sha:-?} (want $GIT_SHORT)"
        else
            # aria-web / aria-wa don't expose the git sha reliably — require a
            # version bump AND a healthy response.
            local code; code=$(curl -s -o /dev/null -w '%{http_code}' -m 8 "https://$app.fly.dev/" 2>/dev/null || echo 000)
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

FAILURES=0
$DEPLOY_INTEL && { deploy_and_verify "aria-intel" "fly.toml"     900 || ((FAILURES++)); }
$DEPLOY_WEB   && { deploy_and_verify "aria-web"   "fly.web.toml" 600 || ((FAILURES++)); }
$DEPLOY_WA    && { deploy_and_verify "aria-wa"    "fly.wa.toml"  600 || ((FAILURES++)); }

echo ""
if [[ $FAILURES -eq 0 ]]; then
    git tag "deploy-$(date +%Y%m%d-%H%M%S)" "$GIT_SHA" 2>/dev/null || true
    echo "=== ✅ ALL DEPLOYS VERIFIED LIVE (commit $GIT_SHORT is serving) ==="
    exit 0
else
    echo "=== ❌ $FAILURES deploy(s) NOT verified live — NOT shipped. Fix + re-run. ==="
    exit 1
fi
