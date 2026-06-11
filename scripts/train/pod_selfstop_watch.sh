#!/usr/bin/env bash
# R-F1503 — on-pod self-stop watcher. Runs ON the RunPod pod, detached, so it is
# fully independent of the local machine (which may sleep). It waits for the eval
# to finish, captures the verdict to the volume, then stops the pod after a grace
# window — guaranteeing the pod never bills idle even if the local driver is
# asleep when the run completes. Verdict + report persist on the volume regardless.
set -uo pipefail
: "${POD_ID:?need POD_ID}"; : "${RP_KEY:?need RP_KEY}"
STATUS=/workspace/eval/_v0_2_status
REPORT=/workspace/eval/aria_llm_v0_2_eval.json
VERDICT=/workspace/eval/_v0_2_verdict.txt
LOG=/workspace/logs/_selfstop.log
GRACE=${GRACE:-300}

echo "[$(date -u +%H:%M:%S)] self-stop armed pod=$POD_ID grace=${GRACE}s" >> "$LOG"
# Wait for completion — _v0_2_status is written by the eval orchestrator's EXIT trap.
while [ ! -f "$STATUS" ]; do sleep 30; done
RC=$(cat "$STATUS" 2>/dev/null)
echo "[$(date -u +%H:%M:%S)] eval done rc=$RC — extracting verdict" >> "$LOG"

python3 - "$REPORT" > "$VERDICT" 2>>"$LOG" <<'PY'
import json, sys
try:
    d = json.load(open(sys.argv[1], encoding="utf-8"))
    dd = d.get("defence_dd") or d.get("dd_eval") or {}
    pi = d.get("prompt_injection") or {}
    print(f"v0.2 judge-DD accuracy: {dd.get('accuracy')} (n={dd.get('total')}) | injection leak_rate={pi.get('leak_rate')}")
    print("compare: v0.3 judge-DD = 0.22")
except Exception as e:
    print(f"verdict-extract failed: {e}")
PY
cat "$VERDICT" >> "$LOG"

echo "[$(date -u +%H:%M:%S)] grace ${GRACE}s (lets an awake local driver pull first), then self-stop" >> "$LOG"
sleep "$GRACE"
echo "[$(date -u +%H:%M:%S)] stopping pod $POD_ID" >> "$LOG"
curl -s -X POST "https://rest.runpod.io/v1/pods/$POD_ID/stop" -H "Authorization: Bearer $RP_KEY" >> "$LOG" 2>&1
echo "[$(date -u +%H:%M:%S)] stop issued — done" >> "$LOG"
