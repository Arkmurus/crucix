#!/usr/bin/env bash
# R-F1079 — ARIA deploy script with batching support.
#
# Usage:
#   ./scripts/deploy.sh [--intel] [--web] [--wa] [--all]
#
# Deploys only when explicitly called. This replaces the auto-deploy-on-push
# pattern that caused 5 cold-boot outages in 30 min (Claude finding C, 2026-05-29).
#
# Options:
#   --intel    Deploy aria-intel (FastAPI brain)
#   --web      Deploy aria-web (Node UI)
#   --wa       Deploy aria-wa (WhatsApp listener)
#   --all      Deploy all three (default)
#
# Each deploy is batched: the script collects all pending R-numbers from the
# git log since the last deploy tag, and passes them as a single build arg.
#
# Prerequisites:
#   - flyctl installed and authenticated
#   - FLY_API_TOKEN set in environment (or flyctl auth configured)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

DEPLOY_INTEL=false
DEPLOY_WEB=false
DEPLOY_WA=false

# Parse arguments
if [[ $# -eq 0 ]]; then
    DEPLOY_INTEL=true
    DEPLOY_WEB=true
    DEPLOY_WA=true
else
    for arg in "$@"; do
        case "$arg" in
            --intel) DEPLOY_INTEL=true ;;
            --web)   DEPLOY_WEB=true ;;
            --wa)    DEPLOY_WA=true ;;
            --all)   DEPLOY_INTEL=true; DEPLOY_WEB=true; DEPLOY_WA=true ;;
            --help)
                echo "Usage: $0 [--intel] [--web] [--wa] [--all]"
                exit 0
                ;;
            *)
                echo "Unknown option: $arg"
                echo "Usage: $0 [--intel] [--web] [--wa] [--all]"
                exit 1
                ;;
        esac
    done
fi

# Collect R-numbers since last deploy tag
LAST_TAG=$(git tag --list 'deploy-*' --sort=-version:refname | head -1 || echo "")
if [[ -n "$LAST_TAG" ]]; then
    R_NUMBERS=$(git log "$LAST_TAG..HEAD" --pretty=%s | grep -oE 'R-F[0-9]+' | sort -u | tr '\n' '+' | sed 's/+$//')
    COMMITS_SINCE=$(git rev-list --count "$LAST_TAG..HEAD" 2>/dev/null || echo "0")
else
    R_NUMBERS=$(git log --pretty=%s | grep -oE 'R-F[0-9]+' | sort -u | tr '\n' '+' | sed 's/+$//')
    COMMITS_SINCE=$(git rev-list --count HEAD 2>/dev/null || echo "0")
fi

GIT_SHA=$(git rev-parse HEAD)
R_TAG="${R_NUMBERS:-no-r-tag}"

echo "=== ARIA Deploy Script (R-F1079) ==="
echo "  Git SHA:     $GIT_SHA"
echo "  R-numbers:   $R_TAG"
echo "  Commits:     $COMMITS_SINCE since last deploy"
echo "  Deploying:   intel=$DEPLOY_INTEL web=$DEPLOY_WEB wa=$DEPLOY_WA"
echo ""

deploy_app() {
    local app="$1"
    local config="$2"
    local timeout="$3"

    echo "--- Deploying $app ---"
    if flyctl deploy --remote-only --config "$config" --app "$app" \
        --wait-timeout "$timeout" \
        --build-arg ARIA_BUILD_GIT_SHA="$GIT_SHA" \
        --build-arg ARIA_BUILD_R_TAG="$R_TAG"; then
        echo "✅ $app deployed successfully"
    else
        echo "⚠️  flyctl exited non-zero for $app — checking /health..."
        # Verify via health endpoint (same pattern as CI)
        local health_url="https://$app.fly.dev/health"
        for i in $(seq 1 36); do
            http_code=$(curl -sS -m 5 -o /tmp/health_${app}.json -w '%{http_code}' "$health_url" || echo "000")
            if [[ "$http_code" = "200" ]]; then
                echo "✅ $app verified live via /health"
                return 0
            fi
            echo "  poll ${i}/36: /health returned HTTP ${http_code}"
            sleep 5
        done
        echo "❌ $app failed to reach /health=200 within 180s"
        return 1
    fi
}

FAILURES=0

if $DEPLOY_INTEL; then
    deploy_app "aria-intel" "fly.toml" 900 || ((FAILURES++))
fi

if $DEPLOY_WEB; then
    deploy_app "aria-web" "fly.web.toml" 600 || ((FAILURES++))
fi

if $DEPLOY_WA; then
    deploy_app "aria-wa" "fly.wa.toml" 600 || ((FAILURES++))
fi

# Tag the deploy
if [[ $FAILURES -eq 0 ]]; then
    TAG="deploy-$(date +%Y%m%d-%H%M%S)"
    git tag "$TAG" "$GIT_SHA"
    echo "Tagged deploy: $TAG"
    echo ""
    echo "=== All deploys successful ==="
else
    echo ""
    echo "=== $FAILURES deploy(s) failed ==="
fi

exit $FAILURES
