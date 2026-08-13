#!/usr/bin/env bash
# R-F3744 — fresh-pod collection of candidate failures over a TRAIN-only queue.
set -uo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd) || exit 1
REPO="${REPO:-$(cd "$SCRIPT_DIR/../.." && pwd)}"
cd "$REPO" || { echo "[generation] FATAL repository unavailable"; exit 1; }

API="https://rest.runpod.io/v1"
PYBIN="${PYBIN:-.venv/Scripts/python.exe}"
BASE_ONLY="${BASE_ONLY:-0}"
QUEUE="${QUEUE:-data/training/tooluse_dpo_generation_v2.jsonl}"
EVAL_LOCAL="${EVAL_LOCAL:-data/training/split_v2/eval.jsonl}"
GOLDEN="${GOLDEN:-data/eval_frozen/aria_eval_500q.jsonl}"
ADAPTER_LOCAL="${ADAPTER_LOCAL:-data/training/checkpoints/aria_tooluse_candidate_latest.tgz}"
REPORT_LOCAL="${REPORT_LOCAL:-data/eval_reports/aria_tooluse_train_generations_v2.json}"
STATE_FILE="${STATE_FILE:-data/eval_reports/.tooluse_generation_pod_state}"
# Measured on the preceding host: ~27 min for the lean 311 MB upload and
# ~74 min for 168 generations. 155 rows plus model load is ~100 min; 8100s is
# that measured envelope plus >20%, not a retry/timeout guess.
# Upload and generation are separately bounded. One shared clock made slow but
# healthy upload consume the generation budget. Measurements from R-F3744:
# upload <=64 min; 168-row generation ~=74 min. Each phase gets its measured
# envelope plus >20% rather than a blanket extension.
UPLOAD_DEADLINE="${UPLOAD_DEADLINE:-5400}"
GENERATION_DEADLINE="${GENERATION_DEADLINE:-7200}"
UPLOAD_SLICE="${UPLOAD_SLICE:-720}"
UPLOAD_SLICES="${UPLOAD_SLICES:-7}"
GRACE="${GRACE:-900}"
COLLECT_GRACE="${COLLECT_GRACE:-900}"
RETRY_SECS="${RETRY_SECS:-90}"
MAX_TRIES="${MAX_TRIES:-15}"
EXPECTED_ROWS=$(grep -cve '^[[:space:]]*$' "$QUEUE" 2>/dev/null || true)

