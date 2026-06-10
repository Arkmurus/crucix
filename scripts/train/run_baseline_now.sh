#!/bin/bash
# R-F1455 — run the baseline on an ALREADY-RUNNING pod (the migrated pod). No
# start-retry. Push runner + eval set, run baseline_pod_run.sh, pull reports,
# stop the pod. Pod id/host/port passed via env (defaults = the migrated pod).
set -uo pipefail
REPO="/c/code/crucix"; cd "$REPO"
POD="${POD:-lqhxb4swwafuzv}"
HOST="${HOST:-216.81.248.127}"
PORT="${PORT:-19967}"
API="https://rest.runpod.io/v1"
API_KEY=$(grep -E "^RUNPOD_API_KEY=" .env | head -1 | cut -d= -f2- | tr -d '"' | tr -d "'" | tr -d '\r')
DSK=$(grep -E "^DEEPSEEK_API_KEY=" .env | head -1 | cut -d= -f2- | tr -d '"' | tr -d "'" | tr -d '\r')
KEY="/tmp/rpkey"; cp ~/.ssh/runpod_aria "$KEY"; chmod 600 "$KEY"
SSH="ssh -i $KEY -o StrictHostKeyChecking=no -o ConnectTimeout=15"
SCP="scp -i $KEY -o StrictHostKeyChecking=no -P $PORT"

stop_pod(){ echo "[driver] stopping pod $POD"; curl -s -X POST "$API/pods/$POD/stop" -H "Authorization: Bearer $API_KEY" >/dev/null 2>&1 || true; }
trap stop_pod EXIT

echo "[driver] target pod $POD @ $HOST:$PORT"
$SSH -p "$PORT" root@"$HOST" "echo SSH_OK" | grep -q SSH_OK || { echo "[driver] FATAL: SSH failed"; exit 1; }

echo "[driver] pushing runner + 500-Q eval set…"
$SCP scripts/train/baseline_pod_run.sh root@"$HOST":/workspace/ || { echo "[driver] FATAL scp runner"; exit 1; }
$SSH -p "$PORT" root@"$HOST" "mkdir -p /workspace/datasets"
$SCP data/eval_reports/aria_eval_500q.jsonl root@"$HOST":/workspace/datasets/aria_eval_500q.jsonl || { echo "[driver] FATAL scp eval set"; exit 1; }

echo "[driver] running baseline_pod_run.sh on the pod…"
$SSH -p "$PORT" root@"$HOST" "DEEPSEEK_API_KEY='$DSK' bash /workspace/baseline_pod_run.sh"

echo "[driver] pulling reports…"
mkdir -p data/eval_reports
$SCP root@"$HOST":/workspace/eval/aria_llm_v02_eval.json data/eval_reports/ 2>/dev/null || echo "[driver] (v02 report not pulled)"
$SCP root@"$HOST":/workspace/eval/deepseek_baseline_eval.json data/eval_reports/ 2>/dev/null || echo "[driver] (deepseek report not pulled)"

stop_pod
echo "[driver] DONE — reports in data/eval_reports/, pod $POD stopped."
