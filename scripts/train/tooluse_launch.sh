#!/usr/bin/env bash
# R-F3420 — LAUNCH ONLY. Create a pod, push, arm the watchdog, start the cycle
# detached, record the handoff, and EXIT. Runs in about two minutes.
#
# WHY THIS EXISTS. The combined driver ran ~35 minutes and was KILLED externally
# — the behaviour this repo already records for background processes on this
# machine. Its `trap stop_pod EXIT` never ran and the pod billed until it was
# stopped by hand. The three "independent" stops were not independent: the local
# trap needed the driver to exit cleanly, the bounded poll lived inside that same
# driver, and the on-pod watchdog waits for the completion sentinel or its
# deadline — with the cycle still legitimately running, neither fired.
#
# The fix is to remove the dependency, not to lengthen a timeout. Nothing here
# waits for the cycle, so a kill costs nothing: the pod keeps working and stops
# itself, and `tooluse_harvest.sh` collects whenever.
#
# THIS SCRIPT DELIBERATELY DOES NOT STOP THE POD ON EXIT. That is the point. It
# DOES release a pod it failed to set up — a pod that never received the work is
# a leak, not a running job.
#
# THE WATCHDOG DEADLINE IS NOW THE ONLY BACKSTOP for an unharvested pod, so it
# is one cycle envelope plus margin rather than six hours. Six hours of a
# $1.49/hr GPU is $9 for a run nobody collected.
set -uo pipefail
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd) || exit 1
REPO="${REPO:-$(cd "$SCRIPT_DIR/../.." && pwd)}"
cd "$REPO" || { echo "[launch] FATAL: repository unavailable: $REPO"; exit 1; }
API="https://rest.runpod.io/v1"
KEY=$(grep -E '^RUNPOD_API_KEY=' .env | head -1 | cut -d= -f2- | tr -d '"' | tr -d "'" | tr -d '\r')
[ -n "$KEY" ] || { echo "[launch] FATAL: RUNPOD_API_KEY not in .env"; exit 1; }

TRAIN_LOCAL="${TRAIN_LOCAL:-data/training/split_v1/train.jsonl}"
EVAL_LOCAL="${EVAL_LOCAL:-data/training/split_v1/eval.jsonl}"
GEN_LOCAL="${GEN_LOCAL:-$TRAIN_LOCAL}"
GOLDEN="${GOLDEN:-data/eval_frozen/aria_eval_500q.jsonl}"
BASE_MODEL="${BASE_MODEL:-mistralai/Mistral-7B-Instruct-v0.3}"
PYBIN="${PYBIN:-.venv/Scripts/python.exe}"
EPOCHS="${EPOCHS:-3}"
# R-F3733 — checkpoint measurement is the critical cycle. Preference generation
# is a separate follow-on job; letting it trail the eval caused a complete,
# measured checkpoint to miss its sentinel and look failed.
GEN_TRAIN="${GEN_TRAIN:-0}"
GEN_LIMIT="${GEN_LIMIT:-150}"
RETRY_SECS=${RETRY_SECS:-90}
MAX_TRIES=${MAX_TRIES:-15}
START_WAIT_TICKS=${START_WAIT_TICKS:-40}
# R-F3445 - DERIVED FROM MEASUREMENT, not from feel. Setting this by intuition
# cost a run: I extended by 45 min when the work needed 45 min and 9 seconds.
# The arithmetic, from observed cycles:
#     base eval   168 rows          ~16 min
#     SFT         488 rows, 3 ep    ~12 min
#     trained eval 168 rows         ~45 min  (41 observed; 88 when max_tokens
#                                             was uncapped - now capped at 700)
#     generation  150 rows          ~37 min
#                                   -------
#                                   ~110 min  + 20% margin = ~132
DEADLINE=${DEADLINE:-8100}
# Post-completion idle before the pod stops itself: enough to notice and harvest,
# short enough that forgetting costs cents rather than dollars.
GRACE=${GRACE:-900}
# R-F3445 - window to collect artefacts that already exist when the DEADLINE
# fires. Without it the deadline destroyed a report nine seconds old.
COLLECT_GRACE=${COLLECT_GRACE:-900}
STATE_FILE="${STATE_FILE:-data/eval_reports/.tooluse_pod_state}"

