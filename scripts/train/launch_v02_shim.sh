#!/usr/bin/env bash
# =============================================================================
# ARIA-LLM v0.2 — shim-based serve + eval (replaces vLLM which needs newer CUDA)
#
# The pod's CUDA driver (565) is too old for latest vLLM's torch. Instead of
# `pip install vllm`, this uses serve_eval_shim.py (transformers+peft+bnb) which
# works with the pod's existing torch 2.4.1+cu124.
#
# Usage:
#   export RUNPOD_API_KEY="..."        # from .env
#   export RUNPOD_POD_ID="7ei3hldcpz4j2v"
#   bash scripts/train/launch_v02_shim.sh
#
# Steps:
#   1. Start pod (retry until GPU free)
#   2. SSH in, install shim deps (transformers peft bitsandbytes uvicorn fastapi)
#   3. Launch serve_eval_shim.py on port 8888
#   4. Health-probe the shim endpoint
#   5. Run 500-Q eval on v0.2 + DeepSeek baseline
#   6. Stop the pod (EXIT trap)
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

# ── Creds from .env ──────────────────────────────────────────────────────────
if [ -f "$REPO_ROOT/.env" ]; then
  for _k in RUNPOD_API_KEY RUNPOD_POD_ID DEEPSEEK_API_KEY RUNPOD_API_BASE; do
    if [ -z "${!_k:-}" ]; then
      _v=$(grep -E "^${_k}=" "$REPO_ROOT/.env" | head -1 | cut -d= -f2- | tr -d '"'"'"'' | tr -d '\r' || true)
      [ -n "$_v" ] && export "$_k=$_v"
    fi
  done
fi

POD_ID="${RUNPOD_POD_ID:-7ei3hldcpz4j2v}"
API_KEY="${RUNPOD_API_KEY:?RUNPOD_API_KEY not set}"
API_BASE="${RUNPOD_API_BASE:-https://rest.runpod.io/v1}"
VLLM_PROXY_PORT="${VLLM_PROXY_PORT:-8888}"

# ── Model config (SSOT corrected R-F1454) ────────────────────────────────────
source "$SCRIPT_DIR/model_config.sh"
BASE_MODEL="${ARIA_BASE_MODEL:-mistralai/Mistral-7B-Instruct-v0.3}"
ADAPTER_PATH="${ARIA_ADAPTER_PATH:-/workspace/checkpoints/aria_llm_v0_2_dpo}"
MODEL_NAME="${ARIA_MODEL_NAME:-aria-llm-v0.2}"

# ── Paths ────────────────────────────────────────────────────────────────────
LOCAL_EVAL_SET="${ARIA_LOCAL_EVAL_SET:-$REPO_ROOT/data/eval_reports/aria_eval_500q.jsonl}"
EVAL_OUTPUT_V02="$REPO_ROOT/data/eval_reports/aria_llm_v02_eval.json"
EVAL_OUTPUT_DS="$REPO_ROOT/data/eval_reports/deepseek_baseline_eval.json"
TIMESTAMP=$(date -u +%Y%m%d_%H%M%S)
LOG_DIR="/tmp/aria_v02_shim_${TIMESTAMP}"
mkdir -p "$LOG_DIR"

# ── Python binary (local, for eval) ──────────────────────────────────────────
PYTHON_BIN="${PYTHON_BIN:-}"
if [ -z "$PYTHON_BIN" ]; then
  if [ -x "$REPO_ROOT/.venv/Scripts/python.exe" ]; then
    PYTHON_BIN="$REPO_ROOT/.venv/Scripts/python.exe"
  elif [ -x "$REPO_ROOT/.venv/bin/python" ]; then
    PYTHON_BIN="$REPO_ROOT/.venv/bin/python"
  else
    PYTHON_BIN="python3"
  fi
fi

echo "=== ARIA-LLM v0.2 shim serve + eval ==="
echo "Pod:       $POD_ID"
echo "Base:      $BASE_MODEL"
echo "Adapter:   $ADAPTER_PATH"
echo "Model:     $MODEL_NAME"
echo "Eval set:  $LOCAL_EVAL_SET"
echo "Log dir:   $LOG_DIR"
echo ""

# ── EXIT trap: always stop the pod ──────────────────────────────────────────
_stop_pod() {
  echo "[trap] stopping pod $POD_ID..."
  curl -s -X POST "$API_BASE/pods/$POD_ID/stop" \
    -H "Authorization: Bearer $API_KEY" >/dev/null 2>&1 || true
}
trap _stop_pod EXIT

