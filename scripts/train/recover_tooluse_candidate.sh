#!/usr/bin/env bash
# R-F3744 — resume candidate v2, persist its adapter, collect TRAIN failures.
set -uo pipefail
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd) || exit 1
REPO="${REPO:-$(cd "$SCRIPT_DIR/../.." && pwd)}"
cd "$REPO" || exit 1

API="https://rest.runpod.io/v1"
KEY=$(grep -E '^RUNPOD_API_KEY=' .env | head -1 | cut -d= -f2- | tr -d '"' | tr -d "'" | tr -d '\r')
[ -n "$KEY" ] || { echo "[recover] FATAL API key unavailable"; exit 1; }
STATE_FILE="${STATE_FILE:-data/eval_reports/.tooluse_pod_state}"
. "$STATE_FILE"
: "${POD_ID:?POD_ID missing from state}"
QUEUE="${QUEUE:-data/training/tooluse_dpo_generation_v2.jsonl}"
LOCAL_ADAPTER="${LOCAL_ADAPTER:-data/training/checkpoints/aria_tooluse_candidate_v2.tgz}"
LOCAL_REPORT="${LOCAL_REPORT:-data/eval_reports/aria_tooluse_train_generations_v2.json}"
LOCAL_TRAINED="${LOCAL_TRAINED:-data/eval_reports/aria_tooluse_eval_trained_recovered.json}"
LOCAL_RUN_LOG="${LOCAL_RUN_LOG:-data/eval_reports/aria_tooluse_cycle_recovered.log}"
DEADLINE="${DEADLINE:-7200}"

log(){ echo "[$(date -u +%H:%M:%S)] [recover] $*"; }
jget(){ .venv/Scripts/python.exe -c "import sys,json;d=json.load(sys.stdin);print(d.get('$1','') or '')" 2>/dev/null; }
pmget(){ .venv/Scripts/python.exe -c "import sys,json;d=json.load(sys.stdin);print((d.get('portMappings') or {}).get('22') or '')" 2>/dev/null; }
stop_pod(){
  log "stopping pod $POD_ID"
  curl -s -X POST "$API/pods/$POD_ID/stop" -H "Authorization: Bearer $KEY" >/dev/null 2>&1
}
trap stop_pod EXIT

[ -s "$QUEUE" ] || { log "FATAL generation queue missing: $QUEUE"; exit 1; }
log "starting retained candidate pod once"
START=$(curl -s -w '\n%{http_code}' -X POST "$API/pods/$POD_ID/start" \
  -H "Authorization: Bearer $KEY")
HTTP=$(echo "$START" | tail -1); BODY=$(echo "$START" | sed '$d')
[ "$HTTP" = 200 ] || { log "BLOCKED start HTTP $HTTP: $BODY"; exit 2; }

HOST=""; PORT=""
for _ in $(seq 1 60); do
  PD=$(curl -s "$API/pods/$POD_ID" -H "Authorization: Bearer $KEY")
  ST=$(echo "$PD" | jget desiredStatus)
  HOST=$(echo "$PD" | jget publicIp); PORT=$(echo "$PD" | pmget)
  [ "$ST" = RUNNING ] && [ -n "$HOST" ] && [ -n "$PORT" ] && break
  sleep 10
done
[ -n "$HOST" ] && [ -n "$PORT" ] || { log "FATAL pod never became reachable"; exit 1; }

KEYF=/tmp/rpkey_recover; cp ~/.ssh/runpod_aria "$KEYF"; chmod 600 "$KEYF"
SSH="ssh -i $KEYF -o StrictHostKeyChecking=no -o ConnectTimeout=15"
TSSH(){ timeout 75 $SSH "$@"; }
for _ in $(seq 1 40); do
  TSSH -p "$PORT" root@"$HOST" 'test -f /workspace/checkpoints/aria_tooluse_v1/adapter_config.json' \
    2>/dev/null && break
  sleep 5
done
TSSH -p "$PORT" root@"$HOST" 'test -f /workspace/checkpoints/aria_tooluse_v1/adapter_config.json' \
  || { log "FATAL retained adapter unavailable"; exit 1; }

