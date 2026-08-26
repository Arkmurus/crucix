#!/bin/bash
# R-F2010 — local driver for the GRPO RLVR cycle. Mirrors run_grounded_dpo_cycle.sh
# (proven pod plumbing) but runs grpo_pod_run.sh: GRPO against the grounding reward,
# starting from the SAVED DPO adapter (uploaded as init). Self-stops; pulls the GRPO
# adapter + judge report. Objective grounding eval is run OFF-POD by the operator/driver
# afterwards (the judge number is biased — do not trust it for promotion).
#
# Usage: RUNPOD_POD_ID=<pod> bash scripts/train/run_grpo_cycle.sh
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
[ -n "$API_KEY" ] || { echo "[driver] FATAL: RUNPOD_API_KEY missing"; exit 1; }

# R-F2762 — default to the KEYWORDED prompt set. aria_grpo_prompts_v1.jsonl has the
# same 664 prompts but ZERO expected_keywords, so it silently kills the recall reward
# term (the whole point of the R-F2558 grounded cycle). grpo_grounded_prompts_v2.jsonl
# = the identical prompts + keywords (verified: same 664 questions). The grpo_train.py
# --dry-run now HARD-FAILS a recall-weighted cycle on a keyword-less set, so this default
# and that guard together make a silent no-op recall cycle impossible.
PROMPTS="${PROMPTS:-data/training/grpo_grounded_prompts_v2.jsonl}"
EVAL_LOCAL="${EVAL_LOCAL:-data/eval_reports/aria_eval_500q_openbook.jsonl}"
INIT_ADAPTER="${INIT_ADAPTER:-data/training/checkpoints/aria_llm_grounded_dpo_v1/aria_llm_v0_4_dpo}"
[ -s "$PROMPTS" ] || { echo "[driver] FATAL: prompts missing: $PROMPTS"; exit 1; }
[ -s "$EVAL_LOCAL" ] || { echo "[driver] FATAL: eval set missing: $EVAL_LOCAL"; exit 1; }
# R-F2549 — INIT_ON_POD starts the cycle from an adapter ALREADY on the pod volume
# (e.g. the v0.7 DPO at /workspace/checkpoints/aria_llm_v0_4_dpo on vol swsj40xxvl),
# skipping the local check + upload (that adapter isn't kept locally).
if [ -z "${INIT_ON_POD:-}" ]; then
  [ -f "$INIT_ADAPTER/adapter_config.json" ] || { echo "[driver] FATAL: init adapter missing: $INIT_ADAPTER"; exit 1; }
fi
echo "[driver] GRPO prompts $(wc -l < "$PROMPTS") | eval $(wc -l < "$EVAL_LOCAL") | init $INIT_ADAPTER"

KEY="/tmp/rpkey"; cp ~/.ssh/runpod_aria "$KEY"; chmod 600 "$KEY"
SSH="ssh -i $KEY -o StrictHostKeyChecking=no -o ConnectTimeout=15 -o ServerAliveInterval=30 -o ServerAliveCountMax=10"
jget(){ python -c "import sys,json;d=json.load(sys.stdin);print(d.get('$1','') or '')" 2>/dev/null; }
pmget(){ python -c "import sys,json;d=json.load(sys.stdin);pm=d.get('portMappings') or {};print(pm.get('22') or '')" 2>/dev/null; }
stop_pod(){ echo "[driver] stopping pod $POD"; curl -s -X POST "$API/pods/$POD/stop" -H "Authorization: Bearer $API_KEY" >/dev/null 2>&1 || true; }

# 1. ensure RUNNING + get host/port
HOST=""; PORT=""
for i in $(seq 1 20); do
  PD=$(curl -s "$API/pods/$POD" -H "Authorization: Bearer $API_KEY")
  ST=$(echo "$PD" | jget desiredStatus)
  if [ "$ST" != "RUNNING" ]; then curl -s -X POST "$API/pods/$POD/start" -H "Authorization: Bearer $API_KEY" >/dev/null 2>&1; fi
  for j in $(seq 1 18); do
    PD=$(curl -s "$API/pods/$POD" -H "Authorization: Bearer $API_KEY")
    ST=$(echo "$PD" | jget desiredStatus); HOST=$(echo "$PD" | jget publicIp); PORT=$(echo "$PD" | pmget)
    [ "$ST" = "RUNNING" ] && [ -n "$HOST" ] && [ -n "$PORT" ] && break; sleep 8
  done
  [ -n "$HOST" ] && [ -n "$PORT" ] && { echo "[driver] pod RUNNING $HOST:$PORT"; break; }; sleep 30
