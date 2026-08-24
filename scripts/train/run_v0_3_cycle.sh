#!/bin/bash
# R-F1470 — v0.3 SFT-distillation cycle DRIVER (local orchestrator).
#
# Starts the migrated pod, pushes the 500-pair distillation corpus + the v0.3
# on-pod driver + the CURRENT train/serve/eval scripts (the pod's git checkout is
# old), runs train->serve->eval->verdict ON the pod, pulls the reports, prints the
# PROMOTE/KEEP verdict, and STOPS the pod (EXIT trap — pay for minutes, not hours).
#
# This is the ONLY pod controller for the run (all earlier drivers' traps are
# dead), so nothing stops the pod out from under it. Dynamic SSH port (changes on
# every restart — read from portMappings["22"]). Mirrors the proven R-F1463 driver
# (run_judge_validation.sh / run_baseline_recover.sh).
#
# Greenlit per data/_V03_TRAINING_RUNBOOK.md + CLAUDE.md §24 (weekly cycle, ~$5-10).
#
# Usage:  bash scripts/train/run_v0_3_cycle.sh
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
POD="${RUNPOD_POD_ID:-lqhxb4swwafuzv}"
API="https://rest.runpod.io/v1"
API_KEY=$(grep -E "^RUNPOD_API_KEY=" .env | head -1 | cut -d= -f2- | tr -d '"' | tr -d "'" | tr -d '\r')
DSK=$(grep -E "^DEEPSEEK_API_KEY=" .env | head -1 | cut -d= -f2- | tr -d '"' | tr -d "'" | tr -d '\r')
[ -n "$API_KEY" ] || { echo "[driver] FATAL: RUNPOD_API_KEY not in .env"; exit 1; }
[ -n "$DSK" ] || echo "[driver] WARN: DEEPSEEK_API_KEY not in .env — judge + DeepSeek baseline will be skipped"

TRAIN_CORPUS="data/training/aria_sft_distill_500.jsonl"
EVAL_LOCAL="data/eval_reports/aria_eval_500q.jsonl"
[ -s "$TRAIN_CORPUS" ] || { echo "[driver] FATAL: corpus missing/empty: $TRAIN_CORPUS (combine batch1+batch2 first)"; exit 1; }
[ -s "$EVAL_LOCAL" ]   || { echo "[driver] FATAL: eval set missing: $EVAL_LOCAL"; exit 1; }
echo "[driver] corpus $(wc -l < "$TRAIN_CORPUS") pairs; eval $(wc -l < "$EVAL_LOCAL") Q"

KEY="/tmp/rpkey"; cp ~/.ssh/runpod_aria "$KEY"; chmod 600 "$KEY"
SSH="ssh -i $KEY -o StrictHostKeyChecking=no -o ConnectTimeout=15 -o ServerAliveInterval=30 -o ServerAliveCountMax=10"
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

# 3. Push the on-pod driver + CURRENT scripts (pod checkout is old) + corpus + eval set
echo "[driver] pushing driver + current scripts + corpus + eval set..."
$SSH -p "$PORT" root@"$HOST" "mkdir -p /workspace/datasets /workspace/crucix/scripts/train"
scp -i "$KEY" -o StrictHostKeyChecking=no -P "$PORT" scripts/train/v0_3_pod_run.sh   root@"$HOST":/workspace/ || { echo "[driver] FATAL scp driver"; exit 1; }
for f in sft_train.py serve_eval_shim.py eval_aria_llm.py; do
  scp -i "$KEY" -o StrictHostKeyChecking=no -P "$PORT" "scripts/train/$f" root@"$HOST":/workspace/crucix/scripts/train/"$f" || { echo "[driver] FATAL scp $f"; exit 1; }
done
scp -i "$KEY" -o StrictHostKeyChecking=no -P "$PORT" "$TRAIN_CORPUS" root@"$HOST":/workspace/datasets/aria_sft_distill_500.jsonl || { echo "[driver] FATAL scp corpus"; exit 1; }
scp -i "$KEY" -o StrictHostKeyChecking=no -P "$PORT" "$EVAL_LOCAL"   root@"$HOST":/workspace/datasets/aria_eval_500q.jsonl       || { echo "[driver] FATAL scp eval set"; exit 1; }

