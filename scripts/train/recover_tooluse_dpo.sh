#!/usr/bin/env bash
# R-F3768 — recover the retained full-epoch adapter, then run eval without retraining.
set -uo pipefail
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd) || exit 1
REPO=$(cd "$SCRIPT_DIR/../.." && pwd) || exit 1; cd "$REPO" || exit 1
API=https://rest.runpod.io/v1; PYBIN=.venv/Scripts/python.exe
STATE_FILE="${STATE_FILE:-data/eval_reports/.tooluse_dpo_pod_state}"; . "$STATE_FILE"
POD_ID="${POD_ID_OVERRIDE:-$POD_ID}"
: "${POD_ID:?POD_ID missing}"
OUTPUT_LOCAL="${OUTPUT_LOCAL:-data/training/checkpoints/aria_tooluse_dpo_v2.tgz}"
REPORT_LOCAL="${REPORT_LOCAL:-data/eval_reports/aria_tooluse_dpo_eval.json}"
KEY=$(grep -E '^RUNPOD_API_KEY=' .env | head -1 | cut -d= -f2- | tr -d '"' | tr -d "'" | tr -d '\r')
log(){ echo "[$(date -u +%H:%M:%S)] [dpo-recover] $*"; }
jget(){ "$PYBIN" -c "import sys,json;d=json.load(sys.stdin);print(d.get('$1','') or '')" 2>/dev/null; }
pmget(){ "$PYBIN" -c "import sys,json;d=json.load(sys.stdin);print((d.get('portMappings') or {}).get('22') or '')" 2>/dev/null; }
stop(){ log "stopping pod $POD_ID"; curl -s -X POST "$API/pods/$POD_ID/stop" -H "Authorization: Bearer $KEY" >/dev/null 2>&1; }
trap stop EXIT
KEYF=/tmp/rpkey_dpo_recover; cp ~/.ssh/runpod_aria "$KEYF"; chmod 600 "$KEYF"
SSH="ssh -i $KEYF -o StrictHostKeyChecking=no -o ConnectTimeout=2"
TSSH(){ timeout 15 $SSH "$@"; }
START=$(curl -s -w '\n%{http_code}' -X POST "$API/pods/$POD_ID/start" -H "Authorization: Bearer $KEY")
HTTP=$(printf '%s' "$START" | tail -1); BODY=$(printf '%s' "$START" | sed '$d')
[ "$HTTP" = 200 ] || { log "BLOCKED start HTTP $HTTP: $BODY"; exit 2; }
HOST=""; PORT=""; SECURED=0
for _ in $(seq 1 120); do
  PD=$(curl -s "$API/pods/$POD_ID" -H "Authorization: Bearer $KEY")
  ST=$(printf '%s' "$PD" | jget desiredStatus); HOST=$(printf '%s' "$PD" | jget publicIp); PORT=$(printf '%s' "$PD" | pmget)
  case "$ST" in EXITED|STOPPED|TERMINATED) log "FATAL pod returned to $ST before recovery secured"; exit 1;; esac
  if [ "$ST" = RUNNING ] && [ -n "$HOST" ] && [ -n "$PORT" ]; then
    if TSSH -p "$PORT" root@"$HOST" "pkill -f '[p]od_selfstop_watch_v04.sh' 2>/dev/null || true; pkill -f '[p]od_tooluse_dpo.sh|[e]val_tooluse.py|[s]erve_eval_shim.py' 2>/dev/null || true; echo RECOVERY_READY" 2>/dev/null | grep -q RECOVERY_READY; then
      SECURED=1; break
    fi
  fi
  sleep 1
