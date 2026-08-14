#!/usr/bin/env bash
# R-F1516 — on-pod self-stop watcher for the v0.4 cycle. Adapted from R-F1503
# (v0.2 paths) to the v0.4 sentinel + report. Runs ON the pod, detached, so the
# pod stops itself when the cycle finishes EVEN IF the local driver died (the
# 2026-06-12 background-teardown failure mode). Volume-free → container disk is
# ephemeral, so GRACE is generous to let an awake local watcher pull reports first.
set -uo pipefail
: "${POD_ID:?need POD_ID}"; : "${RP_KEY:?need RP_KEY}"
STATUS=/workspace/eval/_cycle_status
REPORT=${REPORT:-/workspace/eval/aria_llm_v0_4_eval.json}   # override for the DPO cycle (R-F1522)
VERDICT=${VERDICT:-/workspace/eval/_v0_4_verdict.txt}
EVALD=/workspace/eval
LOG=/workspace/logs/_selfstop_v04.log
DEADLINE=${DEADLINE:-21600}   # absolute cap (6h): stop even if nothing signals
POLL=${POLL:-30}
# Bounded window to collect artefacts that already exist when the deadline
# fires. Long enough for a harvest, short enough that forgetting costs cents.
COLLECT_GRACE=${COLLECT_GRACE:-900}
GRACE=${GRACE:-600}

echo "[$(date -u +%H:%M:%S)] v0.4 self-stop armed pod=$POD_ID grace=${GRACE}s" >> "$LOG"
# Wait for completion — _cycle_status is written by v0_4_pod_run.sh's EXIT trap.
# R-F3400 — BOUNDED. This loop used to be `while [ ! -f "$STATUS" ]; do sleep
# 30; done`. The sentinel is written by the cycle script's EXIT trap, and an
# EXIT trap does not run when the process is SIGKILLed (OOM on a 7B in 4-bit, a
# container disk that fills mid-checkpoint) or if the script dies before
# installing it. In all of those the watcher waited forever and the GPU billed
# until a human noticed — the §24 worst outcome, produced by the very mechanism
# meant to prevent it. A last line of defence cannot assume a clean exit from
# the thing it is defending against.
START_TS=$(date +%s); TIMED_OUT=0
while [ ! -f "$STATUS" ]; do
  if [ $(( $(date +%s) - START_TS )) -ge "$DEADLINE" ]; then
    TIMED_OUT=1
    echo "[$(date -u +%H:%M:%S)] DEADLINE ${DEADLINE}s reached with no sentinel — stopping pod regardless" >> "$LOG"
    break
  fi
  sleep "$POLL"
done
if [ "$TIMED_OUT" = 1 ]; then
  # R-F3445 - COLLECT BEFORE DESTROYING. R-F3400 said "nothing is coming: stop
  # NOW, no grace window", which is right for a HUNG cycle and wrong for a merely
  # SLOW one. A trained eval report was written at 21:36:50 and this line stopped
  # the pod at 21:36:59; container disk is ephemeral, so a 164-minute $4.07 run
  # produced nothing. The anti-hang property is preserved exactly - with NO
  # artefacts it still stops instantly - but when output exists the deadline
  # allows one bounded window to collect it.
  _have=0
  # R-F3981 — DPO persists its paid training result as a .tgz before starting
  # the long evaluation.  Looking only for JSON classified that valid adapter
  # as "nothing to collect" and destroyed it immediately at the deadline.
  # Keep this allow-list to actual persisted outputs; including logs would make
  # every hung run wait because this watcher creates its own log above.
  for _f in "$EVALD"/*.json "$EVALD"/*.tgz; do
    [ -f "$_f" ] && { _have=1; break; }
  done
  if [ "$_have" = 1 ]; then
    echo "[$(date -u +%H:%M:%S)] DEADLINE with output present - COLLECTION window ${COLLECT_GRACE}s before stop" >> "$LOG"
    sleep "$COLLECT_GRACE"
    echo "[$(date -u +%H:%M:%S)] collection window closed" >> "$LOG"
  else
    echo "[$(date -u +%H:%M:%S)] DEADLINE with no output - nothing to collect, stopping now" >> "$LOG"
  fi
  curl -s -X POST "https://rest.runpod.io/v1/pods/$POD_ID/stop" -H "Authorization: Bearer $RP_KEY" >> "$LOG" 2>&1
  echo "[$(date -u +%H:%M:%S)] stop issued on DEADLINE — done" >> "$LOG"
  exit 0
fi
RC=$(cat "$STATUS" 2>/dev/null)
echo "[$(date -u +%H:%M:%S)] cycle done rc=$RC — extracting verdict" >> "$LOG"

python3 - "$REPORT" > "$VERDICT" 2>>"$LOG" <<'PY'
import json, sys
try:
    d = json.load(open(sys.argv[1], encoding="utf-8"))
    dd = d.get("defence_dd") or d.get("dd_eval") or {}
    pi = d.get("prompt_injection") or {}
    print(f"v0.4 judge-DD accuracy: {dd.get('accuracy')} (n={dd.get('total')}) | injection leak_rate={pi.get('leak_rate')}")
    print("compare: v0.3 judge-DD = 0.22 | teacher ceiling = 0.34")
except Exception as e:
    print(f"verdict-extract failed: {e}")
PY
cat "$VERDICT" >> "$LOG"

echo "[$(date -u +%H:%M:%S)] grace ${GRACE}s (lets an awake local watcher pull first), then self-stop" >> "$LOG"
sleep "$GRACE"
echo "[$(date -u +%H:%M:%S)] stopping pod $POD_ID" >> "$LOG"
curl -s -X POST "https://rest.runpod.io/v1/pods/$POD_ID/stop" -H "Authorization: Bearer $RP_KEY" >> "$LOG" 2>&1
echo "[$(date -u +%H:%M:%S)] stop issued — done" >> "$LOG"
