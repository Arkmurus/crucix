#!/usr/bin/env bash
# R-F4075 — resolution-only DPO probe; output is never a promotable artifact.
set -euo pipefail
ROOT=$(cd "$(dirname "$BASH_SOURCE")/../.."; pwd); cd "$ROOT"
hash(){ sha256sum "$1" | awk '{print $1}'; }

ADAPTER=data/training/checkpoints/aria_tooluse_protected_positive_v1_failed_candidate.tgz
PROBE=data/training/aria_tooluse_curve_v5_probe.jsonl
BASELINE=data/eval_reports/rf4054_recovery_baseline/aria_tooluse_sft_child_probe.json
DPO=data/training/aria_tooluse_protected_positive_v1_resolution_dpo.jsonl
EVAL=data/training/split_v1/eval.jsonl
TRAIN_PROOF=data/training/tooluse_novel_resolution_generation_queue.jsonl

test "$(hash "$ADAPTER")" = 223ba1dc99d0e65aafdfa3f5190d57e0e8dfdd4013f9fab5be3994af63384998
test "$(hash "$PROBE")" = 72b7eca2a90db4d1e3a6a4448a2d17f3b2a0dd165f82ab96fd975720d0227c5c
test "$(hash "$BASELINE")" = ad4f6dadef560f4fafbfd47312facd2cadf6cf8955fc7486b8f1a6c1f06703da
test "$(hash "$DPO")" = 03a2c86da1d7f3c006ded6c37b599bd034323edcad67ea152b64b0462d3c6681
test "$(hash "$EVAL")" = d24be361fb30ff0e51272b2a7338be2924b8df5428d55a469f1c907bd28c3b00

REPO="$ROOT" ARIA_POD_CREATE_API=graphql ARIA_MAX_GPU_HOURLY_USD=1.60 \
  TRAINING_RECIPE_KIND=tooluse_dpo_continuation \
  FRESH_BASE=0 EXPECTED_DPO_PAIRS=8 PROTECTED_DPO_AXES=tooluse_resolution \
  POD_RUNNER=scripts/train/pod_tooluse_dpo.sh \
  ADAPTER_LOCAL="$ADAPTER" ADAPTER_SHA256="$(hash "$ADAPTER")" \
  PROBE_LOCAL="$PROBE" PROBE_SHA256="$(hash "$PROBE")" \
  BASELINE_LOCAL="$BASELINE" BASELINE_SHA256="$(hash "$BASELINE")" \
  DPO_LOCAL="$DPO" DPO_SHA256="$(hash "$DPO")" \
  EVAL_LOCAL="$EVAL" TRAIN_PROOF="$TRAIN_PROOF" \
  REPORT_LOCAL=data/eval_reports/aria_tooluse_resolution_dpo_probe_v1_eval.json \
  OUTPUT_LOCAL=data/training/checkpoints/aria_tooluse_resolution_dpo_probe_v1_failed_candidate.tgz \
  INTERMEDIATE_LOCAL=data/training/checkpoints/aria_tooluse_resolution_dpo_probe_v1_failed_candidate.tgz \
  INTERMEDIATE_REMOTE=/workspace/eval/aria_tooluse_dpo_adapter.tgz \
  DIAGNOSTICS_LOCAL=data/eval_reports/aria_tooluse_resolution_dpo_probe_v1_diagnostics.tgz \
  STATE_FILE=data/eval_reports/.tooluse_resolution_dpo_probe_v1_pod_state \
  REMOTE_DPO_OUT=/workspace/checkpoints/aria_tooluse_resolution_dpo_probe_v1 \
  MIN_CYCLE_DEADLINE=7200 CYCLE_DEADLINE=14400 \
  exec bash scripts/train/run_immutable_shell.sh scripts/train/run_tooluse_dpo.sh