log(){ echo "[$(date -u +%H:%M:%S)] [generation] $*"; }
jget(){ "$PYBIN" -c "import sys,json;d=json.load(sys.stdin);print(d.get('$1','') or '')" 2>/dev/null; }
pmget(){ "$PYBIN" -c "import sys,json;d=json.load(sys.stdin);print((d.get('portMappings') or {}).get('22') or '')" 2>/dev/null; }
pod_state(){
  local body parsed
  body=$(curl -fsS --connect-timeout 10 --max-time 20 "$API/pods/$POD_ID" \
    -H "Authorization: Bearer $KEY" 2>/dev/null) || { echo UNREADABLE; return; }
  parsed=$(printf '%s' "$body" | jget desiredStatus) || { echo UNREADABLE; return; }
  case "$parsed" in
    RUNNING|CREATED|STARTING|RESTARTING) echo RUNNING ;;
    EXITED|STOPPED|TERMINATED) echo NOT_RUNNING ;;
    *) echo UNREADABLE ;;
  esac
}
KEY=$(grep -E '^RUNPOD_API_KEY=' .env | head -1 | cut -d= -f2- | tr -d '"' | tr -d "'" | tr -d '\r')
[ -n "$KEY" ] || { log "FATAL API key unavailable"; exit 1; }
[ -s "$QUEUE" ] || { log "FATAL queue missing: $QUEUE"; exit 1; }
[ "$EXPECTED_ROWS" -gt 0 ] || { log "FATAL queue has no rows"; exit 1; }
if [ "$BASE_ONLY" != 1 ]; then
  [ -s "$ADAPTER_LOCAL" ] || { log "FATAL adapter missing: $ADAPTER_LOCAL"; exit 1; }
  ADAPTER_SHA256=$(sha256sum "$ADAPTER_LOCAL" | awk '{print $1}')
  [ -n "$ADAPTER_SHA256" ] || { log "FATAL adapter hash unavailable"; exit 1; }
  tar -tzf "$ADAPTER_LOCAL" | awk '/\/adapter_config.json$/ { found=1 } END { exit !found }' \
    || { log "FATAL adapter archive invalid"; exit 1; }
  ARCHIVE_ADAPTER_DIR=$(tar -tzf "$ADAPTER_LOCAL" \
    | awk -F/ '/\/adapter_config.json$/ { print $1; exit }')
  case "$ARCHIVE_ADAPTER_DIR" in
    ""|*/*|*".."*) log "FATAL unsafe adapter directory in archive"; exit 1 ;;
  esac
  REMOTE_ADAPTER="/workspace/checkpoints/$ARCHIVE_ADAPTER_DIR"
fi

log "strict preflight of train-only generation queue"
"$PYBIN" -m scripts.train.preflight_cycle \
  --train-file "$QUEUE" --eval-file "$EVAL_LOCAL" \
  --base-model mistralai/Mistral-7B-Instruct-v0.3 \
  --golden-set "$GOLDEN" --strict || exit 3

POD_ID=""; HOST=""; PORT=""; ARMED=0
release(){
  [ -z "$POD_ID" ] && return 0
  log "stopping pod $POD_ID"
  for attempt in 1 2 3; do
    curl.exe -s -X POST "$API/pods/$POD_ID/stop" \
      -H "Authorization: Bearer $KEY" >/dev/null 2>&1 || true
    if [ "$(pod_state)" = NOT_RUNNING ]; then
      log "verified pod $POD_ID stopped"
      return 0
    fi
    log "stop unverified attempt $attempt/3"
    sleep 10
  done
  log "FATAL pod $POD_ID stop unverified after 3 attempts"
  return 1
}
trap release EXIT

for i in $(seq 1 "$MAX_TRIES"); do
  POD_ID=$("$PYBIN" scripts/train/_create_v04_pod.py 2>/dev/null | head -1 | tr -d '[:space:]')
  [ -n "$POD_ID" ] || { log "create rejected $i/$MAX_TRIES"; sleep "$RETRY_SECS"; continue; }
  log "created $POD_ID; waiting for RUNNING"
  for _ in $(seq 1 40); do
    PD=$(curl -s "$API/pods/$POD_ID" -H "Authorization: Bearer $KEY")
    ST=$(echo "$PD" | jget desiredStatus)
    HOST=$(echo "$PD" | jget publicIp); PORT=$(echo "$PD" | pmget)
    [ "$ST" = RUNNING ] && [ -n "$HOST" ] && [ -n "$PORT" ] && break
    sleep 10
  done
  [ -n "$HOST" ] && [ -n "$PORT" ] && break
  release; POD_ID=""; sleep "$RETRY_SECS"
done
[ -n "$POD_ID" ] && [ -n "$HOST" ] && [ -n "$PORT" ] \
  || { log "BLOCKED no GPU capacity"; exit 2; }

KEYF=/tmp/rpkey_generation; cp ~/.ssh/runpod_aria "$KEYF"; chmod 600 "$KEYF"
SSH="ssh -i $KEYF -o StrictHostKeyChecking=no -o ConnectTimeout=15 -o ServerAliveInterval=30 -o ServerAliveCountMax=6"
TSSH(){ timeout 75 $SSH "$@"; }
arm_watchdog(){
  local command=$1
  for attempt in 1 2 3; do
    TSSH -p "$PORT" root@"$HOST" \
      "if [ -s /workspace/eval/_watchdog_pid ]; then kill \$(cat /workspace/eval/_watchdog_pid) 2>/dev/null || true; fi; $command" \
      >/dev/null 2>&1 || true
    if TSSH -p "$PORT" root@"$HOST" \
        'if test -s /workspace/eval/_watchdog_pid; then kill -0 "$(cat /workspace/eval/_watchdog_pid)"; else exit 1; fi' \
        >/dev/null 2>&1; then
      log "watchdog arm verified"
      return 0
    fi
    log "watchdog arm unverified attempt $attempt/3"
    sleep 5
  done
  log "FATAL watchdog arm not live after 3 attempts"
  return 1
}
ok=0
for _ in $(seq 1 40); do
  if TSSH -p "$PORT" root@"$HOST" 'echo ok' 2>/dev/null | grep -q ok; then ok=$((ok+1)); else ok=0; fi
  [ "$ok" -ge 3 ] && break
  sleep 5
done
[ "$ok" -ge 3 ] || { log "FATAL SSH unstable"; exit 1; }

TSSH -p "$PORT" root@"$HOST" \
  'mkdir -p /workspace/checkpoints /workspace/datasets /workspace/eval /workspace/logs /workspace/crucix/scripts/train' \
  || { log "FATAL remote layout"; exit 1; }

RSCP(){
  timeout 180 scp -i "$KEYF" -o StrictHostKeyChecking=no -o ConnectTimeout=15 \
    -P "$PORT" "$1" root@"$HOST":"$2" 2>/dev/null
}
for item in \
  "scripts/train/pod_tooluse_generate.sh:/workspace/pod_tooluse_generate.sh" \
  "scripts/train/pod_selfstop_watch_v04.sh:/workspace/pod_selfstop_watch_v04.sh" \
  "scripts/train/eval_tooluse.py:/workspace/crucix/scripts/train/eval_tooluse.py" \
  "scripts/train/serve_eval_shim.py:/workspace/crucix/scripts/train/serve_eval_shim.py" \
  "scripts/train/build_tooluse_corpus.py:/workspace/crucix/scripts/train/build_tooluse_corpus.py" \
  "$QUEUE:/workspace/datasets/aria_tooluse_dpo_generation.jsonl"; do
  src=${item%%:*}; dst=${item#*:}
  RSCP "$src" "$dst" || { log "FATAL upload $src"; exit 1; }
done
mkdir -p "$(dirname "$STATE_FILE")"
arm_watchdog \
  "POD_ID=$POD_ID RP_KEY='$KEY' DEADLINE=$UPLOAD_DEADLINE GRACE=$GRACE COLLECT_GRACE=$COLLECT_GRACE setsid nohup bash /workspace/pod_selfstop_watch_v04.sh >/workspace/logs/_upload_watch.log 2>&1 </dev/null & echo \$! >/workspace/eval/_watchdog_pid" \
  || exit 1
ARMED=1
{ echo "POD_ID=$POD_ID"; echo "HOST=$HOST"; echo "PORT=$PORT"; echo "LAUNCHED_AT=$(date -u +%s)"; } > "$STATE_FILE"

if [ "$BASE_ONLY" != 1 ]; then
log "uploading validated serving adapter with resumable SFTP"
UPLOAD_OK=0
for slice in $(seq 1 "$UPLOAD_SLICES"); do
  if TSSH -p "$PORT" root@"$HOST" 'test -f /workspace/aria_tooluse_candidate.tgz' \
      >/dev/null 2>&1; then
    SFTP_UPLOAD=reput
  else
    SFTP_UPLOAD=put
  fi
  log "adapter transfer slice $slice/$UPLOAD_SLICES mode=$SFTP_UPLOAD"
  if printf '%s %s %s\n' "$SFTP_UPLOAD" "$ADAPTER_LOCAL" \
      /workspace/aria_tooluse_candidate.tgz \
      | timeout "$UPLOAD_SLICE" sftp -b - -i "$KEYF" \
          -o StrictHostKeyChecking=no -o ConnectTimeout=20 \
          -P "$PORT" root@"$HOST" >/dev/null; then
    UPLOAD_OK=1
    break
  fi
  STATE=$(pod_state)
  REMOTE_BYTES=$(TSSH -p "$PORT" root@"$HOST" \
    'stat -c %s /workspace/aria_tooluse_candidate.tgz 2>/dev/null || echo 0' \
    2>/dev/null | tr -d '\r[:space:]')
  log "slice incomplete: remote_bytes=${REMOTE_BYTES:-unknown} state=$STATE"
  [ "$STATE" = RUNNING ] || break
done
[ "$UPLOAD_OK" = 1 ] || { log "FATAL bounded adapter upload incomplete"; exit 1; }
TSSH -p "$PORT" root@"$HOST" \
  "printf '%s  %s\n' '$ADAPTER_SHA256' /workspace/aria_tooluse_candidate.tgz | sha256sum -c - && tar -tzf /workspace/aria_tooluse_candidate.tgz | awk '/\\/adapter_config.json$/ { found=1 } END { exit !found }' && tar -xzf /workspace/aria_tooluse_candidate.tgz -C /workspace/checkpoints" \
  || { log "FATAL remote adapter validation/extract"; exit 1; }
fi

arm_watchdog \
  "rm -f /workspace/eval/_cycle_status; POD_ID=$POD_ID RP_KEY='$KEY' DEADLINE=$GENERATION_DEADLINE GRACE=$GRACE COLLECT_GRACE=$COLLECT_GRACE setsid nohup bash /workspace/pod_selfstop_watch_v04.sh >/workspace/logs/_generation_watch.log 2>&1 </dev/null & echo \$! >/workspace/eval/_watchdog_pid" \
  || exit 1
POD_ENV="BASE_ONLY=$BASE_ONLY"
[ "$BASE_ONLY" = 1 ] || POD_ENV="$POD_ENV ADAPTER='$REMOTE_ADAPTER'"
TSSH -p "$PORT" root@"$HOST" \
  "$POD_ENV setsid nohup bash /workspace/pod_tooluse_generate.sh >/workspace/logs/tooluse_generation.log 2>&1 </dev/null & echo STARTED" \
  | grep -q STARTED || { log "FATAL generation start"; exit 1; }

log "generation started; waiting for completion sentinel"
RC=""
CONTROL_STOPPED=0
RSCP_PULL(){ timeout 600 scp -i "$KEYF" -o StrictHostKeyChecking=no -P "$PORT" root@"$HOST":"$1" "$2" 2>/dev/null; }
for i in $(seq 1 100); do
  RC=$(TSSH -p "$PORT" root@"$HOST" 'cat /workspace/eval/_cycle_status 2>/dev/null' 2>/dev/null | tr -d '\r[:space:]')
  [ -n "$RC" ] && break
  if [ $((i % 5)) -eq 0 ]; then
    mkdir -p "$(dirname "$REPORT_LOCAL")"
    RSCP_PULL /workspace/eval/tooluse_train_generations.json "${REPORT_LOCAL}.partial" || true
  fi
  STATE=$(pod_state)
  if [ "$STATE" = NOT_RUNNING ]; then
    CONTROL_STOPPED=1
    log "pod became NOT_RUNNING before a completion sentinel"
    break
  fi
  [ "$STATE" = UNREADABLE ] && log "control plane unreadable; not assuming generation is running"
  sleep 90
done
harvest_diagnostics(){
  mkdir -p data/eval_reports
  RSCP_PULL /workspace/logs/tooluse_generation.log \
    data/eval_reports/aria_tooluse_generation_run.log || true
  RSCP_PULL /workspace/logs/shim_generation.log \
    data/eval_reports/aria_tooluse_generation_shim.log || true
}
[ "$RC" = 0 ] || {
  harvest_diagnostics
  log "FATAL generation rc=${RC:-missing}; diagnostics harvested"
  exit 1
}

mkdir -p "$(dirname "$REPORT_LOCAL")"
RSCP_PULL /workspace/eval/tooluse_train_generations.json "$REPORT_LOCAL" \
  || { log "FATAL report harvest"; exit 1; }
"$PYBIN" - "$REPORT_LOCAL" "$EXPECTED_ROWS" <<'PY' || exit 1
import json, sys
d = json.load(open(sys.argv[1], encoding="utf-8"))
expected = int(sys.argv[2])
if (d.get("complete") is not True or len(d.get("rows") or []) != expected
        or d.get("total") != expected):
    raise SystemExit(f"generation report did not prove {expected} complete rows")
print(f"verified {expected} complete train-only generations")
PY
log "DONE report=$REPORT_LOCAL"
