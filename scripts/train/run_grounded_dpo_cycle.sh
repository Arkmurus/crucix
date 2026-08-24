#!/bin/bash
# R-F1945 — ONE-LINE grounded SFT->DPO->eval cycle (Track B cap-breaker launcher).
#
# Adapts the PROVEN v0.4 DPO driver (run_v04_dpo_cycle.sh, R-F1522) + on-pod
# orchestrator (dpo_v04_pod_run.sh) — same battle-tested mechanics (pod start via
# RunPod API, scp proven trainers + datasets, run SFT->DPO->eval on the pod,
# self-stop, pull report, verdict) — but points them at the GROUNDED corpus +
# the REWARD-VERIFIED DPO pairs built this session:
#   SFT  on data/training/aria_grounded_v1.jsonl   (664 grounded examples, R-F1939)
#   DPO  on data/training/aria_dpo_pairs_v1.jsonl   (469 pairs, preference VERIFIED
#                                                    by the R-F1942 grounding reward,
#                                                    R-F1943 — chosen=grounded >
#                                                    rejected=parametric)
#   eval vs DeepSeek on the open-book 500-Q set.
# The grounded SFT + grounding-reward DPO is the cap-breaker the v0.5-0.7 SFT-only
# cycles lacked (those capped ~0.31 < DeepSeek 0.34).
#
# REUSES the proven trainers (scripts/train/sft_train.py, dpo_train.py,
# eval_aria_llm.py) — NOT this session's data/training/ duplicates.
#
# Usage:  RUNPOD_POD_ID=<pod> bash scripts/train/run_grounded_dpo_cycle.sh
# Override datasets:  SFT_CORPUS=... DPO_PAIRS=... EVAL_LOCAL=... RUNPOD_POD_ID=<pod> bash ...
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
POD="${RUNPOD_POD_ID:?need RUNPOD_POD_ID (the RunPod pod id; §24 — operator-supplied)}"
API="https://rest.runpod.io/v1"
API_KEY=$(grep -E "^RUNPOD_API_KEY=" .env | head -1 | cut -d= -f2- | tr -d '"' | tr -d "'" | tr -d '\r')
DSK=$(grep -E "^DEEPSEEK_API_KEY=" .env | head -1 | cut -d= -f2- | tr -d '"' | tr -d "'" | tr -d '\r')
[ -n "$API_KEY" ] || { echo "[driver] FATAL: RUNPOD_API_KEY not in .env"; exit 1; }
[ -n "$DSK" ] || { echo "[driver] FATAL: DEEPSEEK_API_KEY not in .env — judge required for the eval"; exit 1; }

SFT_CORPUS="${SFT_CORPUS:-data/training/aria_grounded_v1.jsonl}"
DPO_PAIRS="${DPO_PAIRS:-data/training/aria_dpo_pairs_v1_str.jsonl}"  # R-F1945 fix: STRING format (prompt/chosen/rejected as str, no extra cols) so dpo_train.py's line-120 cleanup+remove_columns fires (the conversational+extras format made DPO produce no adapter, run 1 fail)
EVAL_LOCAL="${EVAL_LOCAL:-data/eval_reports/aria_eval_500q_openbook.jsonl}"
PI_LOCAL="${PI_LOCAL:-data/eval/pi_eval_set_v1.jsonl}"   # R-F2497 — n=155 injection eval set (on-pod leak was hardcoded n=10)
PARITY="${PARITY:-0.336}"   # DeepSeek open-book defence-DD baseline to beat
[ -s "$SFT_CORPUS" ] || { echo "[driver] FATAL: grounded SFT corpus missing/empty: $SFT_CORPUS"; exit 1; }
[ -s "$DPO_PAIRS" ]  || { echo "[driver] FATAL: verified DPO pairs missing/empty: $DPO_PAIRS"; exit 1; }
[ -s "$EVAL_LOCAL" ] || { echo "[driver] FATAL: open-book eval set missing: $EVAL_LOCAL"; exit 1; }
# R-F2367 §24 GATE — refuse to train on eval-contaminated data (cancelled, not run).
python scripts/train/preflight_eval_contamination.py --train "$DPO_PAIRS" --train "$SFT_CORPUS" --eval "$EVAL_LOCAL" --max-overlap 0.01 \
  || { echo "[driver] FATAL (§24): eval contamination pre-flight failed — cycle ABORTED. See remediation above."; exit 1; }
echo "[driver] grounded SFT $(wc -l < "$SFT_CORPUS") | verified DPO $(wc -l < "$DPO_PAIRS") | eval $(wc -l < "$EVAL_LOCAL") | parity>=$PARITY"