done
[ -n "$HOST" ] && [ -n "$PORT" ] || { echo "[driver] FATAL: pod not RUNNING"; exit 1; }

# 2. wait SSH
for i in $(seq 1 24); do $SSH -p "$PORT" root@"$HOST" "echo ok" 2>/dev/null | grep -q ok && { echo "[driver] SSH ready"; break; }; sleep 5; done

# 3. push scripts + aria_service subtree + datasets + init adapter
echo "[driver] pushing scripts + grounding_reward + datasets + init adapter…"
$SSH -p "$PORT" root@"$HOST" "mkdir -p /workspace/datasets /workspace/crucix/scripts/train /workspace/crucix/aria_service/intel /workspace/checkpoints/aria_llm_init"
# R-F2037 — POD_RUN_SCRIPT selects the on-pod runner: default grpo_pod_run.sh
# (HF generate, 4-bit), or grpo_vllm_pod_run.sh (vLLM colocate, A100-80, full dataset)
# via USE_VLLM=1 bash scripts/train/run_grpo_cycle.sh.
POD_RUN_SCRIPT="${POD_RUN_SCRIPT:-grpo_pod_run.sh}"
[ "${USE_VLLM:-0}" = "1" ] && POD_RUN_SCRIPT="grpo_vllm_pod_run.sh"
echo "[driver] on-pod runner: $POD_RUN_SCRIPT"
for f in grpo_train.py grpo_pod_run.sh grpo_vllm_pod_run.sh serve_eval_shim.py eval_aria_llm.py pod_selfstop_watch_v04.sh; do
  scp -i "$KEY" -o StrictHostKeyChecking=no -P "$PORT" "scripts/train/$f" root@"$HOST":/workspace/crucix/scripts/train/"$f" 2>/dev/null || \
  scp -i "$KEY" -o StrictHostKeyChecking=no -P "$PORT" "scripts/train/$f" root@"$HOST":/workspace/"$f" || { echo "[driver] FATAL scp $f"; exit 1; }
done
# the selected runner + selfstop also at /workspace root (launched from there)
scp -i "$KEY" -o StrictHostKeyChecking=no -P "$PORT" "scripts/train/$POD_RUN_SCRIPT" root@"$HOST":/workspace/ || exit 1
# R-F4350 (C-295) — the pod runner sources hf_cache_select.sh and fails
# CLOSED without it, so the selector must ship alongside the runner.
scp -i "$KEY" -o StrictHostKeyChecking=no -P "$PORT" scripts/train/hf_cache_select.sh root@"$HOST":/workspace/ 2>/dev/null || echo "[driver] WARN selfstop scp"
scp -i "$KEY" -o StrictHostKeyChecking=no -P "$PORT" scripts/train/pod_selfstop_watch_v04.sh root@"$HOST":/workspace/ 2>/dev/null || echo "[driver] WARN selfstop scp"
for f in aria_service/__init__.py aria_service/intel/__init__.py aria_service/intel/grounding_reward.py; do
  scp -i "$KEY" -o StrictHostKeyChecking=no -P "$PORT" "$f" root@"$HOST":/workspace/crucix/"$f" || { echo "[driver] FATAL scp $f"; exit 1; }
done
scp -i "$KEY" -o StrictHostKeyChecking=no -P "$PORT" "$PROMPTS" root@"$HOST":/workspace/datasets/aria_grpo_prompts_v1.jsonl || exit 1
scp -i "$KEY" -o StrictHostKeyChecking=no -P "$PORT" "$EVAL_LOCAL" root@"$HOST":/workspace/datasets/aria_eval_500q.jsonl || exit 1
echo "[driver] uploading init adapter (may take ~30s, 335MB)…"
if [ -n "${INIT_ON_POD:-}" ]; then
  # R-F2549 — adapter already on the pod volume; copy it into the init dir the runner reads.
  $SSH -p "$PORT" root@"$HOST" "cp -rf $INIT_ON_POD/* /workspace/checkpoints/aria_llm_init/ && test -f /workspace/checkpoints/aria_llm_init/adapter_config.json" || { echo "[driver] FATAL: INIT_ON_POD adapter missing on pod: $INIT_ON_POD"; exit 1; }
  echo "[driver] init adapter from ON-POD: $INIT_ON_POD"
