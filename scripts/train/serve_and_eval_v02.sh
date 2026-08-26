#!/usr/bin/env bash
# =============================================================================
# ARIA-LLM v0.2 — serve + eval runbook (R-F1388 prep)
#
# Usage:
#   export RUNPOD_API_KEY="..."        # RunPod API key
#   export RUNPOD_POD_ID="7ei3hldcpz4j2v"  # the v0.2 serving pod
#
#   bash scripts/train/serve_and_eval_v02.sh
#
# What it does:
#   1. Resume the RunPod pod (cold-start ~60-90s)
#   2. Wait for SSH/API readiness
#   3. Launch vLLM with the DPO adapter (Qwen2.5-14B, max-model-len 32768)
#   4. Health-probe the vLLM endpoint
#   5. Run the frozen 500-Q eval against it
#   6. Save results to data/training/aria_llm_v02_eval.json
#   7. Stop the pod (so you pay for minutes, not hours)
#
# Prerequisites:
#   - runpodctl installed (or curl for RunPod REST API)
#   - aria_service/ Python environment with dependencies
#   - ~/.ssh/runpod_aria key for SSH access
#
# Cost: ~$1.89/hr for A100 80GB. Realistic estimate: 60-90 min = ~$2-3
#   (14B weight load from volume is 5-10 min alone; 500 questions at ~5s/q
#   is ~42 min; pod resume + vLLM init + health probes add overhead).
#   The original $0.47 estimate was optimistic — this is the honest number.
#
# Instrument consistency (R-F1430): BOTH the v0.2 eval AND the fresh DeepSeek
# baseline use scripts/train/eval_aria_llm.py — the ONLY pod-runnable harness
# (self-contained raw httpx). Do NOT use eval_runner.run_eval() or
# llm_eval_framework.evaluate() here: both import the full FastAPI app +
# redis_store + all intel modules, which are NOT installed on a bare RunPod
# pod (the pod has torch/transformers/vLLM only). The old version of this
# script called eval_golden_seed.get_all() (does not exist) + llm_eval_framework
# (import bug + wrong provider path) and was guaranteed to crash AFTER the
# 45-min model load — wasting the whole paid cycle. Fixed in R-F1430.
# =============================================================================
set -euo pipefail

# R-F1431: resolve repo paths + auto-load the creds we need from the repo .env
# FIRST (before the required-var checks) so the operator does not have to export
# anything by hand — RUNPOD_API_KEY + DEEPSEEK_API_KEY already live in .env.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
if [ -f "$REPO_ROOT/.env" ]; then
  for _k in RUNPOD_API_KEY RUNPOD_POD_ID DEEPSEEK_API_KEY RUNPOD_API_BASE; do
    if [ -z "${!_k:-}" ]; then
      # R-F1451: trailing `|| true` — these vars are OPTIONAL in .env (e.g.
      # RUNPOD_POD_ID/RUNPOD_API_BASE have code defaults below). Without it, when
      # a var is absent grep exits 1, pipefail propagates it, and the command
      # substitution under `set -e` aborts the WHOLE script SILENTLY (no output,
      # exit 1) before any echo — the exact launch-blocker hit on the first run.
      _v=$(grep -E "^${_k}=" "$REPO_ROOT/.env" | head -1 | cut -d= -f2- | tr -d '"'"'"'' | tr -d '\r' || true)
      [ -n "$_v" ] && export "$_k=$_v"
    fi
  done
fi

# ── Config ──────────────────────────────────────────────────────────────────
# POD_ID defaults to the known v0.2 serving pod (7ei3hldcpz4j2v, per model_config.sh);
# override with RUNPOD_POD_ID if it was recreated.
POD_ID="${RUNPOD_POD_ID:-7ei3hldcpz4j2v}"
API_KEY="${RUNPOD_API_KEY:?RUNPOD_API_KEY not set (expected in .env or env)}"
# R-F1432: RunPod POD management is the REST v1 API (rest.runpod.io/v1) — NOT the
# /v2 serverless API the old default used (every /v2 pod call 404s). Verified live
# against this pod + matches aria_service/intel/runpod_scheduler.py:49.
API_BASE="${RUNPOD_API_BASE:-https://rest.runpod.io/v1}"
# R-F1432: this pod (aria-llm-serve, runpod/pytorch image) exposes ONLY 8888/http
# and 22/tcp — port 8000 is NOT exposed, so a -8000 proxy URL never resolves.
# Serve vLLM on the exposed http port 8888 and use its proxy URL.
VLLM_PROXY_PORT="${VLLM_PROXY_PORT:-8888}"

