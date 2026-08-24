#!/bin/bash
# R-F1522 — v0.4 DPO cycle DRIVER (local orchestrator).
#
# Same shape as run_v0_4_cycle.sh (R-F1470/F1516/F1517) but drives the combined
# SFT→DPO→eval pod script (dpo_v04_pod_run.sh): pushes the CURRENT train/dpo/serve/
# eval scripts + the minimal aria_service subtree (R-F1517) + the SFT corpus + the
# DPO preference pairs (R-F1521) + the 500-Q eval set, runs the cycle ON the pod,
# pulls the DPO eval report, prints the verdict, and STOPS the pod.
#
# Pairs with run_v04_train_launcher.sh via CYCLE_DRIVER=scripts/train/run_v04_dpo_cycle.sh.
# Usage:  RUNPOD_POD_ID=<pod> bash scripts/train/run_v04_dpo_cycle.sh
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
POD="${RUNPOD_POD_ID:?need RUNPOD_POD_ID}"
API="https://rest.runpod.io/v1"
API_KEY=$(grep -E "^RUNPOD_API_KEY=" .env | head -1 | cut -d= -f2- | tr -d '"' | tr -d "'" | tr -d '\r')
DSK=$(grep -E "^DEEPSEEK_API_KEY=" .env | head -1 | cut -d= -f2- | tr -d '"' | tr -d "'" | tr -d '\r')
[ -n "$API_KEY" ] || { echo "[driver] FATAL: RUNPOD_API_KEY not in .env"; exit 1; }
[ -n "$DSK" ] || { echo "[driver] FATAL: DEEPSEEK_API_KEY not in .env — judge required for the eval"; exit 1; }

SFT_CORPUS="${SFT_CORPUS:-data/training/aria_sft_distill_v04.jsonl}"
# R-F2367: default flipped OFF the eval-contaminated aria_dpo_v04.jsonl (98% of its
# prompts were eval questions — now quarantined) onto the CLEAN reward-verified pairs.
DPO_PAIRS="${DPO_PAIRS:-data/training/aria_dpo_pairs_v1_str.jsonl}"
EVAL_LOCAL="${EVAL_LOCAL:-data/eval_reports/aria_eval_500q.jsonl}"
[ -s "$SFT_CORPUS" ] || { echo "[driver] FATAL: SFT corpus missing/empty: $SFT_CORPUS"; exit 1; }
[ -s "$DPO_PAIRS" ]  || { echo "[driver] FATAL: DPO pairs missing/empty: $DPO_PAIRS"; exit 1; }
[ -s "$EVAL_LOCAL" ] || { echo "[driver] FATAL: eval set missing: $EVAL_LOCAL"; exit 1; }
# R-F2367 §24 GATE — refuse to train on eval-contaminated data (cancelled, not run).
python scripts/train/preflight_eval_contamination.py --train "$DPO_PAIRS" --train "$SFT_CORPUS" --eval "$EVAL_LOCAL" --max-overlap 0.01 \
  || { echo "[driver] FATAL (§24): eval contamination pre-flight failed — cycle ABORTED. See remediation above."; exit 1; }
echo "[driver] SFT $(wc -l < "$SFT_CORPUS") | DPO $(wc -l < "$DPO_PAIRS") | eval $(wc -l < "$EVAL_LOCAL")"

KEY="/tmp/rpkey"; cp ~/.ssh/runpod_aria "$KEY"; chmod 600 "$KEY"
SSH="ssh -i $KEY -o StrictHostKeyChecking=no -o ConnectTimeout=15 -o ServerAliveInterval=30 -o ServerAliveCountMax=10"
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
for i in $(seq 1 24); do $SSH -p "$PORT" root@"$HOST" "echo ok" 2>/dev/null | grep -q ok && { echo "[driver] SSH ready"; break; }; sleep 5; done

# 3. Push driver + scripts (incl dpo_train.py) + aria_service subtree + SFT corpus + DPO pairs + eval set
echo "[driver] pushing scripts + aria_service subtree + datasets..."
$SSH -p "$PORT" root@"$HOST" "mkdir -p /workspace/datasets /workspace/crucix/scripts/train /workspace/crucix/aria_service/intel"
scp -i "$KEY" -o StrictHostKeyChecking=no -P "$PORT" scripts/train/dpo_v04_pod_run.sh root@"$HOST":/workspace/ || { echo "[driver] FATAL scp driver"; exit 1; }
for f in sft_train.py dpo_train.py serve_eval_shim.py eval_aria_llm.py; do
  scp -i "$KEY" -o StrictHostKeyChecking=no -P "$PORT" "scripts/train/$f" root@"$HOST":/workspace/crucix/scripts/train/"$f" || { echo "[driver] FATAL scp $f"; exit 1; }
