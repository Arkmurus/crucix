#!/bin/bash
# R-F1455 — 100-Q baseline reusing the ALREADY-SERVING shim on the migrated pod.
# No deps reinstall, no model reload — just kill the stray full-500 eval, push the
# 100-Q subset, eval v0.2 (localhost shim) + DeepSeek (same subset), pull, stop.
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
POD="lqhxb4swwafuzv"; HOST="216.81.248.127"; PORT="19967"
API="https://rest.runpod.io/v1"
API_KEY=$(grep -E "^RUNPOD_API_KEY=" .env | head -1 | cut -d= -f2- | tr -d '"' | tr -d "'" | tr -d '\r')
DSK=$(grep -E "^DEEPSEEK_API_KEY=" .env | head -1 | cut -d= -f2- | tr -d '"' | tr -d "'" | tr -d '\r')
KEY="/tmp/rpkey"; cp ~/.ssh/runpod_aria "$KEY"; chmod 600 "$KEY"
SSH="ssh -i $KEY -o StrictHostKeyChecking=no -o ConnectTimeout=15"
ES="/workspace/datasets/aria_eval_100q.jsonl"
EV="/workspace/crucix/scripts/train/eval_aria_llm.py"

stop_pod(){ echo "[driver] stopping pod $POD"; curl -s -X POST "$API/pods/$POD/stop" -H "Authorization: Bearer $API_KEY" >/dev/null 2>&1 || true; }
trap stop_pod EXIT

echo "[driver] cleanup stray full-500 eval (keep the serving shim)…"
$SSH -p "$PORT" root@"$HOST" "pkill -f eval_aria_llm 2>/dev/null || true; sleep 2; echo shim:; pgrep -af serve_eval_shim | head -1; curl -s --max-time 5 http://localhost:8888/v1/models"

echo "[driver] push 100-Q subset…"
scp -i "$KEY" -o StrictHostKeyChecking=no -P "$PORT" data/eval_reports/aria_eval_100q.jsonl root@"$HOST":"$ES" || { echo "[driver] FATAL scp"; exit 1; }

echo "[driver] eval v0.2 (100-Q) against the live shim…"
$SSH -p "$PORT" root@"$HOST" "export HF_HOME=/workspace/.cache/huggingface; python $EV --target http://localhost:8888/v1 --model aria-llm-v0.2 --eval-set $ES --out /workspace/eval/aria_llm_v02_eval_100q.json" || echo "[driver] WARN v0.2 eval returned nonzero"

echo "[driver] eval DeepSeek (100-Q)…"
$SSH -p "$PORT" root@"$HOST" "python $EV --target https://api.deepseek.com/v1 --model deepseek-chat --api-key '$DSK' --eval-set $ES --out /workspace/eval/deepseek_baseline_eval_100q.json" || echo "[driver] WARN deepseek eval returned nonzero"

echo "[driver] pull reports…"
mkdir -p data/eval_reports
scp -i "$KEY" -o StrictHostKeyChecking=no -P "$PORT" root@"$HOST":/workspace/eval/aria_llm_v02_eval_100q.json data/eval_reports/ 2>/dev/null || echo "[driver] (v02 100q not pulled)"
scp -i "$KEY" -o StrictHostKeyChecking=no -P "$PORT" root@"$HOST":/workspace/eval/deepseek_baseline_eval_100q.json data/eval_reports/ 2>/dev/null || echo "[driver] (deepseek 100q not pulled)"

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
