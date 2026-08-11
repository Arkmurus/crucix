#!/usr/bin/env bash
# R-F3848 — build, verify, and run one staged positive-learning candidate.
set -euo pipefail
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd); REPO=$(cd "$SCRIPT_DIR/../.." && pwd); cd "$REPO"
PYBIN="${PYBIN:-.venv/Scripts/python.exe}"
TRAIN=data/training/tooluse_base_balanced_v1_queue.jsonl
SOURCE_DPO=data/training/aria_tooluse_base_balanced_v1_dpo.jsonl
RAW=data/eval_reports/aria_tooluse_base_balanced_v1_generations.json
SFT=data/training/aria_tooluse_curve_v2_sft.jsonl
DPO=data/training/aria_tooluse_curve_v2_dpo.jsonl
PROBE=data/training/aria_tooluse_curve_v2_probe.jsonl
BASELINE=data/eval_reports/aria_tooluse_curve_v2_raw_probe.json
MANIFEST=data/eval_reports/tooluse_curve_v2_manifest.json
EVAL=data/training/split_v2/eval.jsonl
GOLDEN=data/eval_frozen/aria_eval_500q.jsonl
"$PYBIN" -m scripts.train.build_positive_curve_assets --train "$TRAIN" --dpo "$SOURCE_DPO" --raw-report "$RAW" --eval "$EVAL" --golden "$GOLDEN" --quota 3 --sft-out "$SFT" --dpo-out "$DPO" --probe-out "$PROBE" --baseline-out "$BASELINE" --manifest-out "$MANIFEST"
"$PYBIN" -m scripts.train.preflight_cycle --train-file "$SFT" --eval-file "$EVAL" --base-model mistralai/Mistral-7B-Instruct-v0.3 --golden-set "$GOLDEN" --strict
hash(){ sha256sum "$1" | awk '{print $1}'; }
FRESH_BASE=1 EXPECTED_DPO_PAIRS=47 DPO_SHA256="$(hash "$DPO")" EVAL_SHA256="$(hash "$EVAL")" \
  POD_RUNNER=scripts/train/pod_tooluse_curve.sh SFT_LOCAL="$SFT" SFT_SHA256="$(hash "$SFT")" \
  PROBE_LOCAL="$PROBE" PROBE_SHA256="$(hash "$PROBE")" BASELINE_LOCAL="$BASELINE" BASELINE_SHA256="$(hash "$BASELINE")" \
  DPO_LOCAL="$DPO" EVAL_LOCAL="$EVAL" TRAIN_PROOF="$SFT" \
  REPORT_LOCAL=data/eval_reports/aria_tooluse_curve_v2_eval.json \
  OUTPUT_LOCAL=data/training/checkpoints/aria_tooluse_curve_v2.tgz \
  INTERMEDIATE_LOCAL=data/training/checkpoints/aria_tooluse_curve_sft_v2.tgz \
  DIAGNOSTICS_LOCAL=data/eval_reports/aria_tooluse_curve_v2_diagnostics.tgz \
  STATE_FILE=data/eval_reports/.tooluse_curve_v2_pod_state \
  REMOTE_DPO_OUT=/workspace/checkpoints/aria_tooluse_curve_dpo CYCLE_DEADLINE=14400 \
  bash scripts/train/run_tooluse_dpo.sh
