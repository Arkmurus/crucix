#!/bin/bash
# R-F1455 — LOCAL driver for the v0.2-vs-DeepSeek baseline. Runs from the operator
# Windows box (has repo + .env + ~/.ssh/runpod_aria). Retries the pod start until a
# GPU frees on the pinned host, then pushes + runs baseline_pod_run.sh ON THE POD
# (proven shim recipe), pulls the reports, and ALWAYS stops the pod (EXIT trap).
#
# Cost-safe: start attempts on a capacity-blocked pod do not bill; the GPU only
# bills once RUNNING, and the trap stops it on any exit.
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
POD="7ei3hldcpz4j2v"
API="https://rest.runpod.io/v1"
API_KEY=$(grep -E "^RUNPOD_API_KEY=" .env | head -1 | cut -d= -f2- | tr -d '"' | tr -d "'" | tr -d '\r')
DSK=$(grep -E "^DEEPSEEK_API_KEY=" .env | head -1 | cut -d= -f2- | tr -d '"' | tr -d "'" | tr -d '\r')
KEY="/tmp/rpkey"; cp ~/.ssh/runpod_aria "$KEY"; chmod 600 "$KEY"
SSH="ssh -i $KEY -o StrictHostKeyChecking=no -o ConnectTimeout=10"

stop_pod(){ echo "[driver] stopping pod $POD"; curl -s -X POST "$API/pods/$POD/stop" -H "Authorization: Bearer $API_KEY" >/dev/null 2>&1 || true; }
trap stop_pod EXIT

jget(){ python -c "import sys,json;d=json.load(sys.stdin);print(d.get('$1','') or '')" 2>/dev/null; }
pmget(){ python -c "import sys,json;d=json.load(sys.stdin);pm=d.get('portMappings') or {};print(pm.get('22') or '')" 2>/dev/null; }

# ── 1. Retry start until a GPU frees (cap ~60 min); $0 while blocked ──────────
echo "[driver] retrying pod start until a GPU frees on the host…"
HOST=""; PORT=""
for i in $(seq 1 60); do
  R=$(curl -s -X POST "$API/pods/$POD/start" -H "Authorization: Bearer $API_KEY")
  ERR=$(echo "$R" | python -c "import sys,json
try: d=json.load(sys.stdin)
except: print('parse'); raise SystemExit
print(d.get('error','') if isinstance(d,dict) else '')" 2>/dev/null || echo "")
  if [ -z "$ERR" ]; then
    for j in $(seq 1 18); do
      PD=$(curl -s "$API/pods/$POD" -H "Authorization: Bearer $API_KEY")
      ST=$(echo "$PD" | jget desiredStatus); HOST=$(echo "$PD" | jget publicIp); PORT=$(echo "$PD" | pmget)
      [ "$ST" = "RUNNING" ] && [ -n "$HOST" ] && [ -n "$PORT" ] && break
      sleep 8
    done
    [ -n "$HOST" ] && [ -n "$PORT" ] && { echo "[driver] pod RUNNING $HOST:$PORT (attempt $i)"; break; }
  fi
  echo "[driver] start blocked (${ERR:-not-ready}) — retry in 60s ($i/60)"
  HOST=""; PORT=""; sleep 60
done
[ -n "$HOST" ] && [ -n "$PORT" ] || { echo "[driver] FATAL: no GPU freed in ~60 min — recreate the pod on a host with availability (operator)."; exit 1; }

# ── 2. Wait for SSH ──────────────────────────────────────────────────────────
echo "[driver] waiting for SSH…"
ok=0; for i in $(seq 1 24); do $SSH -p "$PORT" root@"$HOST" "echo ok" 2>/dev/null | grep -q ok && { ok=1; echo "[driver] SSH ready ($i)"; break; }; sleep 5; done
[ "$ok" = 1 ] || { echo "[driver] FATAL: SSH never came up"; exit 1; }

# ── 3. Push runner + the frozen 500-Q eval set ───────────────────────────────
echo "[driver] pushing runner + eval set…"
# R-F4350 (C-295) — the pod runner sources hf_cache_select.sh and fails
# CLOSED without it, so the selector must ship alongside the runner.
scp -i "$KEY" -o StrictHostKeyChecking=no -P "$PORT" scripts/train/hf_cache_select.sh root@"$HOST":/workspace/ || { echo "[driver] FATAL scp hf_cache_select.sh"; exit 1; }
scp -i "$KEY" -o StrictHostKeyChecking=no -P "$PORT" scripts/train/baseline_pod_run.sh root@"$HOST":/workspace/ || { echo "[driver] FATAL scp runner"; exit 1; }
$SSH -p "$PORT" root@"$HOST" "mkdir -p /workspace/datasets"
scp -i "$KEY" -o StrictHostKeyChecking=no -P "$PORT" data/eval_reports/aria_eval_500q.jsonl root@"$HOST":/workspace/datasets/aria_eval_500q.jsonl || { echo "[driver] FATAL scp eval set"; exit 1; }

# ── 4. Run the proven baseline ON THE POD ────────────────────────────────────
echo "[driver] running baseline_pod_run.sh on the pod…"
$SSH -p "$PORT" root@"$HOST" "DEEPSEEK_API_KEY='$DSK' bash /workspace/baseline_pod_run.sh"

# ── 5. Pull reports ──────────────────────────────────────────────────────────
echo "[driver] pulling reports…"
mkdir -p data/eval_reports
scp -i "$KEY" -o StrictHostKeyChecking=no -P "$PORT" root@"$HOST":/workspace/eval/aria_llm_v02_eval.json data/eval_reports/ 2>/dev/null || echo "[driver] (v02 report not pulled)"
scp -i "$KEY" -o StrictHostKeyChecking=no -P "$PORT" root@"$HOST":/workspace/eval/deepseek_baseline_eval.json data/eval_reports/ 2>/dev/null || echo "[driver] (deepseek report not pulled)"

# ── 6. Stop pod (trap also covers this) ──────────────────────────────────────
stop_pod
echo "[driver] DONE — reports in data/eval_reports/, pod stopped."
