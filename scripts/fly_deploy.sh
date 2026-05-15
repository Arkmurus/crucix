#!/usr/bin/env bash
# R-F543 (2026-05-15, renumbered from R-F535 due to R-F534 collision) —
# wrapper that bakes git sha + R-number into the fly.io image at build
# time. Use this instead of bare `flyctl deploy`.
#
# Pre-R-F543, manual `flyctl deploy` invocations skipped the
# `--build-arg ARIA_BUILD_GIT_SHA=…` flags that the CI workflow passes.
# Net effect: every manual deploy left /api/aria/health/live reporting
# `UNKNOWN-BUILD · ARIA_BUILD_GIT_SHA not set at image build`. Live
# evidence 2026-05-15: 7 of last 7 fly releases (v857-v863) all by the
# operator, all manual, all returning UNKNOWN-BUILD.
#
# Usage:
#   ./scripts/fly_deploy.sh             # standard deploy
#   ./scripts/fly_deploy.sh --strategy immediate
#
# Any extra args are forwarded to flyctl deploy verbatim.

set -euo pipefail

cd "$(dirname "$0")/.."

GIT_SHA="$(git rev-parse HEAD)"
R_TAG="$(git log -1 --pretty=%s | grep -oE 'R-F[0-9]+([+ ]+F[0-9]+)*' | head -1 | tr -d ' ')"
R_TAG="${R_TAG:-no-r-tag}"

echo "[fly_deploy] GIT_SHA=${GIT_SHA}"
echo "[fly_deploy] R_TAG=${R_TAG}"
echo "[fly_deploy] forwarding extra args: $*"

exec flyctl deploy --remote-only --app aria-intel --wait-timeout 900 \
  --build-arg ARIA_BUILD_GIT_SHA="${GIT_SHA}" \
  --build-arg ARIA_BUILD_R_TAG="${R_TAG}" \
  "$@"