done
[ "$SECURED" = 1 ] || { log "FATAL retained pod unavailable before stale watchdog fired"; exit 1; }
SSH="ssh -i $KEYF -o StrictHostKeyChecking=no -o ConnectTimeout=15"
TSSH(){ timeout 75 $SSH "$@"; }
for _ in $(seq 1 40); do TSSH -p "$PORT" root@"$HOST" 'test -f /workspace/checkpoints/aria_tooluse_dpo_v2/adapter_config.json' 2>/dev/null && break; sleep 5; done
TSSH -p "$PORT" root@"$HOST" 'test -f /workspace/checkpoints/aria_tooluse_dpo_v2/adapter_config.json' || { log "FATAL retained DPO adapter missing"; exit 1; }
mkdir -p "$(dirname "$OUTPUT_LOCAL")" "$(dirname "$REPORT_LOCAL")"
log "persisting full-epoch adapter before evaluation"
timeout 900 scp -i "$KEYF" -o StrictHostKeyChecking=no -P "$PORT" root@"$HOST":/workspace/eval/aria_tooluse_dpo_adapter.tgz "$OUTPUT_LOCAL" || exit 1
tar -tzf "$OUTPUT_LOCAL" | awk '/\/adapter_config.json$/ { found=1 } END { exit !found }' || { log "FATAL recovered adapter invalid"; exit 1; }
# R-F4350 (C-295) — the pod runner sources hf_cache_select.sh and fails
# CLOSED without it, so the selector ships with the runner.
for item in "scripts/train/hf_cache_select.sh:/workspace/hf_cache_select.sh" "scripts/train/pod_tooluse_dpo.sh:/workspace/pod_tooluse_dpo.sh" "scripts/train/eval_tooluse.py:/workspace/crucix/scripts/train/eval_tooluse.py" "scripts/train/build_tooluse_corpus.py:/workspace/crucix/scripts/train/build_tooluse_corpus.py" "scripts/train/serve_eval_shim.py:/workspace/crucix/scripts/train/serve_eval_shim.py" "scripts/train/pod_selfstop_watch_v04.sh:/workspace/pod_selfstop_watch_v04.sh"; do
  src=${item%%:*}; dst=${item#*:}; timeout 180 scp -i "$KEYF" -o StrictHostKeyChecking=no -P "$PORT" "$src" root@"$HOST":"$dst" || exit 1
done
TSSH -p "$PORT" root@"$HOST" "rm -f /workspace/eval/_cycle_status; POD_ID=$POD_ID RP_KEY='$KEY' DEADLINE=7200 GRACE=900 COLLECT_GRACE=900 setsid nohup bash /workspace/pod_selfstop_watch_v04.sh >/workspace/logs/_recovery_eval_watch.log 2>&1 </dev/null & echo ARMED" | grep -q ARMED || exit 1
TSSH -p "$PORT" root@"$HOST" 'SKIP_TRAIN=1 setsid nohup bash /workspace/pod_tooluse_dpo.sh >/workspace/logs/tooluse_dpo_recovery_eval.log 2>&1 </dev/null & echo STARTED' | grep -q STARTED || exit 1
RC=""
for _ in $(seq 1 100); do RC=$(TSSH -p "$PORT" root@"$HOST" 'cat /workspace/eval/_cycle_status 2>/dev/null' 2>/dev/null | tr -d '\r[:space:]'); [ -n "$RC" ] && break; sleep 90; done
[ "$RC" = 0 ] || { timeout 300 scp -i "$KEYF" -o StrictHostKeyChecking=no -P "$PORT" root@"$HOST":/workspace/logs/tooluse_dpo_recovery_eval.log data/eval_reports/aria_tooluse_dpo_recovery_eval.log || true; log "FATAL eval rc=${RC:-missing}"; exit 1; }
timeout 600 scp -i "$KEYF" -o StrictHostKeyChecking=no -P "$PORT" root@"$HOST":/workspace/eval/aria_tooluse_dpo_eval.json "$REPORT_LOCAL" || exit 1
"$PYBIN" - "$REPORT_LOCAL" <<'PY' || exit 1
import json, sys
d=json.load(open(sys.argv[1], encoding="utf-8")); n=168
assert d.get("complete") is True and d.get("total")==n and len(d.get("rows") or [])==n
print("verified retained-adapter held-out eval: n=168")
PY
log "DONE adapter=$OUTPUT_LOCAL report=$REPORT_LOCAL"
