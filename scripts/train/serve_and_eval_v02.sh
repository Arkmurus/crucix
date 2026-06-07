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

# ── Config ──────────────────────────────────────────────────────────────────
POD_ID="${RUNPOD_POD_ID:?RUNPOD_POD_ID not set}"
API_KEY="${RUNPOD_API_KEY:?RUNPOD_API_KEY not set}"
API_BASE="${RUNPOD_API_BASE:-https://api.runpod.io/v2}"

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
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/train/model_config.sh
source "$SCRIPT_DIR/model_config.sh"
MODEL_NAME="$ARIA_MODEL_NAME"
MODEL_PATH="$ARIA_ADAPTER_PATH"
BASE_MODEL="$ARIA_BASE_MODEL"
MAX_MODEL_LEN="$ARIA_MAX_MODEL_LEN"
VLLM_PORT=8000

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

# ── Step 1: Resume pod ─────────────────────────────────────────────────────
echo "[1/6] Resuming pod $POD_ID..."
curl -s -X POST "$API_BASE/pods/$POD_ID/resume" \
  -H "Authorization: Bearer $API_KEY" > "$LOG_DIR/resume.json" 2>&1

# Wait for pod to reach RUNNING state
echo "      Waiting for pod to start..."
for i in $(seq 1 30); do
  STATUS=$(curl -s "$API_BASE/pods/$POD_ID" \
    -H "Authorization: Bearer $API_KEY" | python3 -c "import sys,json; print(json.load(sys.stdin).get('pod',{}).get('desiredStatus',''))" 2>/dev/null || echo "unknown")
  if [ "$STATUS" = "RUNNING" ]; then
    echo "      Pod is RUNNING (attempt $i)"
    break
  fi
  echo "      Status: $STATUS — waiting 10s..."
  sleep 10
done

# Get pod host/port for SSH
POD_DATA=$(curl -s "$API_BASE/pods/$POD_ID" -H "Authorization: Bearer $API_KEY")
POD_HOST=$(echo "$POD_DATA" | python3 -c "import sys,json; d=json.load(sys.stdin).get('pod',{}); print(d.get('runtime',{}).get('host',''))" 2>/dev/null || echo "")
POD_PORT=$(echo "$POD_DATA" | python3 -c "import sys,json; d=json.load(sys.stdin).get('pod',{}); print(d.get('runtime',{}).get('port',''))" 2>/dev/null || echo "22")
echo "      Host: $POD_HOST:$POD_PORT"

# ── Step 2: Wait for SSH readiness ─────────────────────────────────────────
echo "[2/6] Waiting for SSH readiness..."
for i in $(seq 1 20); do
  if ssh -o StrictHostKeyChecking=no -o ConnectTimeout=5 -p "$POD_PORT" "root@$POD_HOST" "echo ready" 2>/dev/null; then
    echo "      SSH ready (attempt $i)"
    break
  fi
  echo "      Waiting 10s..."
  sleep 10
done

# ── Step 3: Launch vLLM ────────────────────────────────────────────────────
echo "[3/6] Launching vLLM with $MODEL_NAME..."
ssh -p "$POD_PORT" "root@$POD_HOST" bash -s << 'REMOTE'
  set -euo pipefail
  mkdir -p /workspace/logs

  # Install vLLM if not present
  pip install -q vllm 2>&1 | tail -1

  # Kill any existing vLLM process
  pkill -f vllm.entrypoints.openai 2>/dev/null || true
  sleep 2

  # Start vLLM with DPO adapter
  nohup python -m vllm.entrypoints.openai.api_server \
    --model Qwen/Qwen2.5-14B-Instruct \
    --enable-lora \
    --lora-modules aria-llm-v0.2=/workspace/checkpoints/aria_llm_v0_2_dpo \
    --max-loras 1 \
    --max-lora-rank 64 \
    --max-model-len 32768 \
    --gpu-memory-utilization 0.9 \
    --host 0.0.0.0 --port 8000 \
    > /workspace/logs/vllm_serve_v02.log 2>&1 &

  echo "vLLM starting — PID: $!"
REMOTE

# Wait for vLLM to load model (~45s for 14B)
# ── Step 4: Health probe ───────────────────────────────────────────────────
# R-F1430: poll for the FULL 14B load window (~5-10 min from the volume), not a
# fixed 45s sleep + 120s probe — the old wait proceeded to eval a NOT-ready
# endpoint, producing an all-error eval on a paid cycle. Up to ~12 min; if it
# never comes ready, abort LOUD (the EXIT trap stops the pod).
echo "[4/6] Probing vLLM endpoint (14B load can take 5-10 min)..."
PROXY_URL="https://${POD_ID}-8000.proxy.runpod.net"
VLLM_READY=0
for i in $(seq 1 48); do
  HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" "$PROXY_URL/v1/models" 2>/dev/null || echo "000")
  if [ "$HTTP_CODE" = "200" ]; then
    echo "      vLLM ready at $PROXY_URL (attempt $i)"
    MODELS=$(curl -s "$PROXY_URL/v1/models" | python3 -m json.tool 2>/dev/null || echo "")
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
mkdir -p data/eval_reports

echo "      → v0.2 ($MODEL_NAME) at $PROXY_URL/v1"
python3 "$SCRIPT_DIR/eval_aria_llm.py" \
  --target "$PROXY_URL/v1" \
  --model "$MODEL_NAME" \
  --eval-set "$ARIA_EVAL_SET" \
  --out "data/eval_reports/aria_llm_v02_eval.json" 2>&1 | tee "$LOG_DIR/eval_v02.log"

if [ -n "${DEEPSEEK_API_KEY:-}" ]; then
  echo "      → DeepSeek baseline (deepseek-chat) at https://api.deepseek.com/v1"
  python3 "$SCRIPT_DIR/eval_aria_llm.py" \
    --target "https://api.deepseek.com/v1" \
    --model "deepseek-chat" \
    --api-key "$DEEPSEEK_API_KEY" \
    --eval-set "$ARIA_EVAL_SET" \
    --out "data/eval_reports/deepseek_baseline_eval.json" 2>&1 | tee "$LOG_DIR/eval_deepseek.log"
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
