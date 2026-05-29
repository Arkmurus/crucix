#!/bin/bash
# ARIA-LLM v0.1 Activation Script (R-F1052)
# Run this on the RunPod pod after it's started.
#
# Usage:
#   ssh root@<pod-host> -p <pod-port> 'bash -s' < scripts/train/activate_aria_llm_v01.sh

set -euo pipefail

echo "=== ARIA-LLM v0.1 Activation ==="
echo "Started: $(date)"

BASE_MODEL="mistralai/Mistral-7B-Instruct-v0.3"
SFT_CHECKPOINT="/workspace/checkpoints/aria_llm_v0_1_sft"
DPO_CHECKPOINT="/workspace/checkpoints/aria_llm_v0_1_dpo"
DATASETS_DIR="/workspace/datasets"
EVAL_SET="${DATASETS_DIR}/aria_eval_500q.jsonl"
DPO_FILE="${DATASETS_DIR}/aria_dpo_v1.jsonl"
LOGS_DIR="/workspace/logs"
EVAL_DIR="/workspace/eval"

mkdir -p "${LOGS_DIR}" "${EVAL_DIR}"

echo "Step 1: Installing dependencies..."
pip install -q --upgrade pip
pip install -q torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu118
pip install -q transformers trl peft accelerate bitsandbytes datasets
pip install -q vllm httpx
echo "Dependencies installed."

echo "Step 2: Checking SFT checkpoint..."
if [ -d "${SFT_CHECKPOINT}" ]; then
    echo "SFT checkpoint found at ${SFT_CHECKPOINT}"
    ls -la "${SFT_CHECKPOINT}/"
else
    echo "ERROR: SFT checkpoint not found at ${SFT_CHECKPOINT}"
    exit 1
fi

echo "Step 3: Checking DPO dataset..."
if [ -f "${DPO_FILE}" ]; then
    echo "DPO dataset exists: $(wc -l < "${DPO_FILE}") pairs"
else
    echo "DPO dataset not found. Will use SFT-only mode."
fi

if [ -f "${DPO_FILE}" ]; then
    echo "Step 4: Running DPO training..."
    python /workspace/scripts/train/dpo_train.py \
        --base-model "${BASE_MODEL}" \
        --sft-checkpoint "${SFT_CHECKPOINT}" \
        --dpo-file "${DPO_FILE}" \
        --output-dir "${DPO_CHECKPOINT}" \
        --epochs 1 \
        --beta 0.1 \
        --lr 5e-6 \
        --batch-size 2 \
        --max-seq-len 4096 \
        --load-in-4bit 2>&1 | tee "${LOGS_DIR}/dpo_train.log"
    echo "DPO training complete."
else
    echo "Step 4: Skipping DPO training (no dataset). Using SFT checkpoint."
fi

echo "Step 5: Checking eval set..."
if [ ! -f "${EVAL_SET}" ]; then
    echo "Creating basic eval set..."
    cat > "${EVAL_SET}" << 'EVALEOF'
{"question": "What is the OFAC SDN list?", "expected_keywords": ["OFAC", "SDN", "sanctions", "Treasury"], "topic": "compliance"}
{"question": "Who is the current US Secretary of Defense?", "expected_keywords": ["Secretary of Defense", "United States"], "topic": "geopolitics"}
{"question": "What is the range of the Bayraktar TB2?", "expected_keywords": ["Bayraktar TB2", "range", "150 km"], "topic": "defence"}
{"question": "What is ITAR?", "expected_keywords": ["ITAR", "International Traffic in Arms", "export control"], "topic": "compliance"}
{"question": "What is SIPRI?", "expected_keywords": ["SIPRI", "Stockholm International Peace", "arms transfers"], "topic": "defence"}
EVALEOF
fi
echo "Eval set: $(wc -l < "${EVAL_SET}") questions"

echo "Step 6: Starting vLLM..."
LORA_PATH="${DPO_CHECKPOINT}"
if [ ! -d "${DPO_CHECKPOINT}" ]; then
    LORA_PATH="${SFT_CHECKPOINT}"
fi
echo "Using LoRA: ${LORA_PATH}"

pkill -f "vllm.entrypoints.openai" 2>/dev/null || true
sleep 2

nohup python -m vllm.entrypoints.openai.api_server \
    --model "${BASE_MODEL}" \
    --enable-lora \
    --lora-modules aria-llm-v0.1="${LORA_PATH}" \
    --max-loras 1 \
    --max-lora-rank 16 \
    --gpu-memory-utilization 0.9 \
    --host 0.0.0.0 --port 8000 \
    > "${LOGS_DIR}/vllm_serve.log" 2>&1 &

echo "Waiting for vLLM (35s)..."
sleep 35

echo "Verifying vLLM..."
curl -s http://localhost:8000/v1/models | python -m json.tool || {
    echo "vLLM failed. Logs:"
    tail -20 "${LOGS_DIR}/vllm_serve.log"
    exit 1
}

echo "Running evaluation..."
python /workspace/scripts/train/eval_aria_llm.py \
    --target "http://localhost:8000/v1" \
    --model "aria-llm-v0.1" \
    --eval-set "${EVAL_SET}" \
    --out "${EVAL_DIR}/aria_llm_v0.1_report.json" 2>&1 | tee "${LOGS_DIR}/eval.log"

echo "Evaluation report: ${EVAL_DIR}/aria_llm_v0.1_report.json"
python -m json.tool "${EVAL_DIR}/aria_llm_v0.1_report.json" 2>/dev/null || true

echo "=== Activation complete: $(date) ==="
