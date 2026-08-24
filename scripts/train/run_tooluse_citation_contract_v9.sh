#!/usr/bin/env bash
# R-F3923 — reinforced citation-contract continuation after repeated v8 calibration regressions.
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")"; pwd)
REPO=$(cd "$SCRIPT_DIR/../.."; pwd)
cd "$REPO"

hash(){ sha256sum "$1" | awk '{print $1}'; }

SFT=data/training/aria_tooluse_citation_contract_v9.jsonl
PARENT=data/training/checkpoints/aria_tooluse_curve_sft_v5.tgz
PROBE=data/training/aria_tooluse_curve_v5_probe.jsonl
BASELINE=data/eval_reports/aria_tooluse_curve_v5_sft_rescored.json
DPO=data/training/aria_tooluse_curve_v5_dpo.jsonl
EVAL=data/training/split_v1/eval.jsonl
GOLDEN=data/eval_frozen/aria_eval_500q.jsonl

test "$(hash "$SFT")" = 527455a979d7b69f0ea22601881de41717714937fb320ff0032229d8aced40e8
test "$(hash "$PARENT")" = 99030c720f6db869f1fb4829d3389ee98f49cb67fea7b5169ca2f1b90417dac8
test "$(hash "$PROBE")" = 72b7eca2a90db4d1e3a6a4448a2d17f3b2a0dd165f82ab96fd975720d0227c5c
test "$(hash "$BASELINE")" = ab029377d8f1e1d287c4e186bb75517ded8a200045de6deb1cb7f3ef3d6f85af
test "$(hash "$DPO")" = 9425cb41de48d4e54522663174fd34d2fd1042bc26c8fd5466a113e848f6b2bd
test "$(hash "$EVAL")" = 8d3d33eca57caccbea25d0d5b499cda0b5aa9e5dbc30ba823d55b39ead573176

FRESH_BASE=0 EXPECTED_DPO_PAIRS=53 \
  HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 \
  POD_RUNNER=scripts/train/pod_tooluse_sft_continue.sh \
  ADAPTER_LOCAL="$PARENT" ADAPTER_SHA256="$(hash "$PARENT")" \
  SFT_LOCAL="$SFT" SFT_SHA256="$(hash "$SFT")" \
  PROBE_LOCAL="$PROBE" PROBE_SHA256="$(hash "$PROBE")" \
  BASELINE_LOCAL="$BASELINE" BASELINE_SHA256="$(hash "$BASELINE")" \
  DPO_LOCAL="$DPO" DPO_SHA256="$(hash "$DPO")" \
  EVAL_LOCAL="$EVAL" TRAIN_PROOF="$SFT" GOLDEN="$GOLDEN" \
  REPORT_LOCAL=data/eval_reports/aria_tooluse_citation_contract_v9_eval.json \
  OUTPUT_LOCAL=data/training/checkpoints/aria_tooluse_citation_contract_v9.tgz \
  DIAGNOSTICS_LOCAL=data/eval_reports/aria_tooluse_citation_contract_v9_diagnostics.tgz \
  STATE_FILE=data/eval_reports/.tooluse_citation_contract_v9_pod_state \
  REMOTE_DPO_OUT=/workspace/checkpoints/aria_tooluse_citation_contract_v9 \
  CYCLE_DEADLINE=14400 \
  exec bash scripts/train/run_tooluse_dpo.sh
