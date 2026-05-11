#!/usr/bin/env bash
# Smoke-test today's commits against the LIVE fly.io deploy.
# Run AFTER every commit lands + before declaring the change shipped.
#
# Usage:
#   export ARIA_API_TOKEN=<your_token>
#   bash scripts/smoke_test_2026_05_11.sh
#
# OR set BASE_URL to override target (default: production):
#   BASE_URL=https://aria-intel.fly.dev bash scripts/smoke_test_2026_05_11.sh
#
# Exit code:
#   0 — all checks PASS
#   1 — one or more FAIL (operator must investigate before sign-off)
#
# Output discipline: every probe prints PASS or FAIL with the endpoint
# and a short rationale. No silent failures.

set -uo pipefail  # strict on uninitialised vars + pipe failures; trap on fail below
BASE_URL="${BASE_URL:-https://aria-intel.fly.dev}"
TOKEN="${ARIA_API_TOKEN:-}"
PASS=0
FAIL=0
FAILED_PROBES=()

probe() {
  local name="$1"
  local cmd="$2"
  local expect="$3"  # substring expected in response body OR HTTP code (3-digit)
  echo -n "  $name ... "
  local out
  out=$(eval "$cmd" 2>&1) || true
  if echo "$out" | grep -qE "$expect"; then
    echo "PASS"
    PASS=$((PASS + 1))
  else
    echo "FAIL"
    echo "    expected: $expect"
    echo "    got:      $(echo "$out" | head -c 200)"
    FAIL=$((FAIL + 1))
    FAILED_PROBES+=("$name")
  fi
}

echo "========================================"
echo " ARIA smoke-test — $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo " Target: $BASE_URL"
echo "========================================"

# ── R-F254 — public-path bypass (no token needed) ───────────────────────
echo
echo "R-F254 public-path bypass (no bearer required):"

probe "GET /api/aria/health" \
  "curl -s -o /dev/null -w '%{http_code}' '$BASE_URL/api/aria/health'" \
  "200"

probe "GET /api/aria/constitution/version" \
  "curl -s '$BASE_URL/api/aria/constitution/version'" \
  'clause_count'

probe "GET /api/aria/chat-audit/stats" \
  "curl -s '$BASE_URL/api/aria/chat-audit/stats'" \
  'head_hash'

probe "GET /api/aria/adversarial/stats" \
  "curl -s '$BASE_URL/api/aria/adversarial/stats'" \
  'last_run\|overall_score'

# ── Authenticated probes (require ARIA_API_TOKEN) ───────────────────────
if [[ -z "$TOKEN" ]]; then
  echo
  echo "Skipping authenticated probes — ARIA_API_TOKEN not set."
  echo "(Export it to test /knowledge/inventory, /dd/reports, etc.)"
else
  echo
  echo "Authenticated probes (using ARIA_API_TOKEN):"

  probe "GET /api/aria/knowledge/inventory?tag=test" \
    "curl -s -H 'Authorization: Bearer $TOKEN' '$BASE_URL/api/aria/knowledge/inventory?tag=test'" \
    '"tag":\|"hits":\|"facts":'

  probe "GET /api/aria/dd/reports?limit=5" \
    "curl -s -H 'Authorization: Bearer $TOKEN' '$BASE_URL/api/aria/dd/reports?limit=5'" \
    '"reports":'

  probe "GET /api/aria/adversarial/amendments" \
    "curl -s -H 'Authorization: Bearer $TOKEN' '$BASE_URL/api/aria/adversarial/amendments'" \
    'queue_depth\|amendments'

  probe "GET /api/aria/learning/stats" \
    "curl -s -H 'Authorization: Bearer $TOKEN' '$BASE_URL/api/aria/learning/stats'" \
    'quarantine\|training_export\|spider'

  # POST endpoints — verify they accept the method (not 405)
  probe "POST /api/aria/adversarial/run-weekly (kebab alias R-F253)" \
    "curl -s -o /dev/null -w '%{http_code}' -X POST -H 'Authorization: Bearer $TOKEN' -H 'Content-Type: application/json' -d '{}' '$BASE_URL/api/aria/adversarial/run-weekly'" \
    "200\|202\|400"   # 200=ran, 202=accepted, 400=validation; anything but 404/405

  probe "POST /api/aria/inbound/test (R-F249 — expects 401 without HMAC)" \
    "curl -s -o /dev/null -w '%{http_code}' -X POST -H 'Authorization: Bearer $TOKEN' -H 'Content-Type: application/json' -d '{}' '$BASE_URL/api/aria/inbound/test'" \
    "401"   # No HMAC + no per-source secret = 401 (correct security behaviour)
fi

# ── Negative probes (ensure auth still works on protected endpoints) ────
echo
echo "Negative probes (auth must REJECT without token):"

probe "GET /api/aria/knowledge/inventory?tag=x (no token → 401)" \
  "curl -s -o /dev/null -w '%{http_code}' '$BASE_URL/api/aria/knowledge/inventory?tag=x'" \
  "401"

probe "POST /api/aria/self/deploy/xyz (no token → 401)" \
  "curl -s -o /dev/null -w '%{http_code}' -X POST '$BASE_URL/api/aria/self/deploy/xyz'" \
  "401"

# ── Result ──────────────────────────────────────────────────────────────
echo
echo "========================================"
echo " Result: $PASS pass, $FAIL fail"
echo "========================================"
if [[ $FAIL -gt 0 ]]; then
  echo "Failed probes:"
  for p in "${FAILED_PROBES[@]}"; do echo "  - $p"; done
  exit 1
fi
exit 0
