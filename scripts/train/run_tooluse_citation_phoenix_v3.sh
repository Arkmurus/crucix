#!/usr/bin/env bash
# R-F3978 — retention-safe correction of four measured adverse-denial failures.
set -euo pipefail
ROOT=$(cd "$(dirname "$BASH_SOURCE")/../.."; pwd); cd "$ROOT"
hash(){ sha256sum "$1" | awk '{print $1}'; }

ADAPTER=data/training/checkpoints/aria_tooluse_curve_sft_v5.tgz
PROBE=data/training/aria_tooluse_curve_v5_probe.jsonl
BASELINE=data/eval_reports/aria_tooluse_curve_v5_sft_rescored.json
DPO=data/training/aria_tooluse_citation_phoenix_v3_disjoint_dpo.jsonl
EVAL=data/training/split_v1/eval.jsonl
HELDOUT_BASELINE=data/eval_reports/aria_tooluse_curve_sft_v5_heldout_rescored.json
TRAIN_PROOF=data/training/tooluse_citation_phoenix_v2_generation_queue.jsonl

test "$(hash "$ADAPTER")" = 99030c720f6db869f1fb4829d3389ee98f49cb67fea7b5169ca2f1b90417dac8
test "$(hash "$PROBE")" = 72b7eca2a90db4d1e3a6a4448a2d17f3b2a0dd165f82ab96fd975720d0227c5c
test "$(hash "$BASELINE")" = 78fd7ca8751f1869b9165c35fb0dc984173271a7bd736ebc892fbd0e0167afa3
test "$(hash "$DPO")" = 7bb73249af5d460227f3c4d85d37d599a53a328e7b0773c468557f39e3c27fe3
test "$(hash "$EVAL")" = d24be361fb30ff0e51272b2a7338be2924b8df5428d55a469f1c907bd28c3b00
test "$(hash "$HELDOUT_BASELINE")" = 176f0c354fe4bc3d18169db84f47cac5331779dc5a360e8a9690364611f5e36e

FRESH_BASE=0 EXPECTED_DPO_PAIRS=41 \
  POD_RUNNER=scripts/train/pod_tooluse_dpo.sh \
  ADAPTER_LOCAL="$ADAPTER" ADAPTER_SHA256="$(hash "$ADAPTER")" \
  PROBE_LOCAL="$PROBE" PROBE_SHA256="$(hash "$PROBE")" \
  BASELINE_LOCAL="$BASELINE" BASELINE_SHA256="$(hash "$BASELINE")" \
  HELDOUT_BASELINE_LOCAL="$HELDOUT_BASELINE" HELDOUT_BASELINE_SHA256="$(hash "$HELDOUT_BASELINE")" \
  DPO_LOCAL="$DPO" DPO_SHA256="$(hash "$DPO")" \
  EVAL_LOCAL="$EVAL" TRAIN_PROOF="$TRAIN_PROOF" \
  REPORT_LOCAL=data/eval_reports/aria_tooluse_citation_phoenix_v3_eval.json \
  OUTPUT_LOCAL=data/training/checkpoints/aria_tooluse_citation_phoenix_v3.tgz \
  INTERMEDIATE_LOCAL=data/training/checkpoints/aria_tooluse_citation_phoenix_v3_failed_candidate.tgz \
  INTERMEDIATE_REMOTE=/workspace/eval/aria_tooluse_dpo_adapter.tgz \
  DIAGNOSTICS_LOCAL=data/eval_reports/aria_tooluse_citation_phoenix_v3_diagnostics.tgz \
  STATE_FILE=data/eval_reports/.tooluse_citation_phoenix_v3_pod_state \
  REMOTE_DPO_OUT=/workspace/checkpoints/aria_tooluse_citation_phoenix_v3 \
  MIN_CYCLE_DEADLINE=7200 CYCLE_DEADLINE=14400 \
  exec bash scripts/train/run_tooluse_dpo.sh
