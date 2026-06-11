#!/usr/bin/env bash
# R-F1514 — v0.4 train launcher. US-KS-2 GPU capacity is INTERMITTENT (flickers
# open for seconds). This loops: create a GPU pod (key injected) and the MOMENT
# one is created, hand it straight to run_v0_4_cycle.sh (which waits for RUNNING,
# scp's the corpus+scripts, trains v0.4, evals vs v0.3 + teacher, prints the
# verdict, and stops the pod). If the cycle fails because the pod vanished before
# it could be claimed, retry the create. Singleton by construction: one create →
# one cycle → exit; never a fleet.
set -uo pipefail
cd "$(dirname "$0")/../.."
LOG=data/_v04_train_launcher.log
RETRY=${RETRY:-150}        # capacity-wait between create attempts
MAX=${MAX:-120}           # ~5h of retrying
log(){ echo "[$(date -u +%H:%M:%S)] $*" | tee -a "$LOG"; }

log "=== R-F1514 v0.4 train launcher (intermittent-capacity GPU grab) ==="
for i in $(seq 1 "$MAX"); do
  POD=$(python scripts/train/_create_v04_pod.py 2>/dev/null | head -1 | tr -d '[:space:]')
  if [ -n "$POD" ]; then
    log "CREATED GPU pod $POD (attempt $i/$MAX) — handing to run_v0_4_cycle.sh NOW"
    RUNPOD_POD_ID="$POD" bash scripts/train/run_v0_4_cycle.sh 2>&1 | tee -a "$LOG"
    RC=${PIPESTATUS[0]}
    if [ "$RC" = "0" ]; then
      log "=== v0.4 cycle completed (rc=0) — pod stopped by the orchestrator ==="
      exit 0
    fi
    log "cycle rc=$RC (pod likely vanished/capacity churn) — retry create in ${RETRY}s"
  else
    log "[try $i/$MAX] no GPU capacity yet — retry in ${RETRY}s"
  fi
  sleep "$RETRY"
done
log "GAVE UP after $MAX tries — US-KS-2 GPU capacity never held. Corpus+scripts staged; re-run me."
exit 2
