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
# R-F1454 (2026-06-09) — CORRECTED: v0.2 is actually Mistral-7B-Instruct-v0.3,
# NOT Qwen2.5-14B. The DPO adapter at /workspace/checkpoints/aria_llm_v0_2_dpo was
# produced by train_promote_v0_2.sh:30 (BASE_MODEL=mistralai/Mistral-7B-Instruct-v0.3)
# and its adapter_config.json base_model_name_or_path confirms Mistral-7B. The
# Qwen2.5-14B weights WERE downloaded to the pod volume (/workspace/qwen14b) as
# exploratory work, but NO Qwen adapter was ever trained — Qwen-14B is the
# ASPIRATIONAL future base, not what exists. Recording Qwen here (the previous
# value) is exactly what the adapter-base guard caught on the first paid run and
# is why this file existed; it had the desired state, not the trained one.
#
# Base weights live in the PERSISTENT volume HF cache (the container ~/.cache is
# wiped on restart): /workspace/.cache/huggingface. Mistral-7B-Instruct-v0.3 is
# GATED on HF and there is NO HF token on the pod, so serving MUST load from this
# cache (serve_and_eval_v02.sh sets HF_HOME + HF_HUB_OFFLINE in the vLLM launch).
#
# ⚠️  VERIFY BEFORE THE FIRST PAID RUN (do NOT skip — this is the #1 cycle-waster):
#     On the pod, read the adapter's base:
#       python -c "import json;print(json.load(open('$ARIA_ADAPTER_PATH/adapter_config.json'))['base_model_name_or_path'])"
#     Confirm it matches ARIA_BASE_MODEL below.

# --- canonical values (override via env to switch model versions) ---
ARIA_BASE_MODEL="${ARIA_BASE_MODEL:-mistralai/Mistral-7B-Instruct-v0.3}"
ARIA_ADAPTER_PATH="${ARIA_ADAPTER_PATH:-/workspace/checkpoints/aria_llm_v0_2_dpo}"
ARIA_MODEL_NAME="${ARIA_MODEL_NAME:-aria-llm-v0.2}"
ARIA_MAX_MODEL_LEN="${ARIA_MAX_MODEL_LEN:-32768}"
# Persistent volume HF cache (base weights survive pod restarts here):
ARIA_HF_HOME="${ARIA_HF_HOME:-/workspace/.cache/huggingface}"
# Frozen 500-Q eval set (export with scripts/train/export_eval_500q.py).
ARIA_EVAL_SET="${ARIA_EVAL_SET:-/workspace/datasets/aria_eval_500q.jsonl}"
# Aspirational future base — downloaded to /workspace/qwen14b but NO adapter yet:
ARIA_FUTURE_BASE_MODEL="Qwen/Qwen2.5-14B-Instruct"

export ARIA_BASE_MODEL ARIA_ADAPTER_PATH ARIA_MODEL_NAME ARIA_MAX_MODEL_LEN ARIA_HF_HOME ARIA_EVAL_SET
