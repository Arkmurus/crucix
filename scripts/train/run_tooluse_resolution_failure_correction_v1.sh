#!/usr/bin/env bash
# R-F4123 — measured resolution correction from the accepted 155/168 parent.
set -euo pipefail
ROOT=$(cd "$(dirname "$BASH_SOURCE")/../.."; pwd); cd "$ROOT"
hash(){ sha256sum "$1" | awk '{print $1}'; }

SFT=data/training/aria_tooluse_resolution_failure_correction_v1.jsonl
PARENT=data/training/checkpoints/aria_tooluse_curve_sft_v5.tgz
PROBE=data/training/aria_tooluse_curve_v5_probe.jsonl
BASELINE=data/eval_reports/aria_tooluse_curve_v5_sft_rf4031_rescored.json
HELDOUT_BASELINE=data/eval_reports/aria_tooluse_curve_sft_v5_heldout_rf4031_rescored.json
DPO=data/training/aria_tooluse_protected_dpo_v1.jsonl
EVAL=data/training/split_v1/eval.jsonl
GOLDEN=data/eval_frozen/aria_eval_500q.jsonl

test "$(hash "$SFT")" = d98f8de3307120e26ae80cbd2e17c572c9b7a7b5a8fdec2a73aba6d894357e1c
test "$(hash "$PARENT")" = 99030c720f6db869f1fb4829d3389ee98f49cb67fea7b5169ca2f1b90417dac8
test "$(hash "$PROBE")" = 72b7eca2a90db4d1e3a6a4448a2d17f3b2a0dd165f82ab96fd975720d0227c5c
test "$(hash "$BASELINE")" = 2ef72c4bc743b4366eb8f7b3e9f74a6491e069953e4fefbffae72ad21689510e
test "$(hash "$HELDOUT_BASELINE")" = c4d3fd2d51a26e46bef17d3a85a36190f384fff0782ef312faf0da5015fcd26a
test "$(hash "$DPO")" = 8637725129c89c7529741a7beda792d0c7ef0c0caff12204efd858790c1b4e46
test "$(hash "$EVAL")" = 8d3d33eca57caccbea25d0d5b499cda0b5aa9e5dbc30ba823d55b39ead573176

REPO="$ROOT" ARIA_POD_CREATE_API=graphql ARIA_MAX_GPU_HOURLY_USD=1.60 \
  FRESH_BASE=0 EXPECTED_DPO_PAIRS=20 SFT_LR=1e-6 \
  TRAINING_RECIPE_KIND=tooluse_positive_sft_scaled_continuation \
  POD_RUNNER=scripts/train/pod_tooluse_sft_continue.sh \
  ADAPTER_LOCAL="$PARENT" ADAPTER_SHA256="$(hash "$PARENT")" \
  SFT_LOCAL="$SFT" SFT_SHA256="$(hash "$SFT")" \
  PROBE_LOCAL="$PROBE" PROBE_SHA256="$(hash "$PROBE")" \
  BASELINE_LOCAL="$BASELINE" BASELINE_SHA256="$(hash "$BASELINE")" \
  HELDOUT_BASELINE_LOCAL="$HELDOUT_BASELINE" HELDOUT_BASELINE_SHA256="$(hash "$HELDOUT_BASELINE")" \
  DPO_LOCAL="$DPO" DPO_SHA256="$(hash "$DPO")" \
  EVAL_LOCAL="$EVAL" TRAIN_PROOF="$SFT" GOLDEN="$GOLDEN" \
  REPORT_LOCAL=data/eval_reports/aria_tooluse_resolution_failure_correction_v1_eval.json \
  OUTPUT_LOCAL=data/training/checkpoints/aria_tooluse_resolution_failure_correction_v1.tgz \
  INTERMEDIATE_LOCAL=data/training/checkpoints/aria_tooluse_resolution_failure_correction_v1_failed_candidate.tgz \
  INTERMEDIATE_REMOTE=/workspace/eval/aria_tooluse_dpo_adapter.tgz \
  DIAGNOSTICS_LOCAL=data/eval_reports/aria_tooluse_resolution_failure_correction_v1_diagnostics.tgz \
  STATE_FILE=data/eval_reports/.tooluse_resolution_failure_correction_v1_pod_state \
  REMOTE_DPO_OUT=/workspace/checkpoints/aria_tooluse_resolution_failure_correction_v1 \
  MIN_CYCLE_DEADLINE=7200 CYCLE_DEADLINE=14400 \
  exec bash scripts/train/run_immutable_shell.sh scripts/train/run_tooluse_dpo.sh