log(){ echo "[$(date -u +%H:%M:%S)] [launch] $*"; }
jget(){ "$PYBIN" -c "import sys,json;d=json.load(sys.stdin);print(d.get('$1','') or '')" 2>/dev/null; }
pmget(){ "$PYBIN" -c "import sys,json;d=json.load(sys.stdin);pm=d.get('portMappings') or {};print(pm.get('22') or '')" 2>/dev/null; }
release(){ [ -n "${POD_ID:-}" ] && { log "releasing pod $POD_ID"; curl -s -X POST "$API/pods/$POD_ID/stop" -H "Authorization: Bearer $KEY" >/dev/null 2>&1; }; return 0; }
die(){ log "FATAL: $*"; release; exit 1; }

# ---- pre-flight on the free side of the spend -------------------------------
log "pre-flight (strict)…"
"$PYBIN" -m scripts.train.preflight_cycle \
  --train-file "$TRAIN_LOCAL" --eval-file "$EVAL_LOCAL" \
  --base-model "$BASE_MODEL" --golden-set "$GOLDEN" --strict || {
    log "BLOCKED: pre-flight failed — not starting a pod."; exit 3; }

KEYF="/tmp/rpkey_launch"; cp ~/.ssh/runpod_aria "$KEYF"; chmod 600 "$KEYF"
SSH="ssh -i $KEYF -o StrictHostKeyChecking=no -o ConnectTimeout=15 -o ServerAliveInterval=30 -o ServerAliveCountMax=6"
TSSH(){ timeout 75 $SSH "$@"; }

# ---- create + start (one step, retried: R-F3417) ---------------------------
POD_ID=""; HOST=""; PORT=""
for i in $(seq 1 "$MAX_TRIES"); do
  POD_ID=$("$PYBIN" scripts/train/_create_v04_pod.py 2>/dev/null | head -1 | tr -d '[:space:]')
  if [ -z "$POD_ID" ]; then
    log "[try $i/$MAX_TRIES] create rejected (no capacity) — retry ${RETRY_SECS}s"
    sleep "$RETRY_SECS"; continue
  fi
  log "CREATED pod $POD_ID (try $i/$MAX_TRIES) — waiting for RUNNING…"
  HOST=""; PORT=""
  for j in $(seq 1 "$START_WAIT_TICKS"); do
    PD=$(curl -s "$API/pods/$POD_ID" -H "Authorization: Bearer $KEY")
    ST=$(echo "$PD" | jget desiredStatus); HOST=$(echo "$PD" | jget publicIp); PORT=$(echo "$PD" | pmget)
    [ "$ST" = "RUNNING" ] && [ -n "$HOST" ] && [ -n "$PORT" ] && break
    sleep 10
  done
  [ -n "$HOST" ] && [ -n "$PORT" ] && { log "RUNNING $HOST:$PORT"; break; }
  log "[try $i/$MAX_TRIES] pod $POD_ID never provisioned — releasing it"
  release; POD_ID=""; sleep "$RETRY_SECS"
done
[ -n "$POD_ID" ] && [ -n "$HOST" ] && [ -n "$PORT" ] \
  || { log "GAVE UP: no pod reached RUNNING in $MAX_TRIES attempts."; exit 2; }

ok=0; for j in $(seq 1 40); do
  if TSSH -p "$PORT" root@"$HOST" "echo ok" 2>/dev/null | grep -q ok; then ok=$((ok+1)); else ok=0; fi
  [ "$ok" -ge 3 ] && { log "SSH stable"; break; }; sleep 5
done
[ "$ok" -ge 3 ] || die "SSH never stabilised"
sleep 10

# R-F4241 - the pod is now REUSED rather than created fresh (see
# scripts/train/pod_of_record.py), so /workspace may already hold the PREVIOUS
# run's reports and completion sentinel. A cycle that dies before writing its
# own report would otherwise let the harvester publish a stale measurement as
# this run's. Move that evidence aside - never delete it - before any work
# starts. Harmless on a genuinely new pod, where there is nothing to move.
ARCHIVE_CMD=$("$PYBIN" -m scripts.train.pod_of_record archive-command) \
  || die "could not build the prior-run archive command"