# R-F1430: ALWAYS stop the pod on EXIT (crash, error, OR success) so a failure
# can never leave the A100 billing open-ended. Pre-R-F1430 the script used
# `set -e` with NO trap, so any error before the final stop step left the pod
# running indefinitely (~$1.89/hr) — and step 5's eval was guaranteed to crash.
# This trap is the single biggest cost-safety fix.
_stop_pod() {
  echo "[trap] stopping pod $POD_ID to prevent runaway GPU billing..."
  curl -s -X POST "$API_BASE/pods/$POD_ID/stop" -H "Authorization: Bearer $API_KEY" >/dev/null 2>&1 || true
}
trap _stop_pod EXIT

# R-F1430: model identity from the SINGLE SOURCE OF TRUTH (model_config.sh) —
# scripts no longer disagree on base model / adapter / context window.
# (SCRIPT_DIR + REPO_ROOT already resolved at the top for the .env cred-load.)
# shellcheck source=scripts/train/model_config.sh
source "$SCRIPT_DIR/model_config.sh"
MODEL_NAME="$ARIA_MODEL_NAME"
MODEL_PATH="$ARIA_ADAPTER_PATH"
BASE_MODEL="$ARIA_BASE_MODEL"
MAX_MODEL_LEN="$ARIA_MAX_MODEL_LEN"

# R-F1431: step 5's eval runs LOCALLY (httpx → pod proxy URL), NOT on the pod.
# So it needs (a) a local Python that HAS httpx — the repo venv, not the Windows
# Store `python3` shim which has no deps and just opens the Store; and (b) a
# LOCAL eval-set file — the SSOT ARIA_EVAL_SET default is a /workspace POD path
# (right for on-pod training, wrong for the local harness). Resolve both here so
# the run does not crash AFTER the paid 14B load (the exact cycle-waster R-F1430
# fixed for the on-pod side). Override either via PYTHON_BIN / ARIA_LOCAL_EVAL_SET.
PYTHON_BIN="${PYTHON_BIN:-}"
if [ -z "$PYTHON_BIN" ]; then
  if [ -x "$REPO_ROOT/.venv/Scripts/python.exe" ]; then
    PYTHON_BIN="$REPO_ROOT/.venv/Scripts/python.exe"   # Windows venv
  elif [ -x "$REPO_ROOT/.venv/bin/python" ]; then
    PYTHON_BIN="$REPO_ROOT/.venv/bin/python"           # POSIX venv
  else
    PYTHON_BIN="python3"
  fi
fi
LOCAL_EVAL_SET="${ARIA_LOCAL_EVAL_SET:-$REPO_ROOT/data/eval_reports/aria_eval_500q.jsonl}"
echo "      Python:    $PYTHON_BIN"
echo "      Eval set:  $LOCAL_EVAL_SET (local)"

EVAL_OUTPUT="data/training/aria_llm_v02_eval.json"
TIMESTAMP=$(date -u +%Y%m%d_%H%M%S)
LOG_DIR="/tmp/aria_v02_serve_${TIMESTAMP}"
mkdir -p "$LOG_DIR"

echo "=== ARIA-LLM v0.2 serve + eval ==="
echo "Pod:       $POD_ID"
echo "Model:     $MODEL_NAME (DPO on $BASE_MODEL)"
echo "Max len:   $MAX_MODEL_LEN"
echo "Log dir:   $LOG_DIR"
echo "Output:    $EVAL_OUTPUT"
echo ""

# ── Step 1: Start pod ──────────────────────────────────────────────────────
# R-F1432: REST v1 uses /pods/{id}/start (NOT /resume) and returns the pod object
# at the TOP LEVEL (desiredStatus), NOT wrapped in {"pod": {...}}. Verified live.
echo "[1/6] Starting pod $POD_ID..."
curl -s -X POST "$API_BASE/pods/$POD_ID/start" \
  -H "Authorization: Bearer $API_KEY" > "$LOG_DIR/start.json" 2>&1

