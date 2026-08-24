#!/bin/bash
# R-F1473 — STANDALONE v0.3 result recovery (fork-light, idempotent, re-runnable).
#
# WHY THIS EXISTS: the 2026-06-09 v0.3 cycle TRAINED fine and its eval ran to
# completion ON THE POD (detached setsid+nohup), but the LOCAL poll loop in
# run_v0_3_cycle.sh died at ~03:17 of Windows git-bash fork exhaustion (errno 11,
# "Resource temporarily unavailable") BEFORE it pulled the reports or printed the
# verdict. The reports persist on the network volume — this script gets them back
# WITHOUT re-training and WITHOUT depending on an 8-hour local loop surviving.
#
# It resumes the volume-mounting pod (default 7ei3hldcpz4j2v, which mounts network
# volume 4vdw2zmqov where /workspace lives), inventories what survived, pulls the
# three eval reports, prints the PROMOTE/KEEP verdict, and STOPS the pod (EXIT trap).
# Re-run it as many times as needed — it is read-only on the pod (scp out only).
#
# Capacity-aware: a pinned-host "not enough free GPUs" start error fails fast in
# ~2s with the real reason (R-F1452 lesson) instead of 5 minutes of silent polling.
#
# Usage:  bash scripts/train/recover_v0_3.sh
#   RUNPOD_POD_ID=<id>  override the pod (default: the volume-mounting base pod)
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
POD="${RUNPOD_POD_ID:-7ei3hldcpz4j2v}"
API="https://rest.runpod.io/v1"
API_KEY=$(grep -E "^RUNPOD_API_KEY=" .env | head -1 | cut -d= -f2- | tr -d '"' | tr -d "'" | tr -d '\r')
[ -n "$API_KEY" ] || { echo "[recover] FATAL: RUNPOD_API_KEY not in .env"; exit 1; }

KEY="/tmp/rpkey"; cp ~/.ssh/runpod_aria "$KEY"; chmod 600 "$KEY"
SSH="ssh -i $KEY -o StrictHostKeyChecking=no -o ConnectTimeout=15 -o ServerAliveInterval=30 -o ServerAliveCountMax=10"
jget(){ python -c "import sys,json;d=json.load(sys.stdin);print(d.get('$1','') or '')" 2>/dev/null; }
pmget(){ python -c "import sys,json;d=json.load(sys.stdin);pm=d.get('portMappings') or {};print(pm.get('22') or '')" 2>/dev/null; }
errget(){ python -c "import sys,json
try: d=json.load(sys.stdin); print(d.get('error','') if isinstance(d,dict) else '')
except: print('')" 2>/dev/null; }

stop_pod(){ echo "[recover] stopping pod $POD"; curl -s -X POST "$API/pods/$POD/stop" -H "Authorization: Bearer $API_KEY" >/dev/null 2>&1 || true; }
trap stop_pod EXIT

# 1. Ensure RUNNING — fail FAST + LOUD on capacity (don't poll silently for 5 min)
HOST=""; PORT=""
for i in $(seq 1 10); do
  PD=$(curl -s "$API/pods/$POD" -H "Authorization: Bearer $API_KEY")
  ST=$(echo "$PD" | jget desiredStatus)
  if [ "$ST" != "RUNNING" ]; then
    R=$(curl -s -X POST "$API/pods/$POD/start" -H "Authorization: Bearer $API_KEY")
    ERR=$(echo "$R" | errget)
    if [ -n "$ERR" ]; then
      echo "[recover] BLOCKED: pod start refused — $ERR"
      case "$ERR" in
        *"free GPUs"*|*"not enough"*) echo "[recover] => pinned host $POD has no free GPU. Retry later, OR pull the reports off volume 4vdw2zmqov via a pod on a host with capacity."; exit 2;;
      esac
      echo "[recover] retry 30s ($i/10)"; sleep 30; continue
    fi
  fi
  for j in $(seq 1 18); do
    PD=$(curl -s "$API/pods/$POD" -H "Authorization: Bearer $API_KEY")
    ST=$(echo "$PD" | jget desiredStatus); HOST=$(echo "$PD" | jget publicIp); PORT=$(echo "$PD" | pmget)
    [ "$ST" = "RUNNING" ] && [ -n "$HOST" ] && [ -n "$PORT" ] && break
    sleep 8
  done
  [ -n "$HOST" ] && [ -n "$PORT" ] && { echo "[recover] pod RUNNING $HOST:$PORT"; break; }
  sleep 20
