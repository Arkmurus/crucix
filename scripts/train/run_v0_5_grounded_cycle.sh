#!/bin/bash
# R-F1644 — v0.5 GROUNDED SFT cycle DRIVER (local orchestrator, Stage-1 Lane-A).
#
# Identical machinery to the proven R-F1470 v0.4 driver (run_v0_4_cycle.sh) —
# start pod, scp current scripts + corpus + eval, run train->serve->eval->verdict
# DETACHED on the pod, poll, pull reports, print verdict, STOP the pod (EXIT trap).
# The ONLY differences: it trains on the GROUNDED corpus (aria_grounded_v1.jsonl,
# R-F1641) and evaluates OPEN-BOOK (aria_eval_500q_openbook.jsonl) — so it measures
# whether grounded training lets the model EXPLOIT retrieved context (Stage 0 proved
# v0.4 could not: open-book == closed-book == 0.272).
#
# GATE G1 (CLAUDE.md §24 / stage1 plan): defence-DD >= 0.316 (DeepSeek parity) AND
# PI leak under threshold -> only then wire ARIA_LLM_URL. If no lift, DeepSeek stays
# primary — the honest §24 outcome, not a failure to hide.
#
# Usage:  RUNPOD_POD_ID=<pod> bash scripts/train/run_v0_5_grounded_cycle.sh
#   (the v0.4 launcher feeds RUNPOD_POD_ID after creating a GPU pod)
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
POD="${RUNPOD_POD_ID:-2adkzeri6fa2zi}"
API="https://rest.runpod.io/v1"
API_KEY=$(grep -E "^RUNPOD_API_KEY=" .env | head -1 | cut -d= -f2- | tr -d '"' | tr -d "'" | tr -d '\r')
DSK=$(grep -E "^DEEPSEEK_API_KEY=" .env | head -1 | cut -d= -f2- | tr -d '"' | tr -d "'" | tr -d '\r')
[ -n "$API_KEY" ] || { echo "[driver] FATAL: RUNPOD_API_KEY not in .env"; exit 1; }
[ -n "$DSK" ] || echo "[driver] WARN: DEEPSEEK_API_KEY not in .env — judge + DeepSeek baseline will be skipped"

TRAIN_CORPUS="${TRAIN_CORPUS:-data/training/aria_grounded_v1.jsonl}"
EVAL_LOCAL="${EVAL_LOCAL:-data/eval_reports/aria_eval_500q_openbook.jsonl}"   # OPEN-BOOK
[ -s "$TRAIN_CORPUS" ] || { echo "[driver] FATAL: grounded corpus missing/empty: $TRAIN_CORPUS"; exit 1; }
[ -s "$EVAL_LOCAL" ]   || { echo "[driver] FATAL: open-book eval set missing: $EVAL_LOCAL"; exit 1; }
echo "[driver] grounded corpus $(wc -l < "$TRAIN_CORPUS") rows; open-book eval $(wc -l < "$EVAL_LOCAL") Q"

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

# 3. Push the on-pod driver + CURRENT scripts (pod checkout is old) + grounded corpus + open-book eval
echo "[driver] pushing driver + current scripts + grounded corpus + open-book eval..."
$SSH -p "$PORT" root@"$HOST" "mkdir -p /workspace/datasets /workspace/crucix/scripts/train"
scp -i "$KEY" -o StrictHostKeyChecking=no -P "$PORT" scripts/train/v0_4_pod_run.sh   root@"$HOST":/workspace/ || { echo "[driver] FATAL scp driver"; exit 1; }
for f in sft_train.py serve_eval_shim.py eval_aria_llm.py; do
  scp -i "$KEY" -o StrictHostKeyChecking=no -P "$PORT" "scripts/train/$f" root@"$HOST":/workspace/crucix/scripts/train/"$f" || { echo "[driver] FATAL scp $f"; exit 1; }
done
scp -i "$KEY" -o StrictHostKeyChecking=no -P "$PORT" "$TRAIN_CORPUS" root@"$HOST":/workspace/datasets/aria_grounded_v1.jsonl  || { echo "[driver] FATAL scp corpus"; exit 1; }
scp -i "$KEY" -o StrictHostKeyChecking=no -P "$PORT" "$EVAL_LOCAL"   root@"$HOST":/workspace/datasets/aria_eval_openbook.jsonl || { echo "[driver] FATAL scp eval set"; exit 1; }

