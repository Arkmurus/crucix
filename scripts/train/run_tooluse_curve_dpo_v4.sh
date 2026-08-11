#!/usr/bin/env bash
# R-F3867 — continue only from the measured-positive v4 SFT adapter.
set -euo pipefail
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO=$(cd "$SCRIPT_DIR/../.." && pwd); cd "$REPO"
PYBIN="${PYBIN:-.venv/Scripts/python.exe}"
ADAPTER=data/training/checkpoints/aria_tooluse_curve_sft_v4.tgz
DPO=data/training/aria_tooluse_curve_v2_dpo.jsonl
PROBE=data/training/aria_tooluse_curve_v2_probe.jsonl
BEFORE=data/eval_reports/aria_tooluse_curve_v4_sft_rescored.json
EVAL=data/training/split_v2/eval.jsonl
GOLDEN=data/eval_frozen/aria_eval_500q.jsonl
for file in "$ADAPTER" "$DPO" "$PROBE" "$BEFORE" "$EVAL" "$GOLDEN"; do
  [ -s "$file" ] || { echo "missing continuation input: $file" >&2; exit 1; }
done
"$PYBIN" -m scripts.train.preflight_cycle --train-file data/training/aria_tooluse_curve_v2_sft.jsonl \
  --eval-file "$EVAL" --base-model mistralai/Mistral-7B-Instruct-v0.3 \
  --golden-set "$GOLDEN" --strict
hash(){ sha256sum "$1" | awk '{print $1}'; }
FRESH_BASE=0 EXPECTED_DPO_PAIRS=47 \
  ADAPTER_LOCAL="$ADAPTER" ADAPTER_SHA256="$(hash "$ADAPTER")" \
  DPO_LOCAL="$DPO" DPO_SHA256="$(hash "$DPO")" EVAL_LOCAL="$EVAL" EVAL_SHA256="$(hash "$EVAL")" \
  POD_RUNNER=scripts/train/pod_tooluse_dpo.sh \
  PROBE_LOCAL="$PROBE" PROBE_SHA256="$(hash "$PROBE")" \
  BASELINE_LOCAL="$BEFORE" BASELINE_SHA256="$(hash "$BEFORE")" \
  TRAIN_PROOF=data/training/aria_tooluse_curve_v2_sft.jsonl \
  REMOTE_SFT_ADAPTER=/workspace/checkpoints/aria_tooluse_curve_sft \
  REMOTE_DPO_OUT=/workspace/checkpoints/aria_tooluse_curve_dpo_v4 \
  REPORT_LOCAL=data/eval_reports/aria_tooluse_curve_v4_dpo_eval.json \
  OUTPUT_LOCAL=data/training/checkpoints/aria_tooluse_curve_dpo_v4.tgz \
  DIAGNOSTICS_LOCAL=data/eval_reports/aria_tooluse_curve_v4_dpo_diagnostics.tgz \
  STATE_FILE=data/eval_reports/.tooluse_curve_v4_dpo_pod_state \
  CYCLE_DEADLINE=14400 \
  bash scripts/train/run_tooluse_dpo.sh
