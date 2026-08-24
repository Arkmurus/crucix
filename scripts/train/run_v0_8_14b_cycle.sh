#!/bin/bash
# R-F1667 — v0.8 GROUNDED SFT cycle DRIVER (Stage-1 Lane-A, bigger+sharper corpus).
#
# Same machinery as the v0.5 driver (R-F1644) but HARDENED against the v0.5
# failure mode (local SSH poll HUNG mid-eval on Windows git-bash → driver never
# pulled; only the on-pod self-stop watcher saved the run):
#   1. AUTO-ARM the on-pod self-stop watcher right after launch (no manual step).
#      => the pod self-stops + preserves reports even if THIS driver dies/hangs.
#   2. timeout-wrap EVERY poll SSH so a hung connection can't freeze the loop —
#      a dead tick just returns empty and the loop continues.
# Trains on the bigger+sharper v0.8 corpus (aria_grounded_v3.jsonl) and evals
# OPEN-BOOK. G1: defence-DD >= DeepSeek parity AND PI leak under the C2 set.
#
# Usage:  RUNPOD_POD_ID=<pod> bash scripts/train/run_v0_8_grounded_cycle.sh
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

TRAIN_CORPUS="${TRAIN_CORPUS:-data/training/aria_grounded_v3.jsonl}"
EVAL_LOCAL="${EVAL_LOCAL:-data/eval_reports/aria_eval_500q_openbook.jsonl}"   # OPEN-BOOK
[ -s "$TRAIN_CORPUS" ] || { echo "[driver] FATAL: v0.8 corpus missing/empty: $TRAIN_CORPUS"; exit 1; }
[ -s "$EVAL_LOCAL" ]   || { echo "[driver] FATAL: open-book eval set missing: $EVAL_LOCAL"; exit 1; }
echo "[driver] v0.8 corpus $(wc -l < "$TRAIN_CORPUS") rows; open-book eval $(wc -l < "$EVAL_LOCAL") Q"

KEY="/tmp/rpkey"; cp ~/.ssh/runpod_aria "$KEY"; chmod 600 "$KEY"
SSH="ssh -i $KEY -o StrictHostKeyChecking=no -o ConnectTimeout=15 -o ServerAliveInterval=30 -o ServerAliveCountMax=6"
TSSH(){ timeout 75 $SSH "$@"; }   # R-F1667: hard cap so a hung SSH never freezes the loop
jget(){ python -c "import sys,json;d=json.load(sys.stdin);print(d.get('$1','') or '')" 2>/dev/null; }
pmget(){ python -c "import sys,json;d=json.load(sys.stdin);pm=d.get('portMappings') or {};print(pm.get('22') or '')" 2>/dev/null; }

stop_pod(){ echo "[driver] stopping pod $POD"; curl -s -X POST "$API/pods/$POD/stop" -H "Authorization: Bearer $API_KEY" >/dev/null 2>&1 || true; }
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
for i in $(seq 1 24); do TSSH -p "$PORT" root@"$HOST" "echo ok" 2>/dev/null | grep -q ok && { echo "[driver] SSH ready"; break; }; sleep 5; done

# 3. Push driver + current scripts + v0.8 corpus + open-book eval + self-stop watcher
echo "[driver] pushing driver + scripts + v0.8 corpus + open-book eval + self-stop watcher..."
TSSH -p "$PORT" root@"$HOST" "mkdir -p /workspace/datasets /workspace/crucix/scripts/train /workspace/logs"
scp -i "$KEY" -o StrictHostKeyChecking=no -P "$PORT" scripts/train/v0_4_pod_run.sh            root@"$HOST":/workspace/ || { echo "[driver] FATAL scp driver"; exit 1; }
scp -i "$KEY" -o StrictHostKeyChecking=no -P "$PORT" scripts/train/pod_selfstop_watch_v04.sh   root@"$HOST":/workspace/ || { echo "[driver] FATAL scp watcher"; exit 1; }
for f in sft_train.py serve_eval_shim.py eval_aria_llm.py; do
  scp -i "$KEY" -o StrictHostKeyChecking=no -P "$PORT" "scripts/train/$f" root@"$HOST":/workspace/crucix/scripts/train/"$f" || { echo "[driver] FATAL scp $f"; exit 1; }
done
scp -i "$KEY" -o StrictHostKeyChecking=no -P "$PORT" "$TRAIN_CORPUS" root@"$HOST":/workspace/datasets/aria_grounded_v3.jsonl  || { echo "[driver] FATAL scp corpus"; exit 1; }
scp -i "$KEY" -o StrictHostKeyChecking=no -P "$PORT" "$EVAL_LOCAL"   root@"$HOST":/workspace/datasets/aria_eval_openbook.jsonl || { echo "[driver] FATAL scp eval set"; exit 1; }
TSSH -p "$PORT" root@"$HOST" "mkdir -p /workspace/crucix/aria_service/intel"
for f in aria_service/__init__.py aria_service/intel/__init__.py aria_service/intel/engine_wiring.py aria_service/intel/prompt_injection_suite.py; do
  scp -i "$KEY" -o StrictHostKeyChecking=no -P "$PORT" "$f" root@"$HOST":/workspace/crucix/"$f" || { echo "[driver] FATAL scp $f"; exit 1; }
