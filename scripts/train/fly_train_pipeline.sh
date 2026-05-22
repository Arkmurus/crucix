#!/bin/bash
# R-F809 — end-to-end ARIA-LLM fine-tune pipeline for Fly.io aria-trainer.
#
# Runs inside the aria-trainer Fly machine (Dockerfile.trainer).
# Replaces the manual RunPod runbook in [[runpod_signed_up]] with a
# single command the operator can fire via:
#
#   flyctl machine run registry.fly.io/aria-trainer:latest \
#     -a aria-trainer \
#     --vm-gpu-kind a100-80gb \
#     --vm-memory 65536 \
#     --vm-cpus 8 \
#     --mount aria_trainer_data=/data \
#     --command "bash /workspace/scripts/train/fly_train_pipeline.sh"
#
# Stages
# ──────
#   1. Verify corpus is present at /data/aria_training/ (must have been
#      uploaded via `flyctl ssh sftp` from aria-intel beforehand — see
#      scripts/train/README.md for the upload step).
#   2. Verify corpus is large enough (≥1000 pairs for proof-of-life,
#      ≥5000 for v0.1 release). Aborts cheaply if not.
#   3. Prepare SFT dataset (filter to quality ≥0.80).
#   4. Run SFT training (LoRA rank 32, default 3 epochs).
#   5. Prepare DPO preference pairs (optional — runs if dpo_pairs ≥100).
#   6. Run DPO training (on top of the SFT adapter).
#   7. Eval — runs the eval harness, prints a verdict line.
#   8. Print the next step: "flyctl machine stop <id>" to stop the meter.

set -euo pipefail

BASE_MODEL="${ARIA_BASE_MODEL:-mistralai/Mistral-7B-Instruct-v0.3}"
EPOCHS="${ARIA_TRAIN_EPOCHS:-3}"
LORA_RANK="${ARIA_LORA_RANK:-32}"
BATCH_SIZE="${ARIA_TRAIN_BATCH:-2}"
SFT_QUALITY_THRESHOLD="${ARIA_SFT_MIN_QUALITY:-0.80}"

CORPUS_DIR="/data/aria_training"
DATASETS_DIR="/data/datasets"
CHECKPOINTS_DIR="/data/checkpoints"
SFT_DATA="${DATASETS_DIR}/aria_sft_v1.jsonl"
DPO_DATA="${DATASETS_DIR}/aria_dpo_v1.jsonl"
SFT_OUT="${CHECKPOINTS_DIR}/aria_llm_v0_1_sft"
DPO_OUT="${CHECKPOINTS_DIR}/aria_llm_v0_1_dpo"

MIN_SFT_PAIRS_PROOF=1000     # below this, fine-tune won't converge
MIN_SFT_PAIRS_V01=5000       # below this, release as v0.1 prematurely
MIN_DPO_PAIRS=100            # below this, skip DPO

mkdir -p "${DATASETS_DIR}" "${CHECKPOINTS_DIR}"

echo "──────────────────────────────────────────────────────────────"
echo " ARIA-LLM fine-tune pipeline (R-F809)"
echo " Base model:   ${BASE_MODEL}"
echo " Epochs:       ${EPOCHS}"
echo " LoRA rank:    ${LORA_RANK}"
echo " Batch size:   ${BATCH_SIZE}"
echo "──────────────────────────────────────────────────────────────"

# Stage 1: corpus presence
if [ ! -d "${CORPUS_DIR}" ] || [ -z "$(ls -A "${CORPUS_DIR}" 2>/dev/null)" ]; then
  echo "FATAL: no harvest corpus at ${CORPUS_DIR}."
  echo "Upload from aria-intel first:"
  echo "  flyctl ssh sftp shell -a aria-intel"
  echo "  get /data/aria_training/training_data.jsonl /tmp/td.jsonl"
  echo "  exit"
  echo "  flyctl ssh sftp shell -a aria-trainer"
  echo "  put /tmp/td.jsonl /data/aria_training/training_data.jsonl"
  exit 1
fi

# Stage 2: corpus size check
TOTAL_PAIRS=$(find "${CORPUS_DIR}" -name '*.jsonl' -exec wc -l {} + 2>/dev/null \
              | awk '/total/ {print $1; exit} {sum+=$1} END {print sum+0}' \
              | tail -n 1)
TOTAL_PAIRS=${TOTAL_PAIRS:-0}
echo "[stage 2] corpus pairs: ${TOTAL_PAIRS}"

