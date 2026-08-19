#!/usr/bin/env bash
# R-F4140 — deduplicated, decision-state-covered DPO from the accepted parent.
set -euo pipefail
ROOT=$(cd "$(dirname "$BASH_SOURCE")/../.."; pwd); cd "$ROOT"
hash(){ sha256sum "$1" | awk '{print $1}'; }

PARENT=data/training/checkpoints/aria_tooluse_curve_sft_v5.tgz
PROBE=data/training/aria_tooluse_curve_v5_probe.jsonl
BASELINE=data/eval_reports/aria_tooluse_curve_v5_sft_rf4031_rescored.json
HELDOUT_BASELINE=data/eval_reports/aria_tooluse_curve_sft_v5_heldout_rf4031_rescored.json
DPO=data/training/aria_tooluse_resolution_boundary_dpo_v1.jsonl
MANIFEST=data/eval_reports/aria_tooluse_resolution_boundary_dpo_v1_manifest.json
EVAL=data/training/split_v1/eval.jsonl
TRAIN_PROOF=data/training/aria_tooluse_resolution_branch_expansion_v1.jsonl

test "$(hash "$PARENT")" = 99030c720f6db869f1fb4829d3389ee98f49cb67fea7b5169ca2f1b90417dac8
test "$(hash "$PROBE")" = 72b7eca2a90db4d1e3a6a4448a2d17f3b2a0dd165f82ab96fd975720d0227c5c
test "$(hash "$BASELINE")" = 679ce658e04282aea977b5d91c8f897f0aa9a296bba9aca4472703b679ccd49d
test "$(hash "$HELDOUT_BASELINE")" = 0c132d6a19f587960072bd8e423c9c9170595ce999c97f58c6113a3c66a4ac63
test "$(hash "$DPO")" = e998e1e7c77ba762ba75af55acd26df56b2c39325f43f04e51c7fa73e368532a
test "$(hash "$MANIFEST")" = e9761aa57543d302c9e4d34cf214e21c9d7fedc38b890636a55fd25be2e79d81
test "$(hash "$EVAL")" = d24be361fb30ff0e51272b2a7338be2924b8df5428d55a469f1c907bd28c3b00

REPO="$ROOT" ARIA_POD_CREATE_API=graphql ARIA_MAX_GPU_HOURLY_USD=1.60 \
  TRAINING_RECIPE_KIND=tooluse_dpo_boundary_accepted_continuation \
  FRESH_BASE=0 EXPECTED_DPO_PAIRS=32 DPO_GRAD_ACCUM=4 DPO_EXPECTED_UPDATES=4 \
  PROTECTED_DPO_AXES=tooluse_resolution \
  POD_RUNNER=scripts/train/pod_tooluse_dpo.sh \
  ADAPTER_LOCAL="$PARENT" ADAPTER_SHA256="$(hash "$PARENT")" \
  PROBE_LOCAL="$PROBE" PROBE_SHA256="$(hash "$PROBE")" \
  BASELINE_LOCAL="$BASELINE" BASELINE_SHA256="$(hash "$BASELINE")" \
  HELDOUT_BASELINE_LOCAL="$HELDOUT_BASELINE" HELDOUT_BASELINE_SHA256="$(hash "$HELDOUT_BASELINE")" \
  DPO_LOCAL="$DPO" DPO_SHA256="$(hash "$DPO")" \
  EVAL_LOCAL="$EVAL" TRAIN_PROOF="$TRAIN_PROOF" \
  REPORT_LOCAL=data/eval_reports/aria_tooluse_resolution_boundary_dpo_v1_eval.json \
  OUTPUT_LOCAL=data/training/checkpoints/aria_tooluse_resolution_boundary_dpo_v1.tgz \
  INTERMEDIATE_LOCAL=data/training/checkpoints/aria_tooluse_resolution_boundary_dpo_v1_failed_candidate.tgz \
  INTERMEDIATE_REMOTE=/workspace/eval/aria_tooluse_dpo_adapter.tgz \
  DIAGNOSTICS_LOCAL=data/eval_reports/aria_tooluse_resolution_boundary_dpo_v1_diagnostics.tgz \
  STATE_FILE=data/eval_reports/.tooluse_resolution_boundary_dpo_v1_pod_state \
  REMOTE_DPO_OUT=/workspace/checkpoints/aria_tooluse_resolution_boundary_dpo_v1 \
  MIN_CYCLE_DEADLINE=7200 CYCLE_DEADLINE=14400 \
  exec bash scripts/train/run_immutable_shell.sh scripts/train/run_tooluse_dpo.sh