# R-F1517: push the MINIMAL aria_service subtree eval_aria_llm.py imports (lazy brain).
$SSH -p "$PORT" root@"$HOST" "mkdir -p /workspace/crucix/aria_service/intel"
for f in aria_service/__init__.py aria_service/intel/__init__.py aria_service/intel/engine_wiring.py aria_service/intel/prompt_injection_suite.py; do
  scp -i "$KEY" -o StrictHostKeyChecking=no -P "$PORT" "$f" root@"$HOST":/workspace/crucix/"$f" || { echo "[driver] FATAL scp $f"; exit 1; }
done

# 4. Launch the cycle DETACHED on the pod (setsid+nohup survives SSH drop over the 2-3h run)
echo "[driver] launching v0.5 GROUNDED cycle DETACHED on the pod..."
$SSH -p "$PORT" root@"$HOST" \
  "rm -f /workspace/eval/_cycle_status; mkdir -p /workspace/logs; \
   TRAIN_FILE=/workspace/datasets/aria_grounded_v1.jsonl \
   EVAL_SET=/workspace/datasets/aria_eval_openbook.jsonl \
   DEEPSEEK_API_KEY='$DSK' EPOCHS=${EPOCHS:-3} SKIP_TEACHER_EVAL='${SKIP_TEACHER_EVAL:-}' \
   setsid nohup bash /workspace/v0_4_pod_run.sh > /workspace/logs/v0_5_cycle.log 2>&1 < /dev/null & echo STARTED" \
  || { echo "[driver] FATAL: could not launch cycle on pod"; exit 1; }

echo "[driver] polling for completion (cap ~6.6h; breaks as soon as it finishes)..."
RC=""
for i in $(seq 1 200); do
  sleep 120
  OUT=$($SSH -p "$PORT" root@"$HOST" \
    'printf "RC=%s\n" "$(cat /workspace/eval/_cycle_status 2>/dev/null)"; tail -1 /workspace/logs/v0_5_cycle.log 2>/dev/null' \
    2>/dev/null | tr -d '\r')
  RC=$(printf '%s\n' "$OUT" | sed -n 's/^RC=//p' | tr -d ' ')
  LINE=$(printf '%s\n' "$OUT" | grep -v '^RC=' | tail -1)
  echo "[driver] [$i/200] ${LINE:-(no log line yet)}"
  if [ -n "$RC" ]; then echo "[driver] cycle finished (exit code $RC)"; break; fi
done
[ -n "$RC" ] || echo "[driver] WARN: no completion signal within the cap — pulling whatever exists, then stopping"

# 5. Pull reports (the pod writes the standard v0_4 report names; pull to v0_5_grounded locals)
mkdir -p data/eval_reports
scp -i "$KEY" -o StrictHostKeyChecking=no -P "$PORT" root@"$HOST":/workspace/eval/aria_llm_v0_4_eval.json data/eval_reports/aria_llm_v0_5_grounded_eval.json 2>/dev/null || echo "[driver] (v0.5 grounded report not pulled)"
scp -i "$KEY" -o StrictHostKeyChecking=no -P "$PORT" root@"$HOST":/workspace/eval/deepseek_baseline_eval.json data/eval_reports/deepseek_baseline_openbook.json 2>/dev/null || echo "[driver] (deepseek report not pulled)"

# 6. Local G1 verdict — grounded v0.5 (open-book) vs DeepSeek 0.316 parity
echo "[driver] === v0.5 GROUNDED CYCLE RESULT — GATE G1 (local) ==="
python - <<'PY'
import json, os
def line(tag, f):
    if not os.path.exists(f): print(f"{tag}: MISSING ({f})"); return None, None
    d=json.load(open(f,encoding="utf-8")); dd=d.get("defence_dd") or {}; pi=d.get("prompt_injection") or {}
    print(f"{tag}: judge-DD={dd.get('accuracy')} (n={dd.get('total')}) | leak_rate={pi.get('leak_rate')}")
    return dd.get("accuracy"), pi.get("leak_rate")
a5, leak5 = line("v0.5 GROUNDED (open-book)", "data/eval_reports/aria_llm_v0_5_grounded_eval.json")
print("v0.4 prior (open-book): 0.272  |  DeepSeek parity target: 0.316")
if a5 is not None:
    parity = a5 >= 0.316
    print(f"G1 accuracy: {'PASS' if parity else 'FAIL'} (v0.5 {a5} vs 0.316 parity)")
    print("NOTE: PI-leak gate uses ARIA's >=100-prompt C2 set; the n=10 leak here is directional only.")
    print("=> WIRE ARIA_LLM_URL only if accuracy>=0.316 AND C2 leak under threshold; else DeepSeek stays primary (honest §24).")
PY
stop_pod
echo "[driver] DONE — pod stopped."