KEY="/tmp/rpkey"; cp ~/.ssh/runpod_aria "$KEY"; chmod 600 "$KEY"
SSH="ssh -i $KEY -o StrictHostKeyChecking=no -o ConnectTimeout=15 -o ServerAliveInterval=30 -o ServerAliveCountMax=10"
jget(){ python -c "import sys,json;d=json.load(sys.stdin);print(d.get('$1','') or '')" 2>/dev/null; }
pmget(){ python -c "import sys,json;d=json.load(sys.stdin);pm=d.get('portMappings') or {};print(pm.get('22') or '')" 2>/dev/null; }

stop_pod(){ echo "[driver] stopping pod $POD"; curl -s -X POST "$API/pods/$POD/stop" -H "Authorization: Bearer $API_KEY" >/dev/null 2>&1 || true; }
# NOTE: no `trap stop_pod EXIT` — the on-pod self-stop watcher (armed below) owns
# the pod lifecycle, so a driver/session death does NOT abort the detached cycle
# (the watcher stops the pod on completion/idle = no runaway, no abort). The
# normal end-of-run stop_pod still fires when this driver polls to completion.

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

# 3. Push the PROVEN on-pod orchestrator + proven trainers + aria_service subtree + datasets
echo "[driver] pushing proven scripts + aria_service subtree + grounded datasets..."
$SSH -p "$PORT" root@"$HOST" "mkdir -p /workspace/datasets /workspace/crucix/scripts/train /workspace/crucix/aria_service/intel"
scp -i "$KEY" -o StrictHostKeyChecking=no -P "$PORT" scripts/train/dpo_v04_pod_run.sh root@"$HOST":/workspace/ || { echo "[driver] FATAL scp orchestrator"; exit 1; }
scp -i "$KEY" -o StrictHostKeyChecking=no -P "$PORT" scripts/train/pod_selfstop_watch_v04.sh root@"$HOST":/workspace/ 2>/dev/null || echo "[driver] WARN: self-stop watcher scp failed"
for f in sft_train.py dpo_train.py serve_eval_shim.py eval_aria_llm.py; do
  scp -i "$KEY" -o StrictHostKeyChecking=no -P "$PORT" "scripts/train/$f" root@"$HOST":/workspace/crucix/scripts/train/"$f" || { echo "[driver] FATAL scp $f"; exit 1; }
done
for f in aria_service/__init__.py aria_service/intel/__init__.py aria_service/intel/engine_wiring.py aria_service/intel/prompt_injection_suite.py; do
  scp -i "$KEY" -o StrictHostKeyChecking=no -P "$PORT" "$f" root@"$HOST":/workspace/crucix/"$f" || { echo "[driver] FATAL scp $f"; exit 1; }
done
# Reuse the proven on-pod dataset paths (the orchestrator reads them via env below).
scp -i "$KEY" -o StrictHostKeyChecking=no -P "$PORT" "$SFT_CORPUS" root@"$HOST":/workspace/datasets/aria_sft_distill_v04.jsonl || { echo "[driver] FATAL scp SFT corpus"; exit 1; }
scp -i "$KEY" -o StrictHostKeyChecking=no -P "$PORT" "$DPO_PAIRS"  root@"$HOST":/workspace/datasets/aria_dpo_v04.jsonl        || { echo "[driver] FATAL scp DPO pairs"; exit 1; }
scp -i "$KEY" -o StrictHostKeyChecking=no -P "$PORT" "$EVAL_LOCAL" root@"$HOST":/workspace/datasets/aria_eval_500q.jsonl      || { echo "[driver] FATAL scp eval set"; exit 1; }
# R-F2497 — stage the n=155 PI eval set so on-pod leak-rate is measured at 155, not the built-in 10. Non-fatal: degrades to the built-in set if missing.
if [ -s "$PI_LOCAL" ]; then
  scp -i "$KEY" -o StrictHostKeyChecking=no -P "$PORT" "$PI_LOCAL" root@"$HOST":/workspace/datasets/pi_eval_set_v1.jsonl || echo "[driver] WARN: PI eval set scp failed — leak falls back to built-in n=10"
else
  echo "[driver] WARN: PI_LOCAL missing ($PI_LOCAL) — leak falls back to built-in n=10"
fi

# 4. Launch DETACHED (proven dpo_v04_pod_run.sh: SFT -> DPO -> serve -> eval), poll
echo "[driver] launching GROUNDED SFT->DPO->eval cycle DETACHED on the pod..."
$SSH -p "$PORT" root@"$HOST" \
  "rm -f /workspace/eval/_cycle_status; mkdir -p /workspace/logs; \
   SFT_FILE=/workspace/datasets/aria_sft_distill_v04.jsonl \
   DPO_FILE=/workspace/datasets/aria_dpo_v04.jsonl \
   EVAL_SET=/workspace/datasets/aria_eval_500q.jsonl \
   PI_SET=/workspace/datasets/pi_eval_set_v1.jsonl \
   DEEPSEEK_API_KEY='$DSK' \
   DPO_BETA='${DPO_BETA:-}' DPO_LR='${DPO_LR:-}' \
   setsid nohup bash /workspace/dpo_v04_pod_run.sh > /workspace/logs/grounded_dpo_cycle.log 2>&1 < /dev/null & echo STARTED" \
  || { echo "[driver] FATAL: could not launch cycle on pod"; exit 1; }