else
  scp -r -i "$KEY" -o StrictHostKeyChecking=no -P "$PORT" "$INIT_ADAPTER"/* root@"$HOST":/workspace/checkpoints/aria_llm_init/ || { echo "[driver] FATAL scp init adapter"; exit 1; }
fi

# 4. launch detached + arm self-stop watcher
echo "[driver] launching GRPO cycle DETACHED…"
$SSH -p "$PORT" root@"$HOST" \
  "rm -f /workspace/eval/_cycle_status; mkdir -p /workspace/logs; \
   INIT_ADAPTER=/workspace/checkpoints/aria_llm_init \
   PROMPTS=/workspace/datasets/aria_grpo_prompts_v1.jsonl \
   EVAL_SET=/workspace/datasets/aria_eval_500q.jsonl \
   GRPO_OUT='${GRPO_OUT:-/workspace/checkpoints/aria_llm_grpo_v1}' \
   GRPO_REPORT='${GRPO_REPORT:-/workspace/eval/aria_llm_grpo_v1_eval.json}' \
   MAX_PROMPTS='${MAX_PROMPTS:-220}' \
   GROUNDING_PRECISION_WEIGHT='${GROUNDING_PRECISION_WEIGHT:-0.5}' \
   GROUNDING_RECALL_BONUS_WEIGHT='${GROUNDING_RECALL_BONUS_WEIGHT:-0.0}' \
   GROUNDING_RECALL_BONUS_CAP='${GROUNDING_RECALL_BONUS_CAP:-0.25}' \
   GROUNDING_GROUNDED_RECALL='${GROUNDING_GROUNDED_RECALL:-0}' \
   DEEPSEEK_API_KEY='$DSK' \
   setsid nohup bash /workspace/$POD_RUN_SCRIPT > /workspace/logs/grpo_cycle.log 2>&1 < /dev/null & echo STARTED" || { echo "[driver] FATAL launch"; exit 1; }
# R-F2558: the _cycle_status-based self-stop watcher misfired on STALE cross-cycle
# sentinel state (it lives on the shared volume) and hard-killed a run at step
# 109/110, losing ~7h. Replace it with a DUMB absolute-time backstop: no sentinel
# dependency, cannot misfire on stale state. Kill any leftover watchers first so
# they can't accumulate and stop the pod out from under a live run.
BACKSTOP_SECS="${BACKSTOP_SECS:-28800}"   # 8h hard ceiling; the driver/monitor stops the pod earlier on genuine completion
$SSH -p "$PORT" root@"$HOST" \
  "pkill -f pod_selfstop_watch 2>/dev/null; pkill -f selfstop_watch 2>/dev/null; \
   setsid nohup bash -c 'sleep $BACKSTOP_SECS; curl -s -X POST $API/pods/$POD/stop -H \"Authorization: Bearer $API_KEY\"' >/workspace/logs/_backstop.log 2>&1 < /dev/null & echo ARMED_BACKSTOP_${BACKSTOP_SECS}s" || echo "[driver] WARN backstop arm"

echo "[driver] polling (cap ~6.6h)…"
RC=""
for i in $(seq 1 200); do
  sleep 120
  OUT=$($SSH -p "$PORT" root@"$HOST" 'printf "RC=%s\n" "$(cat /workspace/eval/_cycle_status 2>/dev/null)"; tail -1 /workspace/logs/grpo_cycle.log 2>/dev/null' 2>/dev/null | tr -d '\r')
  RC=$(printf '%s\n' "$OUT" | sed -n 's/^RC=//p' | tr -d ' ')
  echo "[driver] [$i/200] $(printf '%s\n' "$OUT" | grep -v '^RC=' | tail -1)"
  [ -n "$RC" ] && { echo "[driver] cycle finished (exit $RC)"; break; }
done

# 5. pull GRPO adapter + judge report
mkdir -p data/eval_reports data/training/checkpoints
scp -i "$KEY" -o StrictHostKeyChecking=no -P "$PORT" root@"$HOST":/workspace/eval/aria_llm_grpo_v1_eval.json data/eval_reports/aria_llm_grpo_v1_eval.json 2>/dev/null || echo "[driver] (grpo report not pulled)"
scp -r -i "$KEY" -o StrictHostKeyChecking=no -P "$PORT" root@"$HOST":/workspace/checkpoints/aria_llm_grpo_v1 data/training/checkpoints/aria_llm_grpo_v1 2>/dev/null && echo "[driver] ✅ GRPO adapter persisted" || echo "[driver] WARN adapter scp-back failed"

echo "[driver] === GRPO CYCLE DONE — run the OBJECTIVE grounding eval off-pod (judge number is biased) ==="
stop_pod
echo "[driver] DONE — pod stop requested."