# R-F1452: SURFACE the start response + FAIL FAST on an error. Pre-R-F1452 the
# start.json (which carries RunPod's real reason, e.g. "There are not enough free
# GPUs on the host machine to start this pod") was written to disk but NEVER
# printed — so a capacity 500 looked like 30 silent "Status: EXITED" polls (5 min
# wasted) before the generic SSH-resolve abort. Now: echo the start error and, on
# a non-transient capacity/error response, abort immediately with the real cause
# (the EXIT trap stops the pod; cost ~$0 since it never ran).
# R-F1452: read start.json via STDIN (cat | python), NOT open('$LOG_DIR/...').
# $PYTHON_BIN is the Windows venv python.exe and $LOG_DIR is an MSYS /tmp path it
# cannot open — open() raised, the check was skipped, and the capacity 500 fell
# through to the silent poll. Piping via stdin matches every other python call in
# this script and is platform-agnostic.
START_ERR=$(cat "$LOG_DIR/start.json" | "$PYTHON_BIN" -c "import sys,json
try:
    d=json.load(sys.stdin)
except Exception:
    sys.exit(0)
if isinstance(d,dict) and (d.get('error') or (isinstance(d.get('status'),int) and d['status']>=400)):
    print(d.get('error') or d.get('status'))
" 2>/dev/null || echo "")
if [ -n "$START_ERR" ]; then
  echo "ERROR: RunPod refused to start pod $POD_ID:" >&2
  echo "       $START_ERR" >&2
  case "$START_ERR" in
    *"free GPUs"*|*"not enough"*|*"capacity"*|*"unavailable"*)
      echo "       → GPU CAPACITY blocker (this pod is pinned to a host with no free GPU)." >&2
      echo "       → Not a code bug. Retry when capacity frees up, or recreate the pod on a host with availability (the adapter lives on the network volume, so a new pod attaching it works)." >&2
      ;;
  esac
  echo "Aborting fast (trap stops the pod; it never ran, cost ~\$0)." >&2
  exit 1
fi

# Wait for pod to reach RUNNING state (and for runtime/IP to populate)
echo "      Waiting for pod to start..."
POD_DATA=""
for i in $(seq 1 30); do
  POD_DATA=$(curl -s "$API_BASE/pods/$POD_ID" -H "Authorization: Bearer $API_KEY")
  STATUS=$(echo "$POD_DATA" | "$PYTHON_BIN" -c "import sys,json; print(json.load(sys.stdin).get('desiredStatus','') or '')" 2>/dev/null || echo "unknown")
  IP_READY=$(echo "$POD_DATA" | "$PYTHON_BIN" -c "import sys,json; print('1' if json.load(sys.stdin).get('publicIp') else '')" 2>/dev/null || echo "")
  if [ "$STATUS" = "RUNNING" ] && [ -n "$IP_READY" ]; then
    echo "      Pod is RUNNING with IP (attempt $i)"
    break
  fi
  echo "      Status: ${STATUS:-?} ip_ready=${IP_READY:-0} — waiting 10s (attempt $i/30)..."
  sleep 10
done

