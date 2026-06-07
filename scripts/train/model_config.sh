#!/usr/bin/env bash
# R-F1430 — SINGLE SOURCE OF TRUTH for ARIA-LLM model identity.
#
# Every train / serve / eval script sources THIS file so the base model,
# adapter path, served name and context window can never disagree again.
# Before R-F1430 they DID: serve_and_eval_v02.sh said Qwen2.5-14B, dpo_train.py
# defaulted to Llama-3.3-70B, the pipelines defaulted to Mistral-7B. A LoRA
# adapter trained on one base CANNOT load on another, so a mismatch silently
# wastes a whole paid GPU cycle.
#
# v0.2 (current sovereign work) = Qwen2.5-14B — per the 2026-06-06 14B work on
# RunPod pod 7ei3hldcpz4j2v (/workspace/qwen14b, served name aria-llm-v0.2).
# The v0.1 era used Mistral-7B-Instruct-v0.3 (kept here as a reference).
#
# ⚠️  VERIFY BEFORE THE FIRST PAID RUN (do NOT skip — this is the #1 cycle-waster):
#     On the pod, read the adapter's base:
#       python -c "import json;print(json.load(open('$ARIA_ADAPTER_PATH/adapter_config.json'))['base_model_name_or_path'])"
#     Confirm it matches ARIA_BASE_MODEL below. If the adapter is Mistral-7B,
#     export ARIA_BASE_MODEL=mistralai/Mistral-7B-Instruct-v0.3 before serving.

# --- canonical values (override via env to switch model versions) ---
ARIA_BASE_MODEL="${ARIA_BASE_MODEL:-Qwen/Qwen2.5-14B-Instruct}"
ARIA_ADAPTER_PATH="${ARIA_ADAPTER_PATH:-/workspace/checkpoints/aria_llm_v0_2_dpo}"
ARIA_MODEL_NAME="${ARIA_MODEL_NAME:-aria-llm-v0.2}"
ARIA_MAX_MODEL_LEN="${ARIA_MAX_MODEL_LEN:-32768}"
# Frozen 500-Q eval set (export with scripts/train/export_eval_500q.py).
ARIA_EVAL_SET="${ARIA_EVAL_SET:-/workspace/datasets/aria_eval_500q.jsonl}"
# v0.1 reference (historical) — NOT the current base:
ARIA_V01_BASE_MODEL="mistralai/Mistral-7B-Instruct-v0.3"

export ARIA_BASE_MODEL ARIA_ADAPTER_PATH ARIA_MODEL_NAME ARIA_MAX_MODEL_LEN ARIA_EVAL_SET