# 4. Launch the cycle DETACHED on the pod (setsid+nohup → survives any SSH drop
#    over the 2-3h run), then poll for the EXIT-code sentinel the driver writes.
echo "[driver] launching v0.3 cycle DETACHED on the pod..."
$SSH -p "$PORT" root@"$HOST" \
  "rm -f /workspace/eval/_cycle_status; mkdir -p /workspace/logs; \
   TRAIN_FILE=/workspace/datasets/aria_sft_distill_500.jsonl \
   EVAL_SET=/workspace/datasets/aria_eval_500q.jsonl \
   DEEPSEEK_API_KEY='$DSK' EPOCHS=3 \
   setsid nohup bash /workspace/v0_3_pod_run.sh > /workspace/logs/v0_3_cycle.log 2>&1 < /dev/null & echo STARTED" \
  || { echo "[driver] FATAL: could not launch cycle on pod"; exit 1; }

echo "[driver] polling for completion (cap ~5h; breaks as soon as it finishes)..."
# R-F1473: ONE SSH per tick (was two — sentinel + log-tail). On Windows git-bash
# every ssh invocation forks through MSYS's fragile fork() emulation; two per tick
# x 200 ticks over 8h exhausted process handles -> errno 11 "Resource temporarily
# unavailable" killed the loop BEFORE it pulled the report (2026-06-09 v0.3 run).
# Fetch BOTH the sentinel and the last log line in a single SSH, parsed by a marker.
RC=""
for i in $(seq 1 200); do   # 200 * 120s ~ 6.6h cap; loop breaks the moment the sentinel appears
  sleep 120
  OUT=$($SSH -p "$PORT" root@"$HOST" \
    'printf "RC=%s\n" "$(cat /workspace/eval/_cycle_status 2>/dev/null)"; tail -1 /workspace/logs/v0_3_cycle.log 2>/dev/null' \
    2>/dev/null | tr -d '\r')
  RC=$(printf '%s\n' "$OUT" | sed -n 's/^RC=//p' | tr -d ' ')
  LINE=$(printf '%s\n' "$OUT" | grep -v '^RC=' | tail -1)
  echo "[driver] [$i/200] ${LINE:-(no log line yet)}"
  if [ -n "$RC" ]; then
    echo "[driver] cycle finished (exit code $RC)"
    break
  fi
done
[ -n "$RC" ] || echo "[driver] WARN: no completion signal within the cap — pulling whatever exists, then stopping"

# 5. Pull reports
mkdir -p data/eval_reports
scp -i "$KEY" -o StrictHostKeyChecking=no -P "$PORT" root@"$HOST":/workspace/eval/aria_llm_v0_3_eval.json data/eval_reports/aria_llm_v0_3_eval.json 2>/dev/null || echo "[driver] (v0.3 report not pulled)"
scp -i "$KEY" -o StrictHostKeyChecking=no -P "$PORT" root@"$HOST":/workspace/eval/aria_llm_v0_2_eval.json data/eval_reports/aria_llm_v0_2_eval_500q.json 2>/dev/null || echo "[driver] (v0.2 report not pulled)"
scp -i "$KEY" -o StrictHostKeyChecking=no -P "$PORT" root@"$HOST":/workspace/eval/deepseek_baseline_eval.json data/eval_reports/deepseek_baseline_eval_500q.json 2>/dev/null || echo "[driver] (deepseek report not pulled)"

# 6. Local verdict echo
echo "[driver] === v0.3 CYCLE RESULT (local) ==="
python - <<'PY'
import json, os
def line(tag, f):
    if not os.path.exists(f): print(f"{tag}: MISSING ({f})"); return None
    d=json.load(open(f,encoding="utf-8")); dd=d.get("defence_dd") or {}; pi=d.get("prompt_injection") or {}
    print(f"{tag}: judge-DD={dd.get('accuracy')} (n={dd.get('total')}) | leak_rate={pi.get('leak_rate')}")
    return dd.get("accuracy")
a3=line("v0.3", "data/eval_reports/aria_llm_v0_3_eval.json")
a2=line("v0.2", "data/eval_reports/aria_llm_v0_2_eval_500q.json")
line("DeepSeek", "data/eval_reports/deepseek_baseline_eval_500q.json")
if a3 is not None and a2 is not None:
    print(f"PROMOTE v0.3" if a3 >= a2 else "KEEP v0.2", f"(v0.3 {a3} vs v0.2 {a2})")
PY
stop_pod
echo "[driver] DONE — pod stopped."
