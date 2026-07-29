#!/usr/bin/env bash
# R-F3401 — SMOKE CYCLE driver. Creates a pod, runs one short tool-use SFT,
# pulls the result, and STOPS THE POD. Modelled on eval_ondemand_v07.sh.
#
# VOLUME-FREE (R-F1516). eval_ondemand_v07 pins networkVolumeId 4vdw2zmqov,
# which region-locks every pod to US-KS-2 — the datacenter that was deleting
# pods within minutes, one of them mid-train. No volume here, so the scheduler
# can place the pod anywhere with capacity. The cost is that container disk is
# EPHEMERAL: anything not pulled before the stop is gone, which is why the
# result is pulled before stop_pod runs and not after.
#
# THREE INDEPENDENT STOPS, because a pod nobody notices is §24's worst outcome:
#   1. `trap stop_pod EXIT` — covers a normal finish, an error, and a Ctrl-C.
#   2. the on-pod self-stop watcher — covers this driver dying or the laptop
#      sleeping. Since R-F3400 it also carries an absolute DEADLINE, so it stops
#      the pod even when the cycle is SIGKILLed and writes no sentinel.
#   3. the poll cap below — bounded, so the driver cannot wait forever either.
# Each covers a case the others do not.
#
# PRE-FLIGHT IS RUN LOCALLY FIRST and its exit code is honoured (§24: a cycle
# that would train on unreviewed or contaminated data is cancelled, not run).
set -uo pipefail
REPO="${REPO:-/c/code/crucix}"; cd "$REPO"
API="https://rest.runpod.io/v1"
KEY=$(grep -E '^RUNPOD_API_KEY=' .env | head -1 | cut -d= -f2- | tr -d '"' | tr -d "'" | tr -d '\r')
[ -n "$KEY" ] || { echo "[smoke] FATAL: RUNPOD_API_KEY not in .env"; exit 1; }

TRAIN_LOCAL="${TRAIN_LOCAL:-data/training/split_v1/train.jsonl}"
EVAL_LOCAL="${EVAL_LOCAL:-data/training/split_v1/eval.jsonl}"
GOLDEN="${GOLDEN:-data/eval_frozen/aria_eval_500q.jsonl}"
BASE_MODEL="${BASE_MODEL:-mistralai/Mistral-7B-Instruct-v0.3}"
PYBIN="${PYBIN:-.venv/Scripts/python.exe}"
RETRY_SECS=${RETRY_SECS:-90}
MAX_TRIES=${MAX_TRIES:-15}
POLL_CAP=${POLL_CAP:-45}          # x 120s = 1.5h hard cap on the driver's wait
DEADLINE=${DEADLINE:-10800}       # 3h absolute cap for the on-pod watcher

log(){ echo "[$(date -u +%H:%M:%S)] [smoke] $*"; }
jget(){ "$PYBIN" -c "import sys,json;d=json.load(sys.stdin);print(d.get('$1','') or '')" 2>/dev/null; }
pmget(){ "$PYBIN" -c "import sys,json;d=json.load(sys.stdin);pm=d.get('portMappings') or {};print(pm.get('22') or '')" 2>/dev/null; }

# ---- 1. pre-flight on the FREE side of the spend ---------------------------
log "pre-flight (strict) before spending anything…"
"$PYBIN" -m scripts.train.preflight_cycle \
  --train-file "$TRAIN_LOCAL" --eval-file "$EVAL_LOCAL" \
  --base-model "$BASE_MODEL" --golden-set "$GOLDEN" --strict || {
    log "BLOCKED: pre-flight failed — not starting a pod."; exit 3; }

KEYF="/tmp/rpkey_smoke"; cp ~/.ssh/runpod_aria "$KEYF"; chmod 600 "$KEYF"
SSH="ssh -i $KEYF -o StrictHostKeyChecking=no -o ConnectTimeout=15 -o ServerAliveInterval=30 -o ServerAliveCountMax=6"
TSSH(){ timeout 75 $SSH "$@"; }

# ---- 2. create (volume-free, any DC with capacity) -------------------------
POD_ID=""
for i in $(seq 1 "$MAX_TRIES"); do
  POD_ID=$("$PYBIN" scripts/train/_create_v04_pod.py 2>/dev/null | head -1 | tr -d '[:space:]')
  [ -n "$POD_ID" ] && { log "CREATED pod $POD_ID (try $i/$MAX_TRIES)"; break; }
  log "[try $i/$MAX_TRIES] no capacity — retry ${RETRY_SECS}s"; sleep "$RETRY_SECS"
done
[ -n "$POD_ID" ] || { log "GAVE UP: no capacity across all GPU types."; exit 2; }

stop_pod(){ log "stopping pod $POD_ID"; curl -s -X POST "$API/pods/$POD_ID/stop" -H "Authorization: Bearer $KEY" >/dev/null 2>&1 || true; }
trap stop_pod EXIT

# ---- 3. wait RUNNING + stable SSH ------------------------------------------
HOST=""; PORT=""
for j in $(seq 1 40); do
  PD=$(curl -s "$API/pods/$POD_ID" -H "Authorization: Bearer $KEY")
  ST=$(echo "$PD" | jget desiredStatus); HOST=$(echo "$PD" | jget publicIp); PORT=$(echo "$PD" | pmget)
  [ "$ST" = "RUNNING" ] && [ -n "$HOST" ] && [ -n "$PORT" ] && { log "RUNNING $HOST:$PORT"; break; }
  sleep 10
