#!/bin/bash
# R-F1455 — clean recovery driver: start the migrated pod, run the FULL proven
# recipe (baseline_pod_run.sh) on the 100-Q subset, pull reports, stop. This is
# the ONLY pod controller now (all earlier drivers + their traps are dead), so
# nothing stops the pod out from under it. Dynamic port (changes on restart).
set -uo pipefail
# R-F4305 (C-258) — resolve the repo from THIS script, never a hardcoded
# checkout. The old hardcoded literal named a machine that no longer exists,
# and `cd` to a missing dir under `set -uo pipefail` does NOT abort — the
# script silently continues in the wrong directory. git first; BASH_SOURCE
# fallback because this file is rsynced onto pods where there is no .git.
# NOTE the braces: `A || B && C` parses as `(A || B) && C`, so an ungrouped
# fallback runs `pwd` even when git SUCCEEDS and $REPO gets two lines.
REPO="$(git rev-parse --show-toplevel 2>/dev/null || { cd "$(dirname "${BASH_SOURCE[0]:-$0}")/../.." && pwd; })"
cd "$REPO" || { echo "FATAL: cannot resolve repo root" >&2; exit 1; }
POD="lqhxb4swwafuzv"
API="https://rest.runpod.io/v1"
API_KEY=$(grep -E "^RUNPOD_API_KEY=" .env | head -1 | cut -d= -f2- | tr -d '"' | tr -d "'" | tr -d '\r')
DSK=$(grep -E "^DEEPSEEK_API_KEY=" .env | head -1 | cut -d= -f2- | tr -d '"' | tr -d "'" | tr -d '\r')
KEY="/tmp/rpkey"; cp ~/.ssh/runpod_aria "$KEY"; chmod 600 "$KEY"
SSH="ssh -i $KEY -o StrictHostKeyChecking=no -o ConnectTimeout=15"
jget(){ python -c "import sys,json;d=json.load(sys.stdin);print(d.get('$1','') or '')" 2>/dev/null; }
pmget(){ python -c "import sys,json;d=json.load(sys.stdin);pm=d.get('portMappings') or {};print(pm.get('22') or '')" 2>/dev/null; }

stop_pod(){ echo "[driver] stopping pod $POD"; curl -s -X POST "$API/pods/$POD/stop" -H "Authorization: Bearer $API_KEY" >/dev/null 2>&1 || true; }
trap stop_pod EXIT

# 1. Ensure RUNNING (start if needed; retry capacity up to ~20 min)
HOST=""; PORT=""
for i in $(seq 1 20); do
  PD=$(curl -s "$API/pods/$POD" -H "Authorization: Bearer $API_KEY")
  ST=$(echo "$PD" | jget desiredStatus)
  if [ "$ST" != "RUNNING" ]; then
    R=$(curl -s -X POST "$API/pods/$POD/start" -H "Authorization: Bearer $API_KEY")
    ERR=$(echo "$R" | python -c "import sys,json
try: d=json.load(sys.stdin); print(d.get('error','') if isinstance(d,dict) else '')
except: print('')" 2>/dev/null || echo "")
    [ -n "$ERR" ] && { echo "[driver] start blocked ($ERR) — retry 60s ($i/20)"; sleep 60; continue; }
  fi
  for j in $(seq 1 18); do
    PD=$(curl -s "$API/pods/$POD" -H "Authorization: Bearer $API_KEY")
    ST=$(echo "$PD" | jget desiredStatus); HOST=$(echo "$PD" | jget publicIp); PORT=$(echo "$PD" | pmget)
    [ "$ST" = "RUNNING" ] && [ -n "$HOST" ] && [ -n "$PORT" ] && break
    sleep 8
  done
  [ -n "$HOST" ] && [ -n "$PORT" ] && { echo "[driver] pod RUNNING $HOST:$PORT"; break; }
  sleep 30
done
[ -n "$HOST" ] && [ -n "$PORT" ] || { echo "[driver] FATAL: pod not RUNNING"; exit 1; }

# 2. Wait SSH
for i in $(seq 1 24); do $SSH -p "$PORT" root@"$HOST" "echo ok" 2>/dev/null | grep -q ok && { echo "[driver] SSH ready"; break; }; sleep 5; done

# 3. Push runner + 100-Q subset
# R-F4350 (C-295) — the pod runner sources hf_cache_select.sh and fails
# CLOSED without it, so the selector must ship alongside the runner.
scp -i "$KEY" -o StrictHostKeyChecking=no -P "$PORT" scripts/train/hf_cache_select.sh root@"$HOST":/workspace/ || { echo "[driver] FATAL scp hf_cache_select.sh"; exit 1; }
scp -i "$KEY" -o StrictHostKeyChecking=no -P "$PORT" scripts/train/baseline_pod_run.sh root@"$HOST":/workspace/ || { echo "[driver] FATAL scp runner"; exit 1; }
$SSH -p "$PORT" root@"$HOST" "mkdir -p /workspace/datasets"
scp -i "$KEY" -o StrictHostKeyChecking=no -P "$PORT" data/eval_reports/aria_eval_100q.jsonl root@"$HOST":/workspace/datasets/aria_eval_100q.jsonl || { echo "[driver] FATAL scp set"; exit 1; }

# 4. Run baseline on the 100-Q subset (deps cached → serve → eval v0.2 + DeepSeek)
$SSH -p "$PORT" root@"$HOST" "EVAL_SET=/workspace/datasets/aria_eval_100q.jsonl DEEPSEEK_API_KEY='$DSK' bash /workspace/baseline_pod_run.sh"

# 5. Pull reports
mkdir -p data/eval_reports
scp -i "$KEY" -o StrictHostKeyChecking=no -P "$PORT" root@"$HOST":/workspace/eval/aria_llm_v02_eval.json data/eval_reports/aria_llm_v02_eval_100q.json 2>/dev/null || echo "[driver] (v02 not pulled)"
scp -i "$KEY" -o StrictHostKeyChecking=no -P "$PORT" root@"$HOST":/workspace/eval/deepseek_baseline_eval.json data/eval_reports/deepseek_baseline_eval_100q.json 2>/dev/null || echo "[driver] (deepseek not pulled)"

echo "[driver] === BASELINE (100-Q) ==="
python - <<'PY'
import json, os
for f in ["data/eval_reports/aria_llm_v02_eval_100q.json","data/eval_reports/deepseek_baseline_eval_100q.json"]:
    if not os.path.exists(f): print(f, "MISSING"); continue
    d=json.load(open(f)); dd=d.get("defence_dd") or {}; pi=d.get("prompt_injection") or {}
    print(f"{d.get('model')}: dd_accuracy={dd.get('accuracy')} (n={dd.get('total')}) | injection pass_rate={pi.get('pass_rate')} leak_rate={pi.get('leak_rate')}")
PY
stop_pod
echo "[driver] DONE — pod stopped."
