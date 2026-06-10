#!/usr/bin/env bash
# R-F1503 — On-demand pod auto-create + resume-eval launcher.
#
# US-KS-2 (the volume's datacenter) is capacity-blocked intermittently. This
# loop retries an ON-DEMAND (SECURE, non-interruptible) pod create across the
# GPU types that exist in US-KS-2, every RETRY_SECS. The moment a create
# succeeds, it hands the pod to run_v0_2_eval.sh, which waits for RUNNING,
# resumes the eval from the checkpoint on the volume (currently ~63/500),
# pulls the report, prints the verdict, and stops the pod.
#
# Fork-light (one curl per tick) so Windows git-bash doesn't exhaust forks.
# Background-safe: re-invokes the caller when it exits (on success or give-up).
set -uo pipefail
cd "$(dirname "$0")/../.."

LOG=data/_ondemand_eval_launcher.log
KEY=$(grep -E '^RUNPOD_API_KEY=' .env | head -1 | cut -d= -f2- | tr -d '"' | tr -d "'" | tr -d '\r')
VOL=4vdw2zmqov
RETRY_SECS=${RETRY_SECS:-150}
MAX_TRIES=${MAX_TRIES:-80}          # 80 * 150s ~= 3.3h
# GPU types that EXIST in US-KS-2 (the "no instances available" set, in cost order).
# Excludes A5000/4090/A30/L4 which returned "could not find specifications" (not in DC).
GPUS='["NVIDIA A40","NVIDIA L40S","NVIDIA RTX A6000","NVIDIA L40","NVIDIA RTX 6000 Ada Generation","NVIDIA A100 80GB PCIe","NVIDIA A100-SXM4-80GB"]'

log(){ echo "[$(date -u +%H:%M:%S)] $*" | tee -a "$LOG"; }

log "=== R-F1503 on-demand launcher start (vol=$VOL, retry=${RETRY_SECS}s, max=${MAX_TRIES}) ==="

SPOT_POD=${SPOT_POD:-7ei3hldcpz4j2v}   # existing EXITED spot pod on the same volume
POD_ID=""
for i in $(seq 1 "$MAX_TRIES"); do
  # (0) Did the operator migrate/start a pod from the console? Use any RUNNING
  #     pod on our volume directly — a migration keeps the volume + checkpoint.
  FOUND=$(python scripts/train/_find_running_pod.py 2>/dev/null | head -1 | tr -d '[:space:]')
  if [ -n "$FOUND" ]; then
    POD_ID="$FOUND"
    log "FOUND operator-started RUNNING pod $POD_ID on volume $VOL (attempt $i/$MAX_TRIES)"
    break
  fi

  # (a) Preferred: create a stable ON-DEMAND (SECURE) pod.
  R=$(curl -s -X POST "https://rest.runpod.io/v1/pods" \
        -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
        -d "{\"name\":\"aria-llm-eval-ondemand\",
             \"imageName\":\"runpod/pytorch:2.4.0-py3.11-cuda12.4.1-devel-ubuntu22.04\",
             \"gpuTypeIds\":$GPUS,\"gpuCount\":1,\"cloudType\":\"SECURE\",
             \"networkVolumeId\":\"$VOL\",\"volumeMountPath\":\"/workspace\",
             \"containerDiskInGb\":80,\"ports\":[\"8888/http\",\"22/tcp\"]}")
  POD_ID=$(echo "$R" | python -c "import sys,json
try: print(json.load(sys.stdin).get('id','') or '')
except Exception: print('')" 2>/dev/null)
  if [ -n "$POD_ID" ]; then
    log "CREATED on-demand pod $POD_ID on attempt $i/$MAX_TRIES"
    break
  fi
  ERR=$(echo "$R" | python -c "import sys,json
try: print(json.load(sys.stdin).get('error','')[:60])
except Exception: print('')" 2>/dev/null)

  # (b) Fallback: try resuming the existing SPOT pod $SPOT_POD on the same volume.
  #     Spot churns, but the checkpoint protects progress — any running pod helps.
  SR=$(curl -s -X POST "https://rest.runpod.io/v1/pods/$SPOT_POD/start" \
         -H "Authorization: Bearer $KEY")
  SERR=$(echo "$SR" | python -c "import sys,json
try:
  d=json.load(sys.stdin)
  print('OK' if not d.get('error') else d.get('error','')[:60])
except Exception: print('')" 2>/dev/null)
  if [ "$SERR" = "OK" ]; then
    POD_ID="$SPOT_POD"
    log "STARTED spot pod $POD_ID on attempt $i/$MAX_TRIES (on-demand still: $ERR)"
    break
  fi

  log "[try $i/$MAX_TRIES] no capacity — on-demand: $ERR | spot($SPOT_POD): $SERR — retry in ${RETRY_SECS}s"
  sleep "$RETRY_SECS"
done

if [ -z "$POD_ID" ]; then
  log "GAVE UP after $MAX_TRIES tries — US-KS-2 still capacity-blocked. Checkpoint safe on volume; re-run me to retry."
  exit 2
fi

log "=== handing pod $POD_ID to run_v0_2_eval.sh (resume from checkpoint, on-demand = no churn) ==="
RUNPOD_POD_ID="$POD_ID" bash scripts/train/run_v0_2_eval.sh 2>&1 | tee -a "$LOG"
RC=${PIPESTATUS[0]}
log "=== run_v0_2_eval.sh exit=$RC — pod stop is handled inside the driver ==="
exit "$RC"
