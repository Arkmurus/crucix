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
LOG=/workspace/logs/_selfstop_v04.log
GRACE=${GRACE:-600}

echo "[$(date -u +%H:%M:%S)] v0.4 self-stop armed pod=$POD_ID grace=${GRACE}s" >> "$LOG"
# Wait for completion — _cycle_status is written by v0_4_pod_run.sh's EXIT trap.
while [ ! -f "$STATUS" ]; do sleep 30; done
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
