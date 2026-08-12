#!/usr/bin/env bash
# R-F3913 — guarded positive-SFT continuation for the citation-contract v8 corpus.
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")"; pwd)
REPO=$(cd "$SCRIPT_DIR/../.."; pwd)
cd "$REPO"

hash(){ sha256sum "$1" | awk '{print $1}'; }

SFT=data/training/aria_tooluse_citation_contract_v8.jsonl
PARENT=data/training/checkpoints/aria_tooluse_curve_sft_v5.tgz
PROBE=data/training/aria_tooluse_curve_v5_probe.jsonl
BASELINE=data/eval_reports/aria_tooluse_curve_v5_sft_rescored.json
DPO=data/training/aria_tooluse_curve_v5_dpo.jsonl
# split_v1 and the local split_v2 copy have the same accepted manifest hash;
# split_v1 is tracked, so a fresh clone can reproduce this launch.
EVAL=data/training/split_v1/eval.jsonl
GOLDEN=data/eval_frozen/aria_eval_500q.jsonl

test "$(hash "$SFT")" = f5bdbdb419cff3fa9cf58b85d217124e05820026fe6c8a49c81d2d83c51610be
test "$(hash "$PARENT")" = 99030c720f6db869f1fb4829d3389ee98f49cb67fea7b5169ca2f1b90417dac8
test "$(hash "$PROBE")" = 72b7eca2a90db4d1e3a6a4448a2d17f3b2a0dd165f82ab96fd975720d0227c5c
test "$(hash "$BASELINE")" = 78fd7ca8751f1869b9165c35fb0dc984173271a7bd736ebc892fbd0e0167afa3
test "$(hash "$DPO")" = 9425cb41de48d4e54522663174fd34d2fd1042bc26c8fd5466a113e848f6b2bd
test "$(hash "$EVAL")" = d24be361fb30ff0e51272b2a7338be2924b8df5428d55a469f1c907bd28c3b00

FRESH_BASE=0 EXPECTED_DPO_PAIRS=53 \
  HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 \
  POD_RUNNER=scripts/train/pod_tooluse_sft_continue.sh \
  ADAPTER_LOCAL="$PARENT" ADAPTER_SHA256="$(hash "$PARENT")" \
  SFT_LOCAL="$SFT" SFT_SHA256="$(hash "$SFT")" \
  PROBE_LOCAL="$PROBE" PROBE_SHA256="$(hash "$PROBE")" \
  BASELINE_LOCAL="$BASELINE" BASELINE_SHA256="$(hash "$BASELINE")" \
  DPO_LOCAL="$DPO" DPO_SHA256="$(hash "$DPO")" \
  EVAL_LOCAL="$EVAL" TRAIN_PROOF="$SFT" GOLDEN="$GOLDEN" \
  REPORT_LOCAL=data/eval_reports/aria_tooluse_citation_contract_v8_eval.json \
  OUTPUT_LOCAL=data/training/checkpoints/aria_tooluse_citation_contract_v8.tgz \
  DIAGNOSTICS_LOCAL=data/eval_reports/aria_tooluse_citation_contract_v8_diagnostics.tgz \
  STATE_FILE=data/eval_reports/.tooluse_citation_contract_v8_pod_state \
  REMOTE_DPO_OUT=/workspace/checkpoints/aria_tooluse_citation_contract_v8 \
  CYCLE_DEADLINE=14400 \
  bash scripts/train/run_tooluse_dpo.sh