# R-F1432: SSH endpoint from REST v1 shape — publicIp + portMappings["22"]
# (the old runtime.host/.port path does not exist in REST v1). Fail LOUD with the
# raw pod JSON if unresolved so the shape is instantly fixable (trap stops the pod).
read -r POD_HOST POD_PORT < <(echo "$POD_DATA" | "$PYTHON_BIN" -c "
import sys,json
d=json.load(sys.stdin)
ip=d.get('publicIp') or ''
pm=d.get('portMappings') or {}
port=str(pm.get('22') or pm.get(22) or '') if isinstance(pm,dict) else ''
print(ip, port)
" 2>/dev/null || echo " ")
# R-F1453: the local $PYTHON_BIN is the Windows venv python.exe, which emits CRLF
# (\r\n). `read` keeps the trailing \r on the LAST field, so POD_PORT becomes
# "18599\r" → `ssh -p 18599\r` fails with "Bad port '18599'" (the \r is invisible)
# and the whole serve step dies after the SSH loop silently 20x-retries. Strip CR
# from both fields. (The $()-captured STATUS/IP_READY above tolerate it.)
POD_HOST="${POD_HOST//$'\r'/}"
POD_PORT="${POD_PORT//$'\r'/}"
echo "      SSH endpoint: ${POD_HOST:-<none>}:${POD_PORT:-<none>}"
if [ -z "$POD_HOST" ] || [ -z "$POD_PORT" ]; then
  echo "ERROR: could not resolve SSH host/port from the running pod. Raw pod JSON:" >&2
  echo "$POD_DATA" | "$PYTHON_BIN" -c "import sys,json;d=json.load(sys.stdin);print(json.dumps({k:d.get(k) for k in ('desiredStatus','publicIp','portMappings','ports')}, indent=2))" >&2 2>/dev/null || echo "$POD_DATA" | head -c 800 >&2
  echo "Aborting (trap stops the pod). Fix the host/port parse to match the shape above." >&2
  exit 1
fi

# R-F1453: resolve the SSH identity the pod accepts and copy it to a perm-safe
# (chmod 600) temp. Two reasons the bare `ssh root@host` calls below failed before:
# (1) no `-i` flag → ssh never tries ~/.ssh/runpod_aria (not a default key name,
# not in agent/config), so it falls through to publickey-denied; (2) on Windows the
# key is checked out 644 and ssh can reject it as "UNPROTECTED PRIVATE KEY FILE".
# Copy to $LOG_DIR/sshid @ 600 and pass it explicitly via $SSH_ID to every ssh call.
# Override the source key with ARIA_RUNPOD_SSH_KEY.
SSH_KEY_SRC="${ARIA_RUNPOD_SSH_KEY:-$HOME/.ssh/runpod_aria}"
SSH_ID="$LOG_DIR/sshid"
if [ -f "$SSH_KEY_SRC" ]; then
  cp "$SSH_KEY_SRC" "$SSH_ID" && chmod 600 "$SSH_ID"
else
  echo "ERROR: SSH key not found at $SSH_KEY_SRC (set ARIA_RUNPOD_SSH_KEY). Aborting (trap stops pod)." >&2
  exit 1
fi

# ── Step 2: Wait for SSH readiness ─────────────────────────────────────────
echo "[2/6] Waiting for SSH readiness..."
for i in $(seq 1 20); do
  if ssh -i "$SSH_ID" -o StrictHostKeyChecking=no -o ConnectTimeout=5 -p "$POD_PORT" "root@$POD_HOST" "echo ready" 2>/dev/null; then
    echo "      SSH ready (attempt $i)"
    break
  fi
  echo "      Waiting 10s..."
  sleep 10
done

# ── Step 3a: VERIFY adapter base matches BASE_MODEL (the #1 cycle-waster) ────
# R-F1431: automate the manual "verify adapter_config.json" step. A LoRA adapter
# trained on one base CANNOT load on another — a silent mismatch wastes the whole
# paid 14B load. Read the adapter's recorded base ON THE POD and abort LOUD if it
# disagrees with the configured BASE_MODEL (the EXIT trap stops the pod).
echo "[3a] Verifying adapter base matches $BASE_MODEL..."
ADAPTER_BASE=$(ssh -i "$SSH_ID" -o StrictHostKeyChecking=no -p "$POD_PORT" "root@$POD_HOST" \
  "python3 -c \"import json;print(json.load(open('$MODEL_PATH/adapter_config.json')).get('base_model_name_or_path',''))\"" 2>/dev/null || echo "")
echo "      adapter base: '${ADAPTER_BASE:-<unreadable>}' (expected: $BASE_MODEL)"
if [ -n "$ADAPTER_BASE" ] && [ "$ADAPTER_BASE" != "$BASE_MODEL" ]; then
  echo "ERROR: adapter base ('$ADAPTER_BASE') != configured BASE_MODEL ('$BASE_MODEL')." >&2
  echo "       Re-run with:  export ARIA_BASE_MODEL='$ADAPTER_BASE'  (and fix --model in the ssh vLLM block if needed)." >&2
  echo "       Aborting before the paid model load (trap stops the pod)." >&2
  exit 1
fi

# ── Step 3: Launch vLLM ────────────────────────────────────────────────────
# R-F1432: pass the SSOT-verified model identity as positional args (the heredoc
# is single-quoted → no local expansion) so the launch uses the SAME base/adapter
# the adapter-base check (step 3a) just verified — not a hardcoded copy that can
# drift. Serve on the EXPOSED http port (8888); free Jupyter which owns it on the
# runpod/pytorch image.
echo "[3/6] Launching vLLM with $MODEL_NAME on port $VLLM_PROXY_PORT..."
ssh -i "$SSH_ID" -o StrictHostKeyChecking=no -p "$POD_PORT" "root@$POD_HOST" bash -s -- \
  "$BASE_MODEL" "$MODEL_PATH" "$MODEL_NAME" "$MAX_MODEL_LEN" "$VLLM_PROXY_PORT" << 'REMOTE'
  set -euo pipefail
  BASE_MODEL="$1"; ADAPTER_PATH="$2"; SERVED_NAME="$3"; MAXLEN="$4"; PORT="$5"
  mkdir -p /workspace/logs

  # R-F1454: load the base model from the PERSISTENT volume HF cache. The
  # container default ~/.cache/huggingface is ephemeral and wiped on every pod
  # restart, so the base (mistralai/Mistral-7B-Instruct-v0.3 — GATED on HF, and
  # there is NO HF token on the pod) is not there and a fresh download would fail.
  # The full weights ARE cached on the volume from the training run. Point HF_HOME
  # at it and force offline so vLLM resolves from cache instead of hitting the hub.
  # R-F4350 (C-295) — ONE definition of which disk holds the HF cache.
  # This used to point the cache at /workspace, a 20G volume whose own comment
  # mis-named it the container disk; see hf_cache_select.sh for the measurement.
  _hfsel=""
  for _d in "$(dirname "${BASH_SOURCE[0]:-$0}")" /workspace/crucix/scripts/train /workspace; do
    [ -f "$_d/hf_cache_select.sh" ] && { _hfsel="$_d/hf_cache_select.sh"; break; }
  done
  [ -n "$_hfsel" ] || { echo "[FATAL] hf_cache_select.sh not found — refusing to guess a cache disk." >&2; exit 1; }
  . "$_hfsel"
  hf_cache_select || exit 1
  export HF_HUB_OFFLINE=1
  export TRANSFORMERS_OFFLINE=1

  # Install vLLM if not present
  pip install -q vllm 2>&1 | tail -1

  # Free the exposed http port (RunPod pytorch image runs Jupyter on 8888) and
  # any prior vLLM, then start vLLM with the DPO adapter on that port.
  pkill -f jupyter 2>/dev/null || true
  pkill -f vllm.entrypoints.openai 2>/dev/null || true
  sleep 2

  nohup python -m vllm.entrypoints.openai.api_server \
    --model "$BASE_MODEL" \
    --enable-lora \
    --lora-modules "${SERVED_NAME}=${ADAPTER_PATH}" \
    --max-loras 1 \
    --max-lora-rank 64 \
    --max-model-len "$MAXLEN" \
    --gpu-memory-utilization 0.9 \
    --host 0.0.0.0 --port "$PORT" \
    > /workspace/logs/vllm_serve_v02.log 2>&1 &

  echo "vLLM starting on port $PORT — PID: $!"
REMOTE

# Wait for vLLM to load model (~45s for 14B)
# ── Step 4: Health probe ───────────────────────────────────────────────────
# R-F1430: poll for the FULL 14B load window (~5-10 min from the volume), not a
# fixed 45s sleep + 120s probe — the old wait proceeded to eval a NOT-ready
# endpoint, producing an all-error eval on a paid cycle. Up to ~12 min; if it
# never comes ready, abort LOUD (the EXIT trap stops the pod).
echo "[4/6] Probing vLLM endpoint (14B load can take 5-10 min)..."
# R-F1432: proxy the EXPOSED http port (8888), not 8000 (not exposed on this pod).
PROXY_URL="https://${POD_ID}-${VLLM_PROXY_PORT}.proxy.runpod.net"
VLLM_READY=0
for i in $(seq 1 48); do
  HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" "$PROXY_URL/v1/models" 2>/dev/null || echo "000")
  if [ "$HTTP_CODE" = "200" ]; then
    echo "      vLLM ready at $PROXY_URL (attempt $i)"
    MODELS=$(curl -s "$PROXY_URL/v1/models" | "$PYTHON_BIN" -m json.tool 2>/dev/null || echo "")
    echo "      Models: $MODELS"
    VLLM_READY=1
    break
  fi
  echo "      HTTP $HTTP_CODE — model still loading, waiting 15s (attempt $i/48)..."
  sleep 15
done
if [ "$VLLM_READY" != "1" ]; then
  echo "ERROR: vLLM did not become ready after ~12 min — aborting (trap stops the pod)." >&2
  exit 1
fi

# ── Step 5: Run the eval — SAME instrument for v0.2 AND the DeepSeek baseline ─
# R-F1430: the old step called eval_golden_seed.get_all() (does NOT exist →
# AttributeError AFTER the 45-min load) then llm_eval_framework (LLMPipeline
# import bug + AriaLLMProvider wrong path → every Q returns [ERROR]). NEITHER
# runs on a bare pod (both import the full FastAPI app). The pod-correct,
# self-contained instrument is scripts/train/eval_aria_llm.py (raw httpx). Run
# it for BOTH the v0.2 endpoint AND the DeepSeek baseline with the SAME eval
# set so the comparison is apples-to-apples (the promotion bar depends on it).
echo "[5/6] Running eval (same instrument: v0.2 + DeepSeek baseline)..."
mkdir -p "$REPO_ROOT/data/eval_reports"

# R-F1431: ensure the LOCAL eval-set exists (auto-export the frozen 500-Q if not).
if [ ! -f "$LOCAL_EVAL_SET" ]; then
  echo "      Exporting frozen 500-Q eval set → $LOCAL_EVAL_SET"
  "$PYTHON_BIN" "$SCRIPT_DIR/export_eval_500q.py" --out "$LOCAL_EVAL_SET"
fi

echo "      → v0.2 ($MODEL_NAME) at $PROXY_URL/v1"
"$PYTHON_BIN" "$SCRIPT_DIR/eval_aria_llm.py" \
  --target "$PROXY_URL/v1" \
  --model "$MODEL_NAME" \
  --eval-set "$LOCAL_EVAL_SET" \
  --out "$REPO_ROOT/data/eval_reports/aria_llm_v02_eval.json" 2>&1 | tee "$LOG_DIR/eval_v02.log"

if [ -n "${DEEPSEEK_API_KEY:-}" ]; then
  echo "      → DeepSeek baseline (deepseek-chat) at https://api.deepseek.com/v1"
  "$PYTHON_BIN" "$SCRIPT_DIR/eval_aria_llm.py" \
    --target "https://api.deepseek.com/v1" \
    --model "deepseek-chat" \
    --api-key "$DEEPSEEK_API_KEY" \
    --eval-set "$LOCAL_EVAL_SET" \
    --out "$REPO_ROOT/data/eval_reports/deepseek_baseline_eval.json" 2>&1 | tee "$LOG_DIR/eval_deepseek.log"
else
  echo "      ⚠ DEEPSEEK_API_KEY not set — skipping DeepSeek baseline. Set it for the apples-to-apples comparison the promotion bar needs."
fi

echo "      Eval complete. Reports in data/eval_reports/ (v0.2 + DeepSeek)."

# ── Step 6: Stop the pod ───────────────────────────────────────────────────
echo "[6/6] Stopping pod $POD_ID..."
curl -s -X POST "$API_BASE/pods/$POD_ID/stop" \
  -H "Authorization: Bearer $API_KEY" > "$LOG_DIR/stop.json" 2>&1
echo "      Pod stop requested."

echo ""
echo "=== Done ==="
echo "Eval reports: data/eval_reports/aria_llm_v02_eval.json + deepseek_baseline_eval.json"
echo "Logs:         $LOG_DIR"
echo "Total cost:   ~\$2-3 (60-90 min A100 80GB at ~\$1.89/hr); pod stopped by EXIT trap."