done
[ -n "$HOST" ] && [ -n "$PORT" ] || { log "FATAL: pod never reached RUNNING"; exit 1; }

ok=0; for j in $(seq 1 40); do
  if TSSH -p "$PORT" root@"$HOST" "echo ok" 2>/dev/null | grep -q ok; then ok=$((ok+1)); else ok=0; fi
  [ "$ok" -ge 3 ] && { log "SSH stable"; break; }; sleep 5
done
[ "$ok" -ge 3 ] || { log "FATAL: SSH never stabilised"; exit 1; }
sleep 10

RSCP(){ local s="$1" d="$2" t; for t in 1 2 3 4 5; do
  timeout 120 scp -i "$KEYF" -o StrictHostKeyChecking=no -o ConnectTimeout=15 -P "$PORT" "$s" root@"$HOST":"$d" 2>/dev/null && return 0
  log "scp retry $t $(basename "$s")"; sleep 10; done; return 1; }

# ---- 4. push ---------------------------------------------------------------
for j in 1 2 3; do TSSH -p "$PORT" root@"$HOST" \
  "mkdir -p /workspace/datasets /workspace/crucix/scripts/train /workspace/logs /workspace/eval /workspace/checkpoints" 2>/dev/null && break; sleep 8; done
RSCP scripts/train/pod_smoke_sft.sh          /workspace/                                   || { log "FATAL scp runner"; exit 1; }
RSCP scripts/train/pod_selfstop_watch_v04.sh /workspace/                                   || { log "FATAL scp watcher"; exit 1; }
RSCP scripts/train/sft_train.py              /workspace/crucix/scripts/train/sft_train.py  || { log "FATAL scp trainer"; exit 1; }
RSCP "$TRAIN_LOCAL" /workspace/datasets/aria_tooluse_train.jsonl                            || { log "FATAL scp train"; exit 1; }
RSCP "$EVAL_LOCAL"  /workspace/datasets/aria_tooluse_eval.jsonl                             || { log "FATAL scp eval"; exit 1; }

# ---- 5. arm the watcher BEFORE launching the work --------------------------
# Order matters: if the run were launched first and this driver then died, the
# pod would have no independent stop at all.
log "arming on-pod self-stop watcher (DEADLINE=${DEADLINE}s)…"
TSSH -p "$PORT" root@"$HOST" \
  "POD_ID=$POD_ID RP_KEY='$KEY' GRACE=600 DEADLINE=$DEADLINE setsid nohup bash /workspace/pod_selfstop_watch_v04.sh >/workspace/logs/_selfstop.log 2>&1 </dev/null & echo ARMED" \
  | grep -q ARMED || { log "FATAL: watcher did not arm — refusing to run unattended"; exit 1; }

log "launching smoke SFT detached…"
TSSH -p "$PORT" root@"$HOST" \
  "rm -f /workspace/eval/_cycle_status; BASE_MODEL='$BASE_MODEL' setsid nohup bash /workspace/pod_smoke_sft.sh >/workspace/logs/smoke_cycle.log 2>&1 </dev/null & echo STARTED" \
  | grep -q STARTED || { log "FATAL launch"; exit 1; }

# ---- 6. poll (bounded) -----------------------------------------------------
log "polling (cap $((POLL_CAP*2)) min)…"; RC=""
for i in $(seq 1 "$POLL_CAP"); do
  sleep 120
  OUT=$(TSSH -p "$PORT" root@"$HOST" 'printf "RC=%s\n" "$(cat /workspace/eval/_cycle_status 2>/dev/null)"; tail -1 /workspace/logs/smoke_cycle.log 2>/dev/null' 2>/dev/null | tr -d '\r')
  RC=$(printf '%s\n' "$OUT" | sed -n 's/^RC=//p' | tr -d ' ')
  log "[$i/$POLL_CAP] $(printf '%s\n' "$OUT" | grep -v '^RC=' | tail -1)"
  [ -n "$RC" ] && { log "cycle finished (exit $RC)"; break; }
done

# ---- 7. pull BEFORE stopping (container disk is ephemeral) -----------------
mkdir -p data/eval_reports
PULL(){ timeout 120 scp -i "$KEYF" -o StrictHostKeyChecking=no -P "$PORT" root@"$HOST":"$1" "$2" 2>/dev/null; }
PULL /workspace/eval/aria_tooluse_smoke.json data/eval_reports/aria_tooluse_smoke.json || log "(result json not pulled)"
PULL /workspace/logs/smoke_sft.log           data/eval_reports/aria_tooluse_smoke_sft.log || log "(train log not pulled)"

log "=== RESULT ==="
if [ -s data/eval_reports/aria_tooluse_smoke.json ]; then
  cat data/eval_reports/aria_tooluse_smoke.json
else
  log "NO RESULT PULLED — treat this cycle as FAILED, not as a pass."
fi
[ "${RC:-}" = "0" ] || log "NOTE: cycle rc='${RC:-<none>}' — a non-zero or empty rc is NOT a pass."

stop_pod
log "DONE — pod $POD_ID stopped."