TSSH -p "$PORT" root@"$HOST" "$ARCHIVE_CMD" | grep -q ARCHIVED \
  || die "prior-run evidence was not archived - refusing to start work that could be confused with it"

RSCP(){ local s="$1" d="$2" t; for t in 1 2 3 4 5; do
  timeout 120 scp -i "$KEYF" -o StrictHostKeyChecking=no -o ConnectTimeout=15 -P "$PORT" "$s" root@"$HOST":"$d" 2>/dev/null && return 0
  log "scp retry $t $(basename "$s")"; sleep 10; done; return 1; }

for j in 1 2 3; do TSSH -p "$PORT" root@"$HOST" \
  "mkdir -p /workspace/datasets /workspace/crucix/scripts/train /workspace/logs /workspace/eval /workspace/checkpoints" 2>/dev/null && break; sleep 8; done
TSSH -p "$PORT" root@"$HOST" "touch /workspace/crucix/scripts/__init__.py" 2>/dev/null

RSCP scripts/train/pod_tooluse_cycle.sh     /workspace/                                        || die "scp runner"
RSCP scripts/train/pod_selfstop_watch_v04.sh /workspace/                                       || die "scp watcher"
RSCP scripts/train/sft_train.py             /workspace/crucix/scripts/train/sft_train.py       || die "scp trainer"
RSCP scripts/train/eval_tooluse.py          /workspace/crucix/scripts/train/eval_tooluse.py    || die "scp eval"
RSCP scripts/train/serve_eval_shim.py       /workspace/crucix/scripts/train/serve_eval_shim.py || die "scp shim"
RSCP scripts/train/build_tooluse_corpus.py  /workspace/crucix/scripts/train/build_tooluse_corpus.py || die "scp validator"
RSCP scripts/train/__init__.py              /workspace/crucix/scripts/train/__init__.py        || true
RSCP "$TRAIN_LOCAL" /workspace/datasets/aria_tooluse_train.jsonl                               || die "scp train set"
RSCP "$EVAL_LOCAL"  /workspace/datasets/aria_tooluse_eval.jsonl                                || die "scp eval set"
RSCP "$GEN_LOCAL"   /workspace/datasets/aria_tooluse_generation.jsonl                          || die "scp generation set"

# ---- arm the watchdog BEFORE the work, and refuse to run unbounded ---------
log "arming self-stop watchdog (DEADLINE=${DEADLINE}s, GRACE=${GRACE}s, COLLECT_GRACE=${COLLECT_GRACE}s)…"
TSSH -p "$PORT" root@"$HOST" \
  "POD_ID=$POD_ID RP_KEY='$KEY' GRACE=$GRACE DEADLINE=$DEADLINE COLLECT_GRACE=$COLLECT_GRACE setsid nohup bash /workspace/pod_selfstop_watch_v04.sh >/workspace/logs/_selfstop.log 2>&1 </dev/null & echo ARMED" \
  | grep -q ARMED || die "watchdog did not arm — refusing to start work nothing would bound"

log "starting cycle detached…"
TSSH -p "$PORT" root@"$HOST" \
  "rm -f /workspace/eval/_cycle_status; BASE_MODEL='$BASE_MODEL' EPOCHS=$EPOCHS GEN_TRAIN=$GEN_TRAIN GEN_LIMIT=$GEN_LIMIT GEN_FILE=/workspace/datasets/aria_tooluse_generation.jsonl setsid nohup bash /workspace/pod_tooluse_cycle.sh >/workspace/logs/tooluse_cycle.log 2>&1 </dev/null & echo STARTED" \
  | grep -q STARTED || die "cycle did not start"

mkdir -p "$(dirname "$STATE_FILE")"
{ echo "POD_ID=$POD_ID"; echo "HOST=$HOST"; echo "PORT=$PORT"
  echo "LAUNCHED_AT=$(date -u +%s)"; echo "DEADLINE=$DEADLINE"; } > "$STATE_FILE"

log "LAUNCHED — pod $POD_ID, state in $STATE_FILE"
log "the pod now works independently of this machine; it self-stops after"
log "${DEADLINE}s even if nobody returns. Collect with:"
log "    bash scripts/train/tooluse_harvest.sh"
