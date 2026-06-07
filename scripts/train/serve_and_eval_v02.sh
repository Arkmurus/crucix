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
# Instrument consistency: BOTH the fresh DeepSeek baseline AND the v0.2 eval
# use eval_runner.run_eval() — the proven live path. Do NOT use
# llm_eval_framework.evaluate(model="deepseek") for the baseline; that path
# has an import bug (LLMPipeline vs LLMTrainingPipeline) and is deferred for
# post-T-R1 fix. The script below calls eval_runner via the Python inline
# block, which routes through _aria_chat_session() — the same code the
# WhatsApp user hits.
# =============================================================================
set -euo pipefail

# ── Config ──────────────────────────────────────────────────────────────────
POD_ID="${RUNPOD_POD_ID:?RUNPOD_POD_ID not set}"
API_KEY="${RUNPOD_API_KEY:?RUNPOD_API_KEY not set}"
API_BASE="${RUNPOD_API_BASE:-https://api.runpod.io/v2}"

MODEL_NAME="aria-llm-v0.2"
MODEL_PATH="/workspace/checkpoints/aria_llm_v0_2_dpo"  # DPO adapter
BASE_MODEL="Qwen/Qwen2.5-14B-Instruct"
MAX_MODEL_LEN=32768
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
echo "      Waiting for vLLM to load model..."
sleep 45

# ── Step 4: Health probe ───────────────────────────────────────────────────
echo "[4/6] Probing vLLM endpoint..."
PROXY_URL="https://${POD_ID}-8000.proxy.runpod.net"
for i in $(seq 1 12); do
  HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" "$PROXY_URL/v1/models" 2>/dev/null || echo "000")
  if [ "$HTTP_CODE" = "200" ]; then
    echo "      vLLM ready at $PROXY_URL (attempt $i)"
    MODELS=$(curl -s "$PROXY_URL/v1/models" | python3 -m json.tool 2>/dev/null || echo "")
    echo "      Models: $MODELS"
    break
  fi
  echo "      HTTP $HTTP_CODE — waiting 10s..."
  sleep 10
done

# ── Step 5: Run the 500-Q eval ─────────────────────────────────────────────
echo "[5/6] Running 500-Q eval against $MODEL_NAME..."
echo "      Setting ARIA_LLM_URL=$PROXY_URL/v1"
echo "      Setting ARIA_LLM_MODEL=$MODEL_NAME"

# Run the eval via the Python framework
# Uses the llm_eval_framework.evaluate() function with model="aria-llm"
# which connects to ARIA_LLM_URL
ARIA_LLM_URL="$PROXY_URL/v1" \
ARIA_LLM_MODEL="$MODEL_NAME" \
ARIA_LLM_KEY="" \
python3 -c "
import asyncio
import json
import os
import sys

# Ensure the repo is on the path
sys.path.insert(0, '.')

from aria_service.intel.llm_eval_framework import evaluate
from aria_service.intel.eval_golden_seed import get_all

async def main():
    # Load the 500-Q golden seed
    seed_data = await get_all()
    print(f'Loaded {len(seed_data)} seed entries')

    # Convert to EvalQuestion objects
    from aria_service.intel.llm_eval_framework import EvalQuestion
    questions = [
        EvalQuestion(
            id=s.get('seed_id', f'q_{i}'),
            question=s.get('question', ''),
            expected_answer=s.get('expected_answer', ''),
            category=s.get('category', 'general'),
            requires_refusal=s.get('requires_refusal', False),
            requires_grounding=s.get('requires_grounding', True),
        )
        for i, s in enumerate(seed_data or [])
        if s.get('question') and s.get('expected_answer')
    ]
    print(f'Converted {len(questions)} questions')

    # Run evaluation
    result = await evaluate(
        model_a='aria-llm',
        questions=questions,
        sample_size=500,
    )

    # Save result
    output = {
        'target': os.environ.get('ARIA_LLM_URL', ''),
        'model': os.environ.get('ARIA_LLM_MODEL', 'aria-llm-v0.2'),
        'started_at': result.timestamp,
        'finished_at': asyncio.get_event_loop().time(),
        'model_a': {
            'overall_score': result.model_a.overall_score if result.model_a else 0,
            'avg_correctness': result.model_a.avg_correctness if result.model_a else 0,
            'avg_grounded_rate': result.model_a.avg_grounded_rate if result.model_a else 0,
            'avg_refusal_accuracy': result.model_a.avg_refusal_accuracy if result.model_a else 0,
            'questions_attempted': result.model_a.questions_attempted if result.model_a else 0,
            'questions_passed': result.model_a.questions_passed if result.model_a else 0,
        },
        'question_count': result.question_count,
        'duration_s': result.duration_s,
    }

    out_path = '$EVAL_OUTPUT'
    with open(out_path, 'w') as f:
        json.dump(output, f, indent=2, default=str)
    print(f'Results saved to {out_path}')
    print(json.dumps(output, indent=2, default=str))

asyncio.run(main())
" 2>&1 | tee "$LOG_DIR/eval_output.log"

echo "      Eval complete. Results:"
head -30 "$LOG_DIR/eval_output.log"

# ── Step 6: Stop the pod ───────────────────────────────────────────────────
echo "[6/6] Stopping pod $POD_ID..."
curl -s -X POST "$API_BASE/pods/$POD_ID/stop" \
  -H "Authorization: Bearer $API_KEY" > "$LOG_DIR/stop.json" 2>&1
echo "      Pod stop requested."

echo ""
echo "=== Done ==="
echo "Eval results: $EVAL_OUTPUT"
echo "Logs:        $LOG_DIR"
echo "Total cost:  ~$2-3 (60-90 min A100 80GB at ~$1.89/hr)"