done
[ -n "$HOST" ] && [ -n "$PORT" ] || { echo "[recover] FATAL: pod not RUNNING (capacity?)"; exit 1; }

# 2. Wait SSH
for i in $(seq 1 24); do $SSH -p "$PORT" root@"$HOST" "echo ok" 2>/dev/null | grep -q ok && { echo "[recover] SSH ready"; break; }; sleep 5; done

# 3. ONE SSH: inventory what survived on the volume (no fork-per-file loop)
echo "[recover] === what survived on the volume ==="
$SSH -p "$PORT" root@"$HOST" '
  echo "--- /workspace/eval ---"; ls -la /workspace/eval 2>/dev/null || echo "(no /workspace/eval)"
  echo "--- v0.3 adapter ---"; ls -la /workspace/checkpoints/aria_llm_v0_3_sft 2>/dev/null || echo "(no v0.3 adapter)"
  echo "--- cycle status sentinel ---"; cat /workspace/eval/_cycle_status 2>/dev/null && echo "(cycle finished on pod)" || echo "(no sentinel — cycle did not reach its EXIT on pod)"
  echo "--- on-pod cycle log tail ---"; tail -25 /workspace/logs/v0_3_cycle.log 2>/dev/null || echo "(no cycle log)"
' 2>/dev/null || echo "[recover] WARN: inventory SSH failed"

# 4. Pull the three reports (scp out only — read-only on the pod)
mkdir -p data/eval_reports
scp -i "$KEY" -o StrictHostKeyChecking=no -P "$PORT" root@"$HOST":/workspace/eval/aria_llm_v0_3_eval.json     data/eval_reports/aria_llm_v0_3_eval.json     2>/dev/null && echo "[recover] pulled v0.3 report"     || echo "[recover] (v0.3 report not present)"
scp -i "$KEY" -o StrictHostKeyChecking=no -P "$PORT" root@"$HOST":/workspace/eval/aria_llm_v0_2_eval.json     data/eval_reports/aria_llm_v0_2_eval_500q.json 2>/dev/null && echo "[recover] pulled v0.2 report"     || echo "[recover] (v0.2 report not present)"
scp -i "$KEY" -o StrictHostKeyChecking=no -P "$PORT" root@"$HOST":/workspace/eval/deepseek_baseline_eval.json data/eval_reports/deepseek_baseline_eval_500q.json 2>/dev/null && echo "[recover] pulled DeepSeek report" || echo "[recover] (deepseek report not present)"

# 5. Verdict (same logic as the on-pod driver)
echo "[recover] === v0.3 RESULT ==="
python - <<'PY'
import json, os
def dd(r): return ((r or {}).get("defence_dd") or (r or {}).get("dd_eval") or {})
def pi(r): return ((r or {}).get("prompt_injection") or {})
def load(f):
    if not os.path.exists(f): return None
    try: return json.load(open(f, encoding="utf-8"))
    except Exception: return None
v3 = load("data/eval_reports/aria_llm_v0_3_eval.json")
v2 = load("data/eval_reports/aria_llm_v0_2_eval_500q.json")
ds = load("data/eval_reports/deepseek_baseline_eval_500q.json")
a3, a2 = dd(v3).get("accuracy"), dd(v2).get("accuracy")
print(f"v0.3 judge-DD: {a3} (n={dd(v3).get('total')}) | leak_rate={pi(v3).get('leak_rate')}")
print(f"v0.2 judge-DD: {a2} (n={dd(v2).get('total')}) | leak_rate={pi(v2).get('leak_rate')}")
print(f"DeepSeek judge-DD: {dd(ds).get('accuracy')} (n={dd(ds).get('total')})")
if a3 is None:
    print("VERDICT: INCOMPLETE — v0.3 report not on the volume; re-serve+eval the persisted adapter or re-run the cycle.")
elif a2 is None:
    print(f"VERDICT: v0.3 = {a3}; no v0.2 comparison report on the volume.")
else:
    print(f"VERDICT: {'PROMOTE v0.3 ✅' if a3 >= a2 else 'KEEP v0.2'} (v0.3 {a3} vs v0.2 {a2})")
PY
stop_pod
echo "[recover] DONE — pod stopped."
