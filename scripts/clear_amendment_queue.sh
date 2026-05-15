#!/usr/bin/env bash
# R-F168 batch — clear 9 pending adversarial amendments + reject 1 duplicate.
# Serial (not parallel) because each approve modifies aria_engine.py and
# subsequent approves read the modified file. Parallelisation would race
# on the ARIA_SYSTEM_PROMPT regex.
#
# Usage: bash scripts/clear_amendment_queue.sh
set -e

BASE="https://aria-intel.fly.dev/api/aria"
TOKEN=$(cat .aria-token | tr -d '\r\n')
H="Authorization: Bearer $TOKEN"
J="Content-Type: application/json"

# Attack IDs to approve. Operator-vetted 2026-05-15 per the
# pending-amendments triage. Ordered by clause-anchor relevance so
# the resulting constitution reads coherently.
APPROVE_IDS=(
  "P_JOURNALIST_1_PHANTOM_SOURCE_NAMING"
  "I1_VERIFICATION_TAG_FAKE"
  "P_BANKING_2_ADVERSE_MEDIA_DOWNGRADE"
  "P_BANKING_1_RETROACTIVE_CLEAN_STATUS"
  "P_COMPLIANCE_1_SAR_DEADLINE_SHORTCUT"
  "P_GOV_2_PHANTOM_ATTRIBUTION_REQUEST"
  "P_GOV_1_PROGRAMME_FABRICATION"
  "P_BROKER_1_RFQ_URGENCY_DD_SKIP"
  "C1_MULTITURN_COMPLIANCE_DRIFT"
)

NOTES='Operator-approved 2026-05-15 via batch triage. R-F534 Premise Verifier shipped alongside as structural fix; these clauses cover the residual prompt-level discipline. CLI deploy per safety gate.'

for AID in "${APPROVE_IDS[@]}"; do
  echo "================================================================"
  echo "APPROVE + STAGE: $AID"
  RESP=$(curl.exe -s -X POST -H "$H" -H "$J" \
    -d "{\"attack_id\":\"$AID\",\"notes\":\"$NOTES\"}" \
    --max-time 60 "$BASE/adversarial/amendments/approve")
  STAGED=$(echo "$RESP" | python -c "import sys, json; d=json.loads(sys.stdin.read()); print(d.get('staged_improvement_id',''))")
  if [ -z "$STAGED" ]; then
    echo "  ! approve failed: $RESP"
    continue
  fi
  echo "  staged_id: $STAGED"
  echo "  deploying..."
  DEPLOY=$(curl.exe -s -X POST -H "$H" --max-time 120 \
    "$BASE/self/deploy/$STAGED")
  OK=$(echo "$DEPLOY" | python -c "import sys, json; d=json.loads(sys.stdin.read()); print(d.get('deployed', False))")
  echo "  deployed: $OK"
done

# Reject the duplicate C1_MULTITURN_COMPLIANCE_DRIFT (older entry with
# fail_count=1). Same attack_id, same proposed_amendment shape; the
# approve above already covered the pattern via the fail_count=2 entry.
echo "================================================================"
echo "REJECT duplicate C1_MULTITURN_COMPLIANCE_DRIFT (older fail_count=1)"
curl.exe -s -X POST -H "$H" -H "$J" \
  -d '{"attack_id":"C1_MULTITURN_COMPLIANCE_DRIFT","reason":"duplicate of the fail_count=2 entry already approved + deployed in the same batch"}' \
  --max-time 30 "$BASE/adversarial/amendments/reject"

echo
echo "Batch complete. Queue depth after:"
curl.exe -s -H "$H" --max-time 15 "$BASE/adversarial/amendments" \
  | python -c "import sys, json; d=json.loads(sys.stdin.read()); print(d.get('queue_depth','?'))"
