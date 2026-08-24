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
test "$(hash "$BASELINE")" = 2ef72c4bc743b4366eb8f7b3e9f74a6491e069953e4fefbffae72ad21689510e
test "$(hash "$HELDOUT_BASELINE")" = c4d3fd2d51a26e46bef17d3a85a36190f384fff0782ef312faf0da5015fcd26a
test "$(hash "$DPO")" = e998e1e7c77ba762ba75af55acd26df56b2c39325f43f04e51c7fa73e368532a
test "$(hash "$MANIFEST")" = d71dc34ba8f0328e4363f6e9dd03caabe2545a752dc038f7264a1233e780f8f2
test "$(hash "$EVAL")" = 8d3d33eca57caccbea25d0d5b499cda0b5aa9e5dbc30ba823d55b39ead573176

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
