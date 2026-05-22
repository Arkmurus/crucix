#!/bin/bash
# R-F819 — end-to-end ARIA-LLM fine-tune pipeline for RunPod.
#
# Replaces the Fly aria-trainer path (R-F809) — Fly deprecated GPUs as
# of 2026-05-22 ("GPUs are deprecated and will be unavailable after
# August 1" per https://fly.io/docs/gpus/python-gpu-example/). The
# original [[runpod_signed_up]] memory documents account setup;
# Step 6 there is what this script automates.
#
# Runs on a RunPod A100 / RTX 4090 pod started with the "RunPod PyTorch
# 2.4" community template (already has python + cuda + torch
# pre-installed). The transformers/trl/peft/bitsandbytes stack still
# needs to be installed once — see Step 3 in the [[runpod_signed_up]]
# runbook.
#
# Operator workflow (from your local machine):
#   1. Provision pod via RunPod console (see RUNPOD.md)
#   2. Web Terminal: paste this script's URL or git-clone the repo
#   3. Run: bash scripts/train/runpod_train_pipeline.sh
#   4. Adapter ends up at /workspace/checkpoints/aria_llm_v0_1_dpo/
#   5. STOP THE POD via console — your meter is ticking
#
# Stages
# ──────
#   1. Verify corpus is present at /workspace/data/aria_training/
#      (operator uploaded via runpod console File Manager OR via wget
#      from a tarball URL — see RUNPOD.md).
#   2. Install training deps (transformers/trl/peft/bitsandbytes).
#   3. Adapt chat_audit YYYY-MM-DD.jsonl → harvest format (R-F812).
#   4. Prepare SFT dataset (filter to quality ≥0.80).
#   5. SFT training (LoRA rank 32, default 3 epochs).
#   6. Prepare + run DPO if ≥100 preference pairs.
#   7. Eval — prints a verdict line.
#   8. Print "STOP THE POD" command.

set -euo pipefail

BASE_MODEL="${ARIA_BASE_MODEL:-mistralai/Mistral-7B-Instruct-v0.3}"
EPOCHS="${ARIA_TRAIN_EPOCHS:-3}"
LORA_RANK="${ARIA_LORA_RANK:-32}"
BATCH_SIZE="${ARIA_TRAIN_BATCH:-2}"
SFT_QUALITY_THRESHOLD="${ARIA_SFT_MIN_QUALITY:-0.80}"

# RunPod convention: /workspace is the persistent volume on most templates.
CORPUS_DIR="${ARIA_CORPUS_DIR:-/workspace/data/aria_training}"
DATASETS_DIR="${ARIA_DATASETS_DIR:-/workspace/datasets}"
CHECKPOINTS_DIR="${ARIA_CHECKPOINTS_DIR:-/workspace/checkpoints}"
REPO_DIR="${ARIA_REPO_DIR:-/workspace/crucix}"

SFT_DATA="${DATASETS_DIR}/aria_sft_v1.jsonl"
DPO_DATA="${DATASETS_DIR}/aria_dpo_v1.jsonl"
SFT_OUT="${CHECKPOINTS_DIR}/aria_llm_v0_1_sft"
DPO_OUT="${CHECKPOINTS_DIR}/aria_llm_v0_1_dpo"

MIN_SFT_PAIRS_PROOF=1000
MIN_SFT_PAIRS_V01=5000
MIN_DPO_PAIRS=100

mkdir -p "${DATASETS_DIR}" "${CHECKPOINTS_DIR}"

echo "──────────────────────────────────────────────────────────────"
echo " ARIA-LLM fine-tune pipeline (R-F819 RunPod variant)"
echo " Base model:   ${BASE_MODEL}"
echo " Epochs:       ${EPOCHS}"
echo " LoRA rank:    ${LORA_RANK}"
echo " Batch size:   ${BATCH_SIZE}"
echo " Corpus dir:   ${CORPUS_DIR}"
echo "──────────────────────────────────────────────────────────────"

# Stage 1: corpus presence
if [ ! -d "${CORPUS_DIR}" ] || [ -z "$(ls -A "${CORPUS_DIR}" 2>/dev/null)" ]; then
  echo "FATAL: no harvest corpus at ${CORPUS_DIR}."
  echo "Upload via the RunPod console File Manager OR:"
  echo "  cd /workspace && wget <signed-tarball-url> && tar -xzf corpus.tar.gz"
  echo "  (see scripts/train/RUNPOD.md for the aria-intel SFTP path)"
  exit 1
fi

# Stage 2a: install training deps if not already present
echo "[stage 2a] install training stack (idempotent)"
pip install --quiet \
    transformers==4.45.0 \
    trl==0.11.0 \
    peft==0.13.0 \
    accelerate==1.0.0 \
    bitsandbytes==0.44.1 \
    datasets==3.0.0 \
    sentencepiece \
    protobuf \
    huggingface_hub

