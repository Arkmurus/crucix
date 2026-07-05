#!/usr/bin/env bash
# R-F2440 — SINGLE SOURCE OF TRUTH for the CODE-SOVEREIGN model identity.
#
# Mirrors model_config.sh (the DD model) but for the code reasoner. Every code
# train/serve/eval step sources THIS so the base model can never disagree with
# the adapter — a LoRA adapter trained on one base CANNOT load on another and a
# mismatch silently wastes a whole paid GPU cycle (the documented #1 cycle-waster
# in model_config.sh). VERIFY the adapter base before any paid serve.
#
# WHY a code-native base (not the DD Mistral): the sovereign that will reason on
# ARIA's coder path must be code-native. Qwen2.5-Coder is the strongest open code
# base in the 7-32B range and is NOT gated on HF (unlike Mistral-7B), so the pod
# can pull it without an HF token.
#
# Base size is the budget lever (operator offered to raise budget):
#   7B  — cheapest (~$8-18 for an SFT+eval cycle), baseline attempt.
#   14B — better reasoning, ~2-3x the cost/time.
#   32B — strongest odds vs DeepSeek, needs an 80GB GPU (A100/H100), highest cost.
# Override CODE_BASE_MODEL to switch. Default 7B (prove the pipeline cheaply first).

CODE_BASE_MODEL="${CODE_BASE_MODEL:-Qwen/Qwen2.5-Coder-7B-Instruct}"
CODE_ADAPTER_PATH="${CODE_ADAPTER_PATH:-/workspace/checkpoints/aria_code_sovereign_v0_sft}"
CODE_MODEL_NAME="${CODE_MODEL_NAME:-aria-code-sovereign-v0}"
CODE_MAX_MODEL_LEN="${CODE_MAX_MODEL_LEN:-8192}"   # inputs run ~6k tokens (localized-edit windows)
CODE_HF_HOME="${CODE_HF_HOME:-/workspace/.cache/huggingface}"

# Data (produced by prepare_code_sft.py; eval tier is the frozen bar).
CODE_SFT_FILE="${CODE_SFT_FILE:-/workspace/datasets/code_sft_v1.jsonl}"
CODE_EVAL_TIER="${CODE_EVAL_TIER:-/workspace/datasets/mined_code_eval_tier.jsonl}"

# LoRA / SFT knobs (sft_train.py). Higher max-seq-len than the DD model because
# code windows are longer than DD Q/A pairs.
CODE_EPOCHS="${CODE_EPOCHS:-3}"
CODE_LORA_RANK="${CODE_LORA_RANK:-32}"
CODE_LORA_ALPHA="${CODE_LORA_ALPHA:-64}"
CODE_LR="${CODE_LR:-2e-5}"
CODE_MAX_SEQ_LEN="${CODE_MAX_SEQ_LEN:-8192}"

# ACTIVATION GATE (the number the sovereign must beat to replace DeepSeek on the
# coder path, R-F1366). Set from the frozen-tier DeepSeek baseline at mine
# completion (bounded run: DeepSeek = 0.5714 on n=7; refresh at full-corpus n).
CODE_ACTIVATION_MIN_RESOLVED_RATE="${CODE_ACTIVATION_MIN_RESOLVED_RATE:-0.58}"

export CODE_BASE_MODEL CODE_ADAPTER_PATH CODE_MODEL_NAME CODE_MAX_MODEL_LEN \
       CODE_HF_HOME CODE_SFT_FILE CODE_EVAL_TIER CODE_EPOCHS CODE_LORA_RANK \
       CODE_LORA_ALPHA CODE_LR CODE_MAX_SEQ_LEN CODE_ACTIVATION_MIN_RESOLVED_RATE
