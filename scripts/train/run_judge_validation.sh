#!/bin/bash
# R-F1463 — REAL judge validation. Re-runs the 100-Q eval on the migrated pod
# with the LLM-judge-enabled eval_aria_llm.py (R-F1456/1459) for BOTH v0.2 and
# DeepSeek, then checks the judge DISCRIMINATES (DeepSeek judge-DD >> v0.2). This
# is the keystone gate: only a validated judge may gate training data.
#
# Pushes the CURRENT eval_aria_llm.py to the pod (the pod's git checkout is old /
# keyword-based) so the run actually uses the judge.
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

stop_pod(){ echo "[val] stopping pod $POD"; curl -s -X POST "$API/pods/$POD/stop" -H "Authorization: Bearer $API_KEY" >/dev/null 2>&1 || true; }
trap stop_pod EXIT

# 1. Ensure RUNNING
HOST=""; PORT=""
for i in $(seq 1 20); do
  PD=$(curl -s "$API/pods/$POD" -H "Authorization: Bearer $API_KEY")
  ST=$(echo "$PD" | jget desiredStatus)
  if [ "$ST" != "RUNNING" ]; then
    R=$(curl -s -X POST "$API/pods/$POD/start" -H "Authorization: Bearer $API_KEY")
    ERR=$(echo "$R" | python -c "import sys,json
try: d=json.load(sys.stdin); print(d.get('error','') if isinstance(d,dict) else '')
except: print('')" 2>/dev/null || echo "")
    [ -n "$ERR" ] && { echo "[val] start blocked ($ERR) — retry 60s ($i/20)"; sleep 60; continue; }
  fi
  for j in $(seq 1 18); do
    PD=$(curl -s "$API/pods/$POD" -H "Authorization: Bearer $API_KEY")
    ST=$(echo "$PD" | jget desiredStatus); HOST=$(echo "$PD" | jget publicIp); PORT=$(echo "$PD" | pmget)
    [ "$ST" = "RUNNING" ] && [ -n "$HOST" ] && [ -n "$PORT" ] && break
    sleep 8
  done
  [ -n "$HOST" ] && [ -n "$PORT" ] && { echo "[val] pod RUNNING $HOST:$PORT"; break; }
  sleep 30
done
[ -n "$HOST" ] && [ -n "$PORT" ] || { echo "[val] FATAL: pod not RUNNING"; exit 1; }

# 2. Wait SSH
for i in $(seq 1 24); do $SSH -p "$PORT" root@"$HOST" "echo ok" 2>/dev/null | grep -q ok && { echo "[val] SSH ready"; break; }; sleep 5; done

# 3. Push runner + CURRENT judge-enabled eval + 100-Q set
echo "[val] pushing runner + CURRENT eval_aria_llm.py (judge-enabled) + eval set..."
# R-F4350 (C-295) — the pod runner sources hf_cache_select.sh and fails
# CLOSED without it, so the selector must ship alongside the runner.
scp -i "$KEY" -o StrictHostKeyChecking=no -P "$PORT" scripts/train/hf_cache_select.sh root@"$HOST":/workspace/ || { echo "[val] FATAL scp hf_cache_select.sh"; exit 1; }
scp -i "$KEY" -o StrictHostKeyChecking=no -P "$PORT" scripts/train/baseline_pod_run.sh root@"$HOST":/workspace/ || { echo "[val] FATAL scp runner"; exit 1; }
scp -i "$KEY" -o StrictHostKeyChecking=no -P "$PORT" scripts/train/eval_aria_llm.py root@"$HOST":/workspace/crucix/scripts/train/eval_aria_llm.py || { echo "[val] FATAL scp eval"; exit 1; }
$SSH -p "$PORT" root@"$HOST" "mkdir -p /workspace/datasets"
scp -i "$KEY" -o StrictHostKeyChecking=no -P "$PORT" data/eval_reports/aria_eval_100q.jsonl root@"$HOST":/workspace/datasets/aria_eval_100q.jsonl || { echo "[val] FATAL scp set"; exit 1; }

# 4. Run (judge auto-on: DEEPSEEK_API_KEY set + eval set has expected_answer)
echo "[val] running judge-scored 100-Q eval (v0.2 + DeepSeek)..."
$SSH -p "$PORT" root@"$HOST" "EVAL_SET=/workspace/datasets/aria_eval_100q.jsonl DEEPSEEK_API_KEY='$DSK' ARIA_EVAL_JUDGE_ENABLED=1 bash /workspace/baseline_pod_run.sh"

# 5. Pull reports
mkdir -p data/eval_reports
scp -i "$KEY" -o StrictHostKeyChecking=no -P "$PORT" root@"$HOST":/workspace/eval/aria_llm_v02_eval.json data/eval_reports/aria_llm_v02_eval_judge.json 2>/dev/null || echo "[val] (v02 not pulled)"
scp -i "$KEY" -o StrictHostKeyChecking=no -P "$PORT" root@"$HOST":/workspace/eval/deepseek_baseline_eval.json data/eval_reports/deepseek_baseline_eval_judge.json 2>/dev/null || echo "[val] (deepseek not pulled)"

# 6. Verdict — does the judge discriminate?
echo "[val] === JUDGE VALIDATION VERDICT ==="
python - <<'PY'
import json, os
def acc(f):
    if not os.path.exists(f): return None, None
    d=json.load(open(f,encoding='utf-8')); dd=d.get('defence_dd') or {}
    return dd.get('accuracy'), dd.get('total')
v2,n2=acc('data/eval_reports/aria_llm_v02_eval_judge.json')
ds,nd=acc('data/eval_reports/deepseek_baseline_eval_judge.json')
print(f"v0.2 judge-DD:     {v2} (n={n2})")
print(f"DeepSeek judge-DD: {ds} (n={nd})")
if v2 is None or ds is None:
    print("VERDICT: INCOMPLETE — a report is missing.")
elif ds > v2 + 0.15:
    print(f"VERDICT: JUDGE DISCRIMINATES ✅ (DeepSeek leads v0.2 by {ds-v2:.2f}). Judge is trustworthy to gate training data.")
elif ds > v2:
    print(f"VERDICT: WEAK discrimination (DeepSeek leads by only {ds-v2:.2f}). Inspect before trusting the gate.")
else:
    print(f"VERDICT: JUDGE FAILS to discriminate (DeepSeek {ds} <= v0.2 {v2}). DO NOT gate on this judge — fix it.")
PY
stop_pod
echo "[val] DONE — pod stopped."
