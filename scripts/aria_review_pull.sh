#!/usr/bin/env bash
# Pull all ARIA learning + adversarial stats in one go.
# Usage: ARIA_BEARER=<token> bash scripts/aria_review_pull.sh
set -u
B="${ARIA_BEARER:?Set ARIA_BEARER first}"
H=(-H "Authorization: Bearer $B" -H "Accept: application/json" --max-time 15 -s)
BASE="https://aria-intel.fly.dev"

dump() {
  local label="$1" url="$2"
  echo "================ $label ================"
  curl "${H[@]}" "$url"
  echo
  echo
}

dump "/health (public)"                 "$BASE/health"
dump "brain stats"                      "$BASE/api/aria/brain/stats"
dump "learning stats (24/7 loop)"       "$BASE/api/aria/learning/stats"
dump "student stats"                    "$BASE/api/aria/student/stats"
dump "student mastery heatmap"          "$BASE/api/aria/student/mastery"
dump "reasoning library"                "$BASE/api/aria/reasoning-library/stats"
dump "neural memory"                    "$BASE/api/aria/neural/stats"
dump "verification gate"                "$BASE/api/aria/verification/stats"
dump "calibration auto-tune"            "$BASE/api/aria/calibration/auto-tune"
dump "mistake ledger"                   "$BASE/api/aria/self/mistakes/stats"
dump "predictor block rate"             "$BASE/api/aria/predictor/block_rate"
dump "training-data calibration"        "$BASE/api/aria/training-data/calibration"
dump "adversarial — last run + trend"   "$BASE/api/aria/adversarial/stats"
dump "self / capability manifest"       "$BASE/api/aria/self/manifest"
dump "autonomous engine status"         "$BASE/api/aria/autonomous/status"
