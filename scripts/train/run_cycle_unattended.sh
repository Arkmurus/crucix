#!/usr/bin/env bash
# R-F3718 — versioned supervisor for a paid tool-use cycle.
# Pod safety remains independent: launch arms the on-pod deadline before work.
set -uo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd) || exit 1
REPO="${REPO:-$(cd "$SCRIPT_DIR/../.." && pwd)}"
cd "$REPO" || { echo "[driver] FATAL: repository unavailable: $REPO"; exit 1; }
API="https://rest.runpod.io/v1"
PYBIN="${PYBIN:-.venv/Scripts/python.exe}"
STATE_FILE="${STATE_FILE:-data/eval_reports/.tooluse_pod_state}"
LOG_FILE="${LOG_FILE:-data/eval_reports/aria_tooluse_unattended.log}"
POLL_SECS="${POLL_SECS:-180}"
MAX_POLLS="${MAX_POLLS:-80}"

mkdir -p "$(dirname "$LOG_FILE")"
log(){ printf '[%s] [driver] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" | tee -a "$LOG_FILE"; }
KEY=$(grep -E '^RUNPOD_API_KEY=' .env | head -1 | cut -d= -f2- | tr -d '"' | tr -d "'" | tr -d '\r')
[ -n "$KEY" ] || { log "FATAL: RUNPOD_API_KEY not in .env"; exit 1; }

# Exactly RUNNING / NOT_RUNNING / UNREADABLE. Empty or malformed output is
# unknown, never evidence that work lives.
pod_state(){
  local body parsed
  body=$(curl -fsS --connect-timeout 10 --max-time 20 "$API/pods/$POD_ID" \
    -H "Authorization: Bearer $KEY" 2>/dev/null) || { echo UNREADABLE; return; }
  parsed=$(printf '%s' "$body" | "$PYBIN" -c \
    "import json,sys; d=json.load(sys.stdin); print(str(d.get('desiredStatus') or '').upper())" \
    2>/dev/null) || { echo UNREADABLE; return; }
  case "$parsed" in
    RUNNING|CREATED|STARTING|RESTARTING) echo RUNNING ;;
    EXITED|STOPPED|TERMINATED) echo NOT_RUNNING ;;
    *) echo UNREADABLE ;;
  esac
}

log "launching paid cycle"
bash "$SCRIPT_DIR/tooluse_launch.sh" 2>&1 | tee -a "$LOG_FILE"
launch_rc=${PIPESTATUS[0]}
[ "$launch_rc" = 0 ] || { log "FATAL: launch rc=$launch_rc"; exit "$launch_rc"; }
[ -s "$STATE_FILE" ] || { log "FATAL: launch wrote no handoff"; exit 1; }
. "$STATE_FILE"

KEYF="${KEYF:-/tmp/rpkey_unattended}"
cp ~/.ssh/runpod_aria "$KEYF" || { log "FATAL: SSH key unavailable"; exit 1; }
chmod 600 "$KEYF"
sentinel(){
  timeout 35 ssh -i "$KEYF" -o StrictHostKeyChecking=no -o ConnectTimeout=10 \
    -p "$PORT" root@"$HOST" 'cat /workspace/eval/_cycle_status 2>/dev/null' \
    2>/dev/null | tr -d '\r[:space:]'
}

log "handoff loaded: pod=$POD_ID host=$HOST:$PORT"
deadline_collection=0
for i in $(seq 1 "$MAX_POLLS"); do
  rc=$(sentinel)
  if [ -n "$rc" ]; then
    log "completion sentinel rc=$rc; harvesting"
    bash "$SCRIPT_DIR/tooluse_harvest.sh" 2>&1 | tee -a "$LOG_FILE"
    harvest_rc=${PIPESTATUS[0]}
    [ "$rc" = 0 ] && [ "$harvest_rc" = 0 ] && exit 0
    exit 1
  fi
  state=$(pod_state)
  elapsed=$(( $(date -u +%s) - LAUNCHED_AT ))
  if [ "$elapsed" -ge "$DEADLINE" ] && [ "$state" = RUNNING ]; then
    # The watcher is now inside COLLECT_GRACE. Waiting for EXITED makes the
    # pod-local disk unreachable, which is how the Aug-4 diagnostics were lost.
    deadline_collection=$((deadline_collection + 1))
    log "deadline reached; collection attempt $deadline_collection while pod is reachable"
    bash "$SCRIPT_DIR/tooluse_harvest.sh" --leave-running 2>&1 | tee -a "$LOG_FILE"
  fi
  case "$state" in
    RUNNING) log "poll $i/$MAX_POLLS: RUNNING; sentinel absent" ;;
    NOT_RUNNING)
      log "poll $i/$MAX_POLLS: NOT_RUNNING with no sentinel; salvage attempt"
      bash "$SCRIPT_DIR/tooluse_harvest.sh" 2>&1 | tee -a "$LOG_FILE"
      exit 1 ;;
    UNREADABLE)
      log "poll $i/$MAX_POLLS: UNREADABLE; not assuming RUNNING; SSH sentinel absent" ;;
  esac
  sleep "$POLL_SECS"
done
log "poll bound reached; non-destructive collection attempt"
bash "$SCRIPT_DIR/tooluse_harvest.sh" --leave-running 2>&1 | tee -a "$LOG_FILE"
exit 1