# 4b. AUTO-ARM the on-pod self-stop watcher (R-F1654 hardening) so the pod ALWAYS
# stops on completion/idle even if THIS driver dies — no runaway GPU spend, and
# the detached cycle survives a driver/session death.
echo "[driver] arming on-pod self-stop watcher (GRACE=1200)..."
$SSH -p "$PORT" root@"$HOST" \
  "POD_ID=$POD RP_KEY='$API_KEY' GRACE=1200 setsid nohup bash /workspace/pod_selfstop_watch_v04.sh >/workspace/logs/_selfstop.log 2>&1 < /dev/null & echo ARMED" \
  || echo "[driver] WARN: self-stop watcher arm failed — relying on EXIT trap"

echo "[driver] polling for completion (cap ~6.6h; breaks as soon as it finishes)..."
RC=""
for i in $(seq 1 200); do
  sleep 120
  OUT=$($SSH -p "$PORT" root@"$HOST" \
    'printf "RC=%s\n" "$(cat /workspace/eval/_cycle_status 2>/dev/null)"; tail -1 /workspace/logs/grounded_dpo_cycle.log 2>/dev/null' \
    2>/dev/null | tr -d '\r')
  RC=$(printf '%s\n' "$OUT" | sed -n 's/^RC=//p' | tr -d ' ')
  LINE=$(printf '%s\n' "$OUT" | grep -v '^RC=' | tail -1)
  echo "[driver] [$i/200] ${LINE:-(no log line yet)}"
  [ -n "$RC" ] && { echo "[driver] cycle finished (exit code $RC)"; break; }
done
[ -n "$RC" ] || echo "[driver] WARN: no completion signal within the cap — pulling whatever exists, then stopping"

# 5. Pull the DPO eval report
mkdir -p data/eval_reports
scp -i "$KEY" -o StrictHostKeyChecking=no -P "$PORT" root@"$HOST":/workspace/eval/aria_llm_v0_4_dpo_eval.json data/eval_reports/aria_llm_grounded_dpo_eval.json 2>/dev/null || echo "[driver] (DPO report not pulled)"

# 5b. R-F1945 — PERSIST the trained LoRA adapter durably (the pod is volume-free
# = wiped on stop, so the proven model is otherwise LOST). scp the adapter dir
# back so we never have to re-train just to recover it; this is also the source
# for the later HF push -> serverless deploy.
mkdir -p data/training/checkpoints
ADAPTER_LOCAL="data/training/checkpoints/aria_llm_grounded_dpo_v1"
if scp -r -i "$KEY" -o StrictHostKeyChecking=no -P "$PORT" root@"$HOST":/workspace/checkpoints/aria_llm_v0_4_dpo "$ADAPTER_LOCAL" 2>/dev/null; then
  echo "[driver] ✅ adapter persisted -> $ADAPTER_LOCAL ($(ls "$ADAPTER_LOCAL" 2>/dev/null | tr '\n' ' '))"
else
  echo "[driver] WARN: adapter scp-back failed (cycle may have failed before producing it)"
fi

# 6. G1 verdict — grounded-DPO vs DeepSeek parity
echo "[driver] === GROUNDED SFT->DPO CYCLE RESULT — GATE G1 (local) ==="
PARITY="$PARITY" python - <<'PY'
import json, os
f="data/eval_reports/aria_llm_grounded_dpo_eval.json"
parity=float(os.environ.get("PARITY","0.336"))
if not os.path.exists(f):
    print("grounded-DPO: MISSING (report not pulled)")
else:
    d=json.load(open(f,encoding="utf-8")); dd=d.get("defence_dd") or {}; pi=d.get("prompt_injection") or {}
    acc=dd.get("accuracy"); leak=pi.get("leak_rate")
    print(f"grounded-DPO: judge-DD={acc} (n={dd.get('total')}) | leak_rate={leak}")
    print(f"prior SFT-only cap ~0.31 | DeepSeek parity {parity}")
    if acc is not None:
        print(f"G1 accuracy: {'PASS' if acc>=parity else 'FAIL'} ({acc} vs {parity} parity)")
        print("=> if PASS (acc>=parity AND PI leak under threshold): set ARIA_LLM_URL = reasoning independence; else DeepSeek stays (honest §24).")
PY
stop_pod
echo "[driver] DONE — pod stopped."
