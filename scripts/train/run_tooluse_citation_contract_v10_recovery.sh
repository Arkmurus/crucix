#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd "$(dirname "$BASH_SOURCE")/../.."; pwd); cd "$ROOT"
hash(){ sha256sum "$1" | awk '{print $1}'; }
ADAPTER=data/training/checkpoints/aria_tooluse_citation_contract_v10_calibration_child.tgz
PROBE=data/training/aria_tooluse_curve_v5_probe.jsonl
BASELINE=data/eval_reports/aria_tooluse_curve_v5_sft_rescored.json
DPO=data/training/aria_tooluse_curve_v5_dpo.jsonl
EVAL=data/training/split_v1/eval.jsonl
test "$(hash "$ADAPTER")" = 587a7a1f823c8d70326ab9683e65998579c089fbf0255ba9377be30e7fc55850
FRESH_BASE=0 EXPECTED_DPO_PAIRS=53 POD_RUNNER=scripts/train/pod_tooluse_calibration_recovery.sh ADAPTER_LOCAL="$ADAPTER" ADAPTER_SHA256="$(hash "$ADAPTER")" PROBE_LOCAL="$PROBE" PROBE_SHA256="$(hash "$PROBE")" BASELINE_LOCAL="$BASELINE" BASELINE_SHA256="$(hash "$BASELINE")" DPO_LOCAL="$DPO" DPO_SHA256="$(hash "$DPO")" EVAL_LOCAL="$EVAL" TRAIN_PROOF=data/training/aria_tooluse_citation_contract_v10.jsonl REPORT_LOCAL=data/eval_reports/aria_tooluse_citation_contract_v10_eval.json OUTPUT_LOCAL=data/training/checkpoints/aria_tooluse_citation_contract_v10.tgz DIAGNOSTICS_LOCAL=data/eval_reports/aria_tooluse_citation_contract_v10_recovery_diagnostics.tgz STATE_FILE=data/eval_reports/.tooluse_citation_contract_v10_recovery_pod_state REMOTE_DPO_OUT=/workspace/checkpoints/aria_tooluse_citation_contract_v10 MIN_CYCLE_DEADLINE=7200 CYCLE_DEADLINE=14400 exec bash scripts/train/run_tooluse_dpo.sh