# Stage 2b: corpus size check
TOTAL_PAIRS=$(find "${CORPUS_DIR}" -name '*.jsonl' -exec wc -l {} + 2>/dev/null \
              | awk '/total/ {print $1; exit} {sum+=$1} END {print sum+0}' \
              | tail -n 1)
TOTAL_PAIRS=${TOTAL_PAIRS:-0}
echo "[stage 2b] corpus pairs: ${TOTAL_PAIRS}"

if [ "${TOTAL_PAIRS}" -lt "${MIN_SFT_PAIRS_PROOF}" ]; then
  echo "FATAL: corpus has only ${TOTAL_PAIRS} pairs; min ${MIN_SFT_PAIRS_PROOF}"
  echo "       required for proof-of-life. Let the harvester run longer."
  exit 1
fi
if [ "${TOTAL_PAIRS}" -lt "${MIN_SFT_PAIRS_V01}" ]; then
  echo "[stage 2b] WARN: ${TOTAL_PAIRS} pairs < ${MIN_SFT_PAIRS_V01} target."
  echo "          Proceeding as proof-of-life run only."
fi

# Stage 3: adapt chat_audit JSONL → harvest format (R-F812)
echo "[stage 3] adapt_chat_audit"
python "${REPO_DIR}/scripts/train/adapt_chat_audit.py" \
    --harvest-dir "${CORPUS_DIR}" \
    --out-prefix "harvest-adapted-"

# Stage 4: prepare SFT data
echo "[stage 4] prepare_sft"
python "${REPO_DIR}/scripts/train/prepare_sft.py" \
    --harvest-dir "${CORPUS_DIR}" \
    --min-score "${SFT_QUALITY_THRESHOLD}" \
    --out "${SFT_DATA}"

SFT_LINES=$(wc -l < "${SFT_DATA}")
echo "[stage 4] SFT dataset: ${SFT_LINES} pairs (after quality filter)"

# Stage 5: SFT training
echo "[stage 5] sft_train (${BASE_MODEL})"
python "${REPO_DIR}/scripts/train/sft_train.py" \
    --base-model "${BASE_MODEL}" \
    --train-file "${SFT_DATA}" \
    --output-dir "${SFT_OUT}" \
    --epochs "${EPOCHS}" \
    --lora-rank "${LORA_RANK}" \
    --batch-size "${BATCH_SIZE}"

# Stage 6: prepare DPO
echo "[stage 6] prepare_dpo"
python "${REPO_DIR}/scripts/train/prepare_dpo.py" \
    --harvest-dir "${CORPUS_DIR}" \
    --out "${DPO_DATA}" \
    || true

DPO_LINES=0
if [ -f "${DPO_DATA}" ]; then
  DPO_LINES=$(wc -l < "${DPO_DATA}")
fi
echo "[stage 6] DPO dataset: ${DPO_LINES} preference pairs"

# Stage 7: DPO training (conditional)
if [ "${DPO_LINES}" -ge "${MIN_DPO_PAIRS}" ]; then
  echo "[stage 7] dpo_train"
  python "${REPO_DIR}/scripts/train/dpo_train.py" \
      --base-model "${BASE_MODEL}" \
      --sft-adapter "${SFT_OUT}" \
      --train-file "${DPO_DATA}" \
      --output-dir "${DPO_OUT}"
  FINAL_ADAPTER="${DPO_OUT}"
else
  echo "[stage 7] SKIP — only ${DPO_LINES} DPO pairs (< ${MIN_DPO_PAIRS})"
  FINAL_ADAPTER="${SFT_OUT}"
fi

# Stage 8: eval
echo "[stage 8] eval_aria_llm"
python "${REPO_DIR}/scripts/train/eval_aria_llm.py" \
    --base-model "${BASE_MODEL}" \
    --adapter "${FINAL_ADAPTER}" \
    || true

echo ""
echo "──────────────────────────────────────────────────────────────"
echo " DONE."
echo " Final adapter:  ${FINAL_ADAPTER}"
echo ""
echo " STOP THE POD via the RunPod console NOW —"
echo " your meter is still ticking until you click Stop."
echo ""
echo " To wire ARIA-LLM into aria-intel's chain (from local):"
echo "   1. RunPod console → Deploy a serving endpoint with vLLM +"
echo "      the adapter from /workspace/checkpoints/aria_llm_v0_1_dpo"
echo "   2. flyctl secrets set ARIA_LLM_URL=https://<runpod-endpoint>/v1 \\"
echo "        ARIA_LLM_MODEL=aria_v0_1 -a aria-intel"
echo "   3. flyctl machines restart -a aria-intel"
echo "──────────────────────────────────────────────────────────────"