# Persist the irreplaceable adapter BEFORE launching more paid work.
mkdir -p "$(dirname "$LOCAL_ADAPTER")"
log "persisting candidate adapter locally before generation"
TSSH -p "$PORT" root@"$HOST" \
  'tar -C /workspace/checkpoints -czf /workspace/eval/aria_tooluse_candidate_v2.tgz aria_tooluse_v1' \
  || { log "FATAL adapter archive failed"; exit 1; }
timeout 600 scp -i "$KEYF" -o StrictHostKeyChecking=no -P "$PORT" \
  root@"$HOST":/workspace/eval/aria_tooluse_candidate_v2.tgz "$LOCAL_ADAPTER" \
  || { log "FATAL adapter persistence failed"; exit 1; }
[ -s "$LOCAL_ADAPTER" ] || { log "FATAL persisted adapter is empty"; exit 1; }
tar -tzf "$LOCAL_ADAPTER" \
  | awk '/aria_tooluse_v1\/adapter_config.json$/ { found=1 } END { exit !found }' \
  || { log "FATAL persisted adapter archive is invalid"; exit 1; }

# Pull measured state under recovery-specific names before changing any sentinel.
mkdir -p "$(dirname "$LOCAL_TRAINED")"
timeout 300 scp -i "$KEYF" -o StrictHostKeyChecking=no -P "$PORT" \
  root@"$HOST":/workspace/eval/tooluse_eval_trained.json "$LOCAL_TRAINED" \
  || log "WARN trained-eval checkpoint unavailable"
timeout 300 scp -i "$KEYF" -o StrictHostKeyChecking=no -P "$PORT" \
  root@"$HOST":/workspace/logs/tooluse_cycle.log "$LOCAL_RUN_LOG" \
  || log "WARN cycle log unavailable"

log "uploading current evaluator and train-only queue"
for src_dst in \
  "scripts/train/pod_tooluse_generate.sh:/workspace/pod_tooluse_generate.sh" \
  "scripts/train/eval_tooluse.py:/workspace/crucix/scripts/train/eval_tooluse.py" \
  "scripts/train/serve_eval_shim.py:/workspace/crucix/scripts/train/serve_eval_shim.py" \
  "scripts/train/build_tooluse_corpus.py:/workspace/crucix/scripts/train/build_tooluse_corpus.py" \
  "scripts/train/pod_selfstop_watch_v04.sh:/workspace/pod_selfstop_watch_v04.sh" \
  "$QUEUE:/workspace/datasets/aria_tooluse_dpo_generation.jsonl"; do
  src=${src_dst%%:*}; dst=${src_dst#*:}
  timeout 180 scp -i "$KEYF" -o StrictHostKeyChecking=no -P "$PORT" "$src" root@"$HOST":"$dst" \
    || { log "FATAL upload failed: $src"; exit 1; }
done

log "arming independent watchdog before generation"
TSSH -p "$PORT" root@"$HOST" \
  "rm -f /workspace/eval/_cycle_status; POD_ID=$POD_ID RP_KEY='$KEY' DEADLINE=$DEADLINE GRACE=900 COLLECT_GRACE=900 setsid nohup bash /workspace/pod_selfstop_watch_v04.sh >/workspace/logs/_generation_watch.log 2>&1 </dev/null & echo ARMED" \
  | grep -q ARMED || { log "FATAL watchdog not armed"; exit 1; }

TSSH -p "$PORT" root@"$HOST" \
  "setsid nohup bash /workspace/pod_tooluse_generate.sh >/workspace/logs/tooluse_generation.log 2>&1 </dev/null & echo STARTED" \
  | grep -q STARTED || { log "FATAL generation did not start"; exit 1; }

log "waiting for bounded generation"
for _ in $(seq 1 80); do
  RC=$(TSSH -p "$PORT" root@"$HOST" 'cat /workspace/eval/_cycle_status 2>/dev/null' 2>/dev/null | tr -d '\r[:space:]')
  [ -n "$RC" ] && break
  sleep 90
done
[ "${RC:-}" = 0 ] || { log "FATAL generation rc=${RC:-missing}"; exit 1; }

mkdir -p "$(dirname "$LOCAL_REPORT")"
timeout 300 scp -i "$KEYF" -o StrictHostKeyChecking=no -P "$PORT" \
  root@"$HOST":/workspace/eval/tooluse_train_generations.json "$LOCAL_REPORT" \
  || { log "FATAL generation report harvest failed"; exit 1; }
log "DONE adapter=$LOCAL_ADAPTER report=$LOCAL_REPORT"