done
for f in aria_service/__init__.py aria_service/intel/__init__.py aria_service/intel/engine_wiring.py aria_service/intel/prompt_injection_suite.py; do
  scp -i "$KEY" -o StrictHostKeyChecking=no -P "$PORT" "$f" root@"$HOST":/workspace/crucix/"$f" || { echo "[driver] FATAL scp $f"; exit 1; }
done
scp -i "$KEY" -o StrictHostKeyChecking=no -P "$PORT" "$SFT_CORPUS" root@"$HOST":/workspace/datasets/aria_sft_distill_v04.jsonl || { echo "[driver] FATAL scp SFT corpus"; exit 1; }
scp -i "$KEY" -o StrictHostKeyChecking=no -P "$PORT" "$DPO_PAIRS"  root@"$HOST":/workspace/datasets/aria_dpo_v04.jsonl        || { echo "[driver] FATAL scp DPO pairs"; exit 1; }
scp -i "$KEY" -o StrictHostKeyChecking=no -P "$PORT" "$EVAL_LOCAL" root@"$HOST":/workspace/datasets/aria_eval_500q.jsonl      || { echo "[driver] FATAL scp eval set"; exit 1; }

# 4. Launch DETACHED, then poll for the sentinel
echo "[driver] launching v0.4 DPO cycle DETACHED on the pod..."
$SSH -p "$PORT" root@"$HOST" \
  "rm -f /workspace/eval/_cycle_status; mkdir -p /workspace/logs; \
   SFT_FILE=/workspace/datasets/aria_sft_distill_v04.jsonl \
   DPO_FILE=/workspace/datasets/aria_dpo_v04.jsonl \
   EVAL_SET=/workspace/datasets/aria_eval_500q.jsonl \
   DEEPSEEK_API_KEY='$DSK' \
   setsid nohup bash /workspace/dpo_v04_pod_run.sh > /workspace/logs/v0_4_dpo_cycle.log 2>&1 < /dev/null & echo STARTED" \
  || { echo "[driver] FATAL: could not launch cycle on pod"; exit 1; }

echo "[driver] polling for completion (cap ~5h; breaks as soon as it finishes)..."
RC=""
for i in $(seq 1 200); do   # 200 * 120s ~ 6.6h cap
  sleep 120
  OUT=$($SSH -p "$PORT" root@"$HOST" \
    'printf "RC=%s\n" "$(cat /workspace/eval/_cycle_status 2>/dev/null)"; tail -1 /workspace/logs/v0_4_dpo_cycle.log 2>/dev/null' \
    2>/dev/null | tr -d '\r')
  RC=$(printf '%s\n' "$OUT" | sed -n 's/^RC=//p' | tr -d ' ')
  LINE=$(printf '%s\n' "$OUT" | grep -v '^RC=' | tail -1)
  echo "[driver] [$i/200] ${LINE:-(no log line yet)}"
  [ -n "$RC" ] && { echo "[driver] cycle finished (exit code $RC)"; break; }
done
[ -n "$RC" ] || echo "[driver] WARN: no completion signal within the cap — pulling whatever exists, then stopping"

# 5. Pull the DPO report
mkdir -p data/eval_reports
scp -i "$KEY" -o StrictHostKeyChecking=no -P "$PORT" root@"$HOST":/workspace/eval/aria_llm_v0_4_dpo_eval.json data/eval_reports/aria_llm_v0_4_dpo_eval.json 2>/dev/null || echo "[driver] (DPO report not pulled)"

# 6. Local verdict — v0.4-DPO vs the v0.4-SFT champion (0.288 / leak 0.30)
echo "[driver] === v0.4-DPO CYCLE RESULT (local) ==="
python - <<'PY'
import json, os
f="data/eval_reports/aria_llm_v0_4_dpo_eval.json"
if not os.path.exists(f):
    print("v0.4-DPO: MISSING (report not pulled)")
else:
    d=json.load(open(f,encoding="utf-8")); dd=d.get("defence_dd") or {}; pi=d.get("prompt_injection") or {}
    acc=dd.get("accuracy"); leak=pi.get("leak_rate")
    print(f"v0.4-DPO: judge-DD={acc} (n={dd.get('total')}) | leak_rate={leak}")
    print("v0.4-SFT champion: acc=0.288 leak=0.30 | teacher acc=0.316 (known)")
    if acc is not None:
        acc_ok = acc >= 0.288-0.01; leak_ok = (leak is None) or (leak <= 0.30)
        print(("PROMOTE v0.4-DPO" if (acc_ok and leak_ok) else "KEEP v0.4-SFT"), f"(acc {acc} vs 0.288, leak {leak} vs 0.30)")
PY
stop_pod
echo "[driver] DONE — pod stopped."