if [ "${TOTAL_PAIRS}" -lt "${MIN_SFT_PAIRS_PROOF}" ]; then
  echo "FATAL: corpus has only ${TOTAL_PAIRS} pairs; min ${MIN_SFT_PAIRS_PROOF}"
  echo "        required for proof-of-life. Let the harvester run longer."
  echo "        See /api/aria/harvest/stats for live count."
  exit 1
fi

if [ "${TOTAL_PAIRS}" -lt "${MIN_SFT_PAIRS_V01}" ]; then
  echo "[stage 2] WARN: ${TOTAL_PAIRS} pairs < ${MIN_SFT_PAIRS_V01} target"
  echo "          Proceeding as proof-of-life run. v0.1 release should"
  echo "          wait for ≥${MIN_SFT_PAIRS_V01} pairs."
fi

# Stage 2.5: adapt chat_audit JSONL → harvest format (R-F812).
# The training-data dailies emitted by training_export under
# `YYYY-MM-DD.jsonl` use the OpenAI Messages nested format
# (messages: [...], metadata: {...}) which prepare_sft.py doesn't
# unwrap. This adapter writes harvest-adapted-YYYY-MM-DD.jsonl files
# that the prep script's existing `harvest-*.jsonl` glob picks up.
echo "[stage 2.5] adapt_chat_audit (R-F812)"
python /workspace/scripts/train/adapt_chat_audit.py \
    --harvest-dir "${CORPUS_DIR}" \
    --out-prefix "harvest-adapted-"

# Stage 3: prepare SFT data
echo "[stage 3] prepare_sft"
python /workspace/scripts/train/prepare_sft.py \
    --harvest-dir "${CORPUS_DIR}" \
    --min-score "${SFT_QUALITY_THRESHOLD}" \
    --out "${SFT_DATA}"

SFT_LINES=$(wc -l < "${SFT_DATA}")
echo "[stage 3] SFT dataset: ${SFT_LINES} pairs (after quality filter)"

# Stage 4: SFT training
echo "[stage 4] sft_train (${BASE_MODEL})"
python /workspace/scripts/train/sft_train.py \
    --base-model "${BASE_MODEL}" \
    --train-file "${SFT_DATA}" \
    --output-dir "${SFT_OUT}" \
    --epochs "${EPOCHS}" \
    --lora-rank "${LORA_RANK}" \
    --batch-size "${BATCH_SIZE}"

# Stage 5: prepare DPO
echo "[stage 5] prepare_dpo"
python /workspace/scripts/train/prepare_dpo.py \
    --harvest-dir "${CORPUS_DIR}" \
    --out "${DPO_DATA}" \
    || true   # tolerate missing DPO inputs

DPO_LINES=0
if [ -f "${DPO_DATA}" ]; then
  DPO_LINES=$(wc -l < "${DPO_DATA}")
fi
echo "[stage 5] DPO dataset: ${DPO_LINES} preference pairs"

# Stage 6: DPO training (conditional)
if [ "${DPO_LINES}" -ge "${MIN_DPO_PAIRS}" ]; then
  echo "[stage 6] dpo_train"
  python /workspace/scripts/train/dpo_train.py \
      --base-model "${BASE_MODEL}" \
      --sft-adapter "${SFT_OUT}" \
      --train-file "${DPO_DATA}" \
      --output-dir "${DPO_OUT}"
  FINAL_ADAPTER="${DPO_OUT}"
else
  echo "[stage 6] SKIP — only ${DPO_LINES} DPO pairs (< ${MIN_DPO_PAIRS})"
  FINAL_ADAPTER="${SFT_OUT}"
fi

# Stage 7: eval
echo "[stage 7] eval_aria_llm"
python /workspace/scripts/train/eval_aria_llm.py \
    --base-model "${BASE_MODEL}" \
    --adapter "${FINAL_ADAPTER}" \
    || true   # eval is informational; don't fail the pipeline

echo ""
echo "──────────────────────────────────────────────────────────────"
echo " DONE."
echo " Final adapter:  ${FINAL_ADAPTER}"
echo " Next steps (from operator local machine):"
echo "   flyctl machine list -a aria-trainer    # find this machine's id"
echo "   flyctl machine stop <id> -a aria-trainer  # STOP THE METER"
echo ""
echo " To deploy as inference endpoint, the serving R-number"
echo " will add an aria-llm Fly app with vLLM + ${FINAL_ADAPTER}"
echo " mounted from this volume."
echo "──────────────────────────────────────────────────────────────"