done

# 4. Launch the cycle DETACHED, then AUTO-ARM the self-stop watcher (R-F1667 hardening)
echo "[driver] launching v0.8 GROUNDED cycle DETACHED on the pod..."
TSSH -p "$PORT" root@"$HOST" \
  "rm -f /workspace/eval/_cycle_status; mkdir -p /workspace/logs; \
   BASE_MODEL='${BASE_MODEL:-Qwen/Qwen2.5-14B-Instruct}' ARIA_SKIP_ARCH_CHECK=1 \
   HF_HUB_DISABLE_XET=1 \
   TRAIN_FILE=/workspace/datasets/aria_grounded_v3.jsonl \
   EVAL_SET=/workspace/datasets/aria_eval_openbook.jsonl \
   DEEPSEEK_API_KEY='$DSK' EPOCHS=${EPOCHS:-3} SKIP_TEACHER_EVAL='${SKIP_TEACHER_EVAL:-}' \
   setsid nohup bash /workspace/v0_4_pod_run.sh > /workspace/logs/v0_8_cycle.log 2>&1 < /dev/null & echo STARTED" \
  || { echo "[driver] FATAL: could not launch cycle on pod"; exit 1; }
echo "[driver] AUTO-ARMING on-pod self-stop watcher (safety even if this driver dies)..."
TSSH -p "$PORT" root@"$HOST" \
  "POD_ID=$POD RP_KEY='$API_KEY' GRACE=900 setsid nohup bash /workspace/pod_selfstop_watch_v04.sh >/workspace/logs/_selfstop_launch.log 2>&1 < /dev/null & echo ARMED" \
  || echo "[driver] WARN: self-stop watcher arm failed — relying on EXIT trap"

echo "[driver] polling for completion (cap ~6.6h; timeout-wrapped SSH)..."
RC=""
for i in $(seq 1 200); do
  sleep 120
  OUT=$(TSSH -p "$PORT" root@"$HOST" \
    'printf "RC=%s\n" "$(cat /workspace/eval/_cycle_status 2>/dev/null)"; tail -1 /workspace/logs/v0_8_cycle.log 2>/dev/null' \
    2>/dev/null | tr -d '\r')
  RC=$(printf '%s\n' "$OUT" | sed -n 's/^RC=//p' | tr -d ' ')
  LINE=$(printf '%s\n' "$OUT" | grep -v '^RC=' | tail -1)
  echo "[driver] [$i/200] ${LINE:-(no log line yet)}"
  if [ -n "$RC" ]; then echo "[driver] cycle finished (exit code $RC)"; break; fi
done
[ -n "$RC" ] || echo "[driver] WARN: no completion signal within cap — pulling whatever exists, then stopping"

# 5. Pull reports
mkdir -p data/eval_reports
scp -i "$KEY" -o StrictHostKeyChecking=no -P "$PORT" root@"$HOST":/workspace/eval/aria_llm_v0_4_eval.json data/eval_reports/aria_llm_v0_8_grounded_eval.json 2>/dev/null || echo "[driver] (v0.8 report not pulled)"
scp -i "$KEY" -o StrictHostKeyChecking=no -P "$PORT" root@"$HOST":/workspace/eval/deepseek_baseline_eval.json data/eval_reports/deepseek_baseline_openbook_v06.json 2>/dev/null || echo "[driver] (deepseek report not pulled)"

# 6. G1 verdict
echo "[driver] === v0.8 GROUNDED CYCLE RESULT — GATE G1 (local) ==="
python - <<'PY'
import json, os
def line(tag, f):
    if not os.path.exists(f): print(f"{tag}: MISSING ({f})"); return None
    d=json.load(open(f,encoding="utf-8")); dd=d.get("defence_dd") or {}; pi=d.get("prompt_injection") or {}
    print(f"{tag}: judge-DD={dd.get('accuracy')} (n={dd.get('total')}) | leak_rate={pi.get('leak_rate')}")
    return dd.get("accuracy")
a6=line("v0.8 GROUNDED (open-book)", "data/eval_reports/aria_llm_v0_8_grounded_eval.json")
ds=line("DeepSeek (open-book)", "data/eval_reports/deepseek_baseline_openbook_v06.json")
print("v0.5 prior: 0.30")
if a6 is not None:
    tgt = ds or 0.336
    print(f"G1 accuracy: {'PASS' if a6>=tgt else 'FAIL'} (v0.8 {a6} vs {tgt} parity)")
    print("=> WIRE ARIA_LLM_URL only if accuracy>=parity AND C2(>=100-prompt) leak under threshold; else DeepSeek stays (honest §24).")
PY
stop_pod
echo "[driver] DONE — pod stopped."
