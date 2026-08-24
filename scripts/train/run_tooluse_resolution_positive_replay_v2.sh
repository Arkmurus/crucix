#!/usr/bin/env bash
# R-F4084 — size-adjusted full-parent resolution replay; never auto-promotable.
set -euo pipefail
ROOT=$(cd "$(dirname "$BASH_SOURCE")/../.."; pwd); cd "$ROOT"
hash(){ sha256sum "$1" | awk '{print $1}'; }

SFT=data/training/aria_tooluse_resolution_positive_replay_v1.jsonl
PARENT=data/training/checkpoints/aria_tooluse_protected_positive_v1_failed_candidate.tgz
PROBE=data/training/aria_tooluse_curve_v5_probe.jsonl
BASELINE=data/eval_reports/rf4054_recovery_baseline/aria_tooluse_sft_child_probe.json
DPO=data/training/aria_tooluse_protected_dpo_v1.jsonl
EVAL=data/training/split_v1/eval.jsonl
GOLDEN=data/eval_frozen/aria_eval_500q.jsonl

test "$(hash "$SFT")" = c8bcd4487281882f0221cf41113d50d053a713dfd055bc0b3c1ccc6726d81ede
test "$(hash "$PARENT")" = 223ba1dc99d0e65aafdfa3f5190d57e0e8dfdd4013f9fab5be3994af63384998
test "$(hash "$PROBE")" = 72b7eca2a90db4d1e3a6a4448a2d17f3b2a0dd165f82ab96fd975720d0227c5c
test "$(hash "$BASELINE")" = ad4f6dadef560f4fafbfd47312facd2cadf6cf8955fc7486b8f1a6c1f06703da
test "$(hash "$DPO")" = 8637725129c89c7529741a7beda792d0c7ef0c0caff12204efd858790c1b4e46
test "$(hash "$EVAL")" = 8d3d33eca57caccbea25d0d5b499cda0b5aa9e5dbc30ba823d55b39ead573176

REPO="$ROOT" ARIA_POD_CREATE_API=graphql ARIA_MAX_GPU_HOURLY_USD=1.60 \
  FRESH_BASE=0 EXPECTED_DPO_PAIRS=20 SFT_LR=1e-6 \
  TRAINING_RECIPE_KIND=tooluse_positive_sft_scaled_diagnostic_continuation \
  POD_RUNNER=scripts/train/pod_tooluse_sft_continue.sh \
  ADAPTER_LOCAL="$PARENT" ADAPTER_SHA256="$(hash "$PARENT")" \
  SFT_LOCAL="$SFT" SFT_SHA256="$(hash "$SFT")" \
  PROBE_LOCAL="$PROBE" PROBE_SHA256="$(hash "$PROBE")" \
  BASELINE_LOCAL="$BASELINE" BASELINE_SHA256="$(hash "$BASELINE")" \
  DPO_LOCAL="$DPO" DPO_SHA256="$(hash "$DPO")" \
  EVAL_LOCAL="$EVAL" TRAIN_PROOF="$SFT" GOLDEN="$GOLDEN" \
  REPORT_LOCAL=data/eval_reports/aria_tooluse_resolution_positive_replay_v2_eval.json \
  OUTPUT_LOCAL=data/training/checkpoints/aria_tooluse_resolution_positive_replay_v2_failed_candidate.tgz \
  INTERMEDIATE_LOCAL=data/training/checkpoints/aria_tooluse_resolution_positive_replay_v2_failed_candidate.tgz \
  INTERMEDIATE_REMOTE=/workspace/eval/aria_tooluse_dpo_adapter.tgz \
  DIAGNOSTICS_LOCAL=data/eval_reports/aria_tooluse_resolution_positive_replay_v2_diagnostics.tgz \
  STATE_FILE=data/eval_reports/.tooluse_resolution_positive_replay_v2_pod_state \
  REMOTE_DPO_OUT=/workspace/checkpoints/aria_tooluse_resolution_positive_replay_v2 \
  MIN_CYCLE_DEADLINE=7200 CYCLE_DEADLINE=14400 \
  exec bash scripts/train/run_immutable_shell.sh scripts/train/run_tooluse_dpo.sh
