#!/usr/bin/env bash
# R-F4031 — correct the failed Phoenix adapter with four-axis genuine evidence.
set -euo pipefail
ROOT=$(cd "$(dirname "$BASH_SOURCE")/../.."; pwd); cd "$ROOT"
hash(){ sha256sum "$1" | awk '{print $1}'; }

ADAPTER=data/training/checkpoints/aria_tooluse_citation_phoenix_v3_failed_candidate.tgz
PROBE=data/training/aria_tooluse_curve_v5_probe.jsonl
BASELINE=data/eval_reports/aria_tooluse_curve_v5_sft_rf4031_rescored.json
HELDOUT_BASELINE=data/eval_reports/aria_tooluse_curve_sft_v5_heldout_rf4031_rescored.json
DPO=data/training/aria_tooluse_protected_dpo_v1.jsonl
EVAL=data/training/split_v1/eval.jsonl
TRAIN_PROOF=data/training/tooluse_novel_resolution_generation_queue.jsonl

test "$(hash "$ADAPTER")" = 9ad61c99ca0e0c735ff9346085d5d6491e6a21820a9219f71f15bb951a51a31a
test "$(hash "$PROBE")" = 72b7eca2a90db4d1e3a6a4448a2d17f3b2a0dd165f82ab96fd975720d0227c5c
test "$(hash "$BASELINE")" = 679ce658e04282aea977b5d91c8f897f0aa9a296bba9aca4472703b679ccd49d
test "$(hash "$HELDOUT_BASELINE")" = 0c132d6a19f587960072bd8e423c9c9170595ce999c97f58c6113a3c66a4ac63
test "$(hash "$DPO")" = c48d9130528fe375e258d28d5dc8ef3f58e543c26271529e4917fb099325459a
test "$(hash "$EVAL")" = d24be361fb30ff0e51272b2a7338be2924b8df5428d55a469f1c907bd28c3b00

REPO="$ROOT" FRESH_BASE=0 EXPECTED_DPO_PAIRS=20 \
  PROTECTED_DPO_AXES=tooluse_adverse,tooluse_contradiction,tooluse_resolution,tooluse_news_impact \
  POD_RUNNER=scripts/train/pod_tooluse_dpo.sh \
  ADAPTER_LOCAL="$ADAPTER" ADAPTER_SHA256="$(hash "$ADAPTER")" \
  PROBE_LOCAL="$PROBE" PROBE_SHA256="$(hash "$PROBE")" \
  BASELINE_LOCAL="$BASELINE" BASELINE_SHA256="$(hash "$BASELINE")" \
  HELDOUT_BASELINE_LOCAL="$HELDOUT_BASELINE" HELDOUT_BASELINE_SHA256="$(hash "$HELDOUT_BASELINE")" \
  DPO_LOCAL="$DPO" DPO_SHA256="$(hash "$DPO")" \
  EVAL_LOCAL="$EVAL" TRAIN_PROOF="$TRAIN_PROOF" \
  REPORT_LOCAL=data/eval_reports/aria_tooluse_protected_dpo_v1_eval.json \
  OUTPUT_LOCAL=data/training/checkpoints/aria_tooluse_protected_dpo_v1.tgz \
  INTERMEDIATE_LOCAL=data/training/checkpoints/aria_tooluse_protected_dpo_v1_failed_candidate.tgz \
  INTERMEDIATE_REMOTE=/workspace/eval/aria_tooluse_dpo_adapter.tgz \
  DIAGNOSTICS_LOCAL=data/eval_reports/aria_tooluse_protected_dpo_v1_diagnostics.tgz \
  STATE_FILE=data/eval_reports/.tooluse_protected_dpo_v1_pod_state \
  REMOTE_DPO_OUT=/workspace/checkpoints/aria_tooluse_protected_dpo_v1 \
  MIN_CYCLE_DEADLINE=7200 CYCLE_DEADLINE=14400 \
  exec bash scripts/train/run_immutable_shell.sh scripts/train/run_tooluse_dpo.sh
