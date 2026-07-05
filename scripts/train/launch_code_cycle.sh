#!/usr/bin/env bash
# R-F2440 — code-sovereign train/eval cycle LAUNCHER with a mechanical §24 gate.
#
# This is the "lets do it" button. It refuses to spend a paid GPU cycle on an
# unready corpus (the §24 rule: training must be REAL — no thin/unreviewed data).
# It runs the pre-flight, and ONLY if the corpus is SFT-READY does it print the
# exact pod runbook to launch. It never starts a GPU itself (pod + creds are the
# operator-gated step) — it makes the launch one copy-paste when the data is real.
#
# Usage (local, no spend):  bash scripts/train/launch_code_cycle.sh
set -euo pipefail
cd "$(dirname "$0")/../.."
source scripts/train/code_sovereign_config.sh

echo "=== §24 PRE-FLIGHT (code-sovereign cycle) ==="
PF="$(python scripts/train/prepare_code_sft.py --out data/training/code_sft_v1.jsonl)"
echo "$PF"
READY="$(printf '%s\n' "$PF" | sed -n 's/^SFT-READY.*: //p' | tr -d '[:space:]')"
PAIRS="$(printf '%s\n' "$PF" | sed -n 's/^SFT pairs written: *\([0-9]*\).*/\1/p')"

if [ "$READY" != "True" ]; then
  echo ""
  echo "BLOCKED: NOT SFT-READY (${PAIRS:-0} clean pairs, gate wants >=300)."
  echo "  The mine is still producing verified rows. Re-run when it completes."
  echo "  Refusing to spend a paid GPU cycle on a thin corpus (§24). No pod started."
  exit 1
fi

echo ""
echo "=== SFT-READY (${PAIRS} pairs). POD RUNBOOK (operator confirms spend, then run on the RunPod pod): ==="
cat <<RUNBOOK
# 0) start the pod (§24 RunPod scheduler / weekly-cycle window) and ship datasets:
#    data/training/code_sft_v1.jsonl        -> ${CODE_SFT_FILE}
#    data/eval/mined_code_eval_tier.jsonl   -> ${CODE_EVAL_TIER}
#
# 1) VERIFY base (the #1 cycle-waster) — must be a code-native, ungated base:
echo "base = ${CODE_BASE_MODEL}"
#
# 2) SFT (LoRA) on the code corpus:
python scripts/train/sft_train.py \\
  --base-model ${CODE_BASE_MODEL} \\
  --train-file ${CODE_SFT_FILE} \\
  --output-dir ${CODE_ADAPTER_PATH} \\
  --epochs ${CODE_EPOCHS} --lora-rank ${CODE_LORA_RANK} --lora-alpha ${CODE_LORA_ALPHA} \\
  --lr ${CODE_LR} --max-seq-len ${CODE_MAX_SEQ_LEN}
#
# 3) serve the adapter (vLLM, OpenAI-compatible) as ${CODE_MODEL_NAME} on :8000
#    (HF_HOME=${CODE_HF_HOME}, max-model-len ${CODE_MAX_MODEL_LEN})
#
# 4) EVAL against the frozen tier — same scorer, endpoint-agnostic:
python scripts/eval/eval_mined_tier.py \\
  --eval-set ${CODE_EVAL_TIER} \\
  --target http://localhost:8000/v1 --model ${CODE_MODEL_NAME} \\
  --out data/eval_reports/code_reasoning_mined_sovereign_v0.json
#
# 5) ACTIVATION GATE: only flip ARIA_LLM_URL to the sovereign for the coder path
#    if resolved_rate >= ${CODE_ACTIVATION_MIN_RESOLVED_RATE} (must BEAT DeepSeek).
#    Otherwise the cycle is a measured no-go; iterate corpus/base, do not activate.
#
# 6) STOP the pod (§24 stop-only) — cycle scripts must stop the pod in their final step.
RUNBOOK