# ── Step 1: Start pod (retry until GPU free) ────────────────────────────────
echo "[1/6] Starting pod $POD_ID (retrying until GPU free)..."
for i in $(seq 1 30); do
  curl -s -X POST "$API_BASE/pods/$POD_ID/start" \
    -H "Authorization: Bearer $API_KEY" > "$LOG_DIR/start.json" 2>&1
  START_ERR=$(cat "$LOG_DIR/start.json" | "$PYTHON_BIN" -c "import sys,json
try:
    d=json.load(sys.stdin)
except Exception:
    sys.exit(0)
if isinstance(d,dict) and (d.get('error') or (isinstance(d.get('status'),int) and d['status']>=400)):
    print(d.get('error') or d.get('status'))
" 2>/dev/null || echo "")
  if [ -z "$START_ERR" ]; then
    echo "      Pod started (attempt $i)"
    break
  fi
  echo "      $START_ERR — retrying in 60s (attempt $i/30)..."
  sleep 60
done
if [ -n "${START_ERR:-}" ]; then
  echo "ERROR: Could not start pod after 30 attempts." >&2
  exit 1
fi

# Wait for pod to reach RUNNING state
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
  echo "      Status: ${STATUS:-?} — waiting 10s (attempt $i/30)..."
  sleep 10
done

# ── Resolve SSH endpoint ────────────────────────────────────────────────────
read -r POD_HOST POD_PORT < <(echo "$POD_DATA" | "$PYTHON_BIN" -c "
import sys,json
d=json.load(sys.stdin)
ip=d.get('publicIp') or ''
pm=d.get('portMappings') or {}
port=str(pm.get('22') or pm.get(22) or '') if isinstance(pm,dict) else ''
print(ip, port)
" 2>/dev/null || echo " ")
POD_HOST="${POD_HOST//$'\r'/}"
POD_PORT="${POD_PORT//$'\r'/}"
echo "      SSH endpoint: ${POD_HOST:-<none>}:${POD_PORT:-<none>}"
if [ -z "$POD_HOST" ] || [ -z "$POD_PORT" ]; then
  echo "ERROR: could not resolve SSH host/port." >&2
  exit 1
fi

# ── SSH key ──────────────────────────────────────────────────────────────────
SSH_KEY_SRC="${ARIA_RUNPOD_SSH_KEY:-$HOME/.ssh/runpod_aria}"
SSH_ID="$LOG_DIR/sshid"
if [ -f "$SSH_KEY_SRC" ]; then
  cp "$SSH_KEY_SRC" "$SSH_ID" && chmod 600 "$SSH_ID"
else
  echo "ERROR: SSH key not found at $SSH_KEY_SRC" >&2
  exit 1
fi

# ── Step 2: Wait for SSH readiness ──────────────────────────────────────────
echo "[2/6] Waiting for SSH readiness..."
for i in $(seq 1 20); do
  if ssh -i "$SSH_ID" -o StrictHostKeyChecking=no -o ConnectTimeout=5 -p "$POD_PORT" "root@$POD_HOST" "echo ready" 2>/dev/null; then
    echo "      SSH ready (attempt $i)"
    break
  fi
  echo "      Waiting 10s..."
  sleep 10
done

# ── Step 3a: Verify adapter base ────────────────────────────────────────────
echo "[3a] Verifying adapter base matches $BASE_MODEL..."
ADAPTER_BASE=$(ssh -i "$SSH_ID" -o StrictHostKeyChecking=no -p "$POD_PORT" "root@$POD_HOST" \
  "python3 -c \"import json;print(json.load(open('$ADAPTER_PATH/adapter_config.json')).get('base_model_name_or_path',''))\"" 2>/dev/null || echo "")
echo "      adapter base: '${ADAPTER_BASE:-<unreadable>}' (expected: $BASE_MODEL)"
if [ -n "$ADAPTER_BASE" ] && [ "$ADAPTER_BASE" != "$BASE_MODEL" ]; then
  echo "ERROR: adapter base ('$ADAPTER_BASE') != configured BASE_MODEL ('$BASE_MODEL')." >&2
  exit 1
fi

# ── Step 3: Install shim deps + launch serve_eval_shim.py ───────────────────
echo "[3/6] Installing shim dependencies on pod..."
ssh -i "$SSH_ID" -o StrictHostKeyChecking=no -p "$POD_PORT" "root@$POD_HOST" bash -s -- \
  "$BASE_MODEL" "$ADAPTER_PATH" "$MODEL_NAME" "$VLLM_PROXY_PORT" << 'REMOTE'
  set -euo pipefail
  BASE_MODEL="$1"; ADAPTER_PATH="$2"; SERVED_NAME="$3"; PORT="$4"
  mkdir -p /workspace/logs

  # R-F1454: load from volume HF cache (gated model, no HF token on pod)
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

  # Install shim deps (compatible with pod's CUDA 12.4 / driver 565)
  pip install -q transformers peft bitsandbytes uvicorn fastapi pydantic 2>&1 | tail -3

  # Free port 8888 (Jupyter runs on it in runpod/pytorch image)
  pkill -f jupyter 2>/dev/null || true
  pkill -f serve_eval_shim 2>/dev/null || true
  sleep 2

  # Launch shim in background
  nohup env BASE_MODEL="$BASE_MODEL" ADAPTER="$ADAPTER_PATH" \
    MODEL_NAME="$SERVED_NAME" PORT="$PORT" \
    python3 /workspace/crucix/scripts/train/serve_eval_shim.py \
    > /workspace/logs/shim_v02.log 2>&1 &
  echo "Shim starting on port $PORT — PID: $!"
REMOTE

# ── Step 4: Health probe ────────────────────────────────────────────────────
echo "[4/6] Probing shim endpoint..."
PROXY_URL="https://${POD_ID}-${VLLM_PROXY_PORT}.proxy.runpod.net"
SHIM_READY=0
for i in $(seq 1 48); do
  HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" "$PROXY_URL/v1/models" 2>/dev/null || echo "000")
  if [ "$HTTP_CODE" = "200" ]; then
    echo "      Shim ready at $PROXY_URL (attempt $i)"
    MODELS=$(curl -s "$PROXY_URL/v1/models" | "$PYTHON_BIN" -m json.tool 2>/dev/null || echo "")
    echo "      Models: $MODELS"
    SHIM_READY=1
    break
  fi
  echo "      HTTP $HTTP_CODE — model still loading, waiting 15s (attempt $i/48)..."
  sleep 15
done
if [ "$SHIM_READY" != "1" ]; then
  echo "ERROR: Shim did not become ready after ~12 min." >&2
  exit 1
fi

# ── Step 5: Run eval ────────────────────────────────────────────────────────
echo "[5/6] Running eval (v0.2 + DeepSeek baseline)..."
mkdir -p "$REPO_ROOT/data/eval_reports"

if [ ! -f "$LOCAL_EVAL_SET" ]; then
  echo "      Exporting frozen 500-Q eval set..."
  "$PYTHON_BIN" "$SCRIPT_DIR/export_eval_500q.py" --out "$LOCAL_EVAL_SET"
fi

echo "      → v0.2 ($MODEL_NAME) at $PROXY_URL/v1"
"$PYTHON_BIN" "$SCRIPT_DIR/eval_aria_llm.py" \
  --target "$PROXY_URL/v1" \
  --model "$MODEL_NAME" \
  --eval-set "$LOCAL_EVAL_SET" \
  --out "$EVAL_OUTPUT_V02" 2>&1 | tee "$LOG_DIR/eval_v02.log"

if [ -n "${DEEPSEEK_API_KEY:-}" ]; then
  echo "      → DeepSeek baseline (deepseek-chat) at https://api.deepseek.com/v1"
  "$PYTHON_BIN" "$SCRIPT_DIR/eval_aria_llm.py" \
    --target "https://api.deepseek.com/v1" \
    --model "deepseek-chat" \
    --api-key "$DEEPSEEK_API_KEY" \
    --eval-set "$LOCAL_EVAL_SET" \
    --out "$EVAL_OUTPUT_DS" 2>&1 | tee "$LOG_DIR/eval_deepseek.log"
else
  echo "      ⚠ DEEPSEEK_API_KEY not set — skipping DeepSeek baseline."
fi

echo "      Eval complete."
echo "      v0.2:       $EVAL_OUTPUT_V02"
echo "      DeepSeek:   $EVAL_OUTPUT_DS"

# ── Step 6: Stop pod (EXIT trap handles this) ───────────────────────────────
echo "[6/6] Pod will be stopped by EXIT trap."
echo ""
echo "=== Done ==="
