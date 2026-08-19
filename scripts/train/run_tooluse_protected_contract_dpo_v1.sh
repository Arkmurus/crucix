#!/usr/bin/env bash
# R-F4165 — one bounded protected-contract DPO epoch from the alpha=0.25 frontier.
set -euo pipefail
ROOT=$(cd "$(dirname "$BASH_SOURCE")/../.."; pwd); cd "$ROOT"
hash(){ sha256sum "$1" | awk '{print $1}'; }

PARENT=data/training/checkpoints/aria_tooluse_lora_interpolation_v1_alpha_025.tgz
DPO=data/training/aria_tooluse_protected_contract_dpo_v1.jsonl
MANIFEST=data/eval_reports/aria_tooluse_protected_contract_dpo_v1_manifest.json
BASELINE=data/eval_reports/aria_tooluse_lora_interpolation_v1_alpha_025.json
EVAL=data/training/split_v1/eval.jsonl
GOLDEN=data/eval_frozen/aria_eval_500q.jsonl
TRAIN_PROOF=data/training/aria_tooluse_resolution_branch_expansion_v1.jsonl

test "$(hash "$PARENT")" = 5ba78e6035e304ed266f632b0ac5181c230040d1863c107e05f1e53ae51b1f20
test "$(hash "$DPO")" = 3c75636cfbac34acbd84351188db11bf4c1579589d24cea9e00a21952498ed1f
test "$(hash "$MANIFEST")" = 2f3a1c0e95d767a006a0bdb928e1a7a88eff41c6144a89bc4503e2a5fa54f9c9
test "$(hash "$BASELINE")" = f4369e1182a18eb898aed5119d465641f7f837ebe91078b875868835c1b917ca
test "$(hash "$EVAL")" = d24be361fb30ff0e51272b2a7338be2924b8df5428d55a469f1c907bd28c3b00
test "$(hash "$GOLDEN")" = 4af4f76dbaf8fa3be341b97c94c5d654ef0e354704b974044fc8e64ddcdd296c
test "$(hash "$TRAIN_PROOF")" = e87b085c3fae6c10b4d0afdca252979cc63ab10d21819393c2659abad68715c2

REPO="$ROOT" ARIA_POD_CREATE_API=graphql ARIA_MAX_GPU_HOURLY_USD=1.60 \
  TRAINING_RECIPE_KIND=tooluse_dpo_protected_frontier_continuation \
  FRESH_BASE=0 EXPECTED_DPO_PAIRS=47 DPO_GRAD_ACCUM=4 DPO_EXPECTED_UPDATES=6 \
  PROTECTED_DPO_AXES=tooluse_challenge,tooluse_multihop,tooluse_resolution \
  POD_RUNNER=scripts/train/pod_tooluse_dpo.sh \
  ADAPTER_LOCAL="$PARENT" ADAPTER_SHA256="$(hash "$PARENT")" \
  HELDOUT_BASELINE_LOCAL="$BASELINE" HELDOUT_BASELINE_SHA256="$(hash "$BASELINE")" \
  DPO_LOCAL="$DPO" DPO_SHA256="$(hash "$DPO")" \
  EVAL_LOCAL="$EVAL" TRAIN_PROOF="$TRAIN_PROOF" GOLDEN="$GOLDEN" \
  REPORT_LOCAL=data/eval_reports/aria_tooluse_protected_contract_dpo_v1_eval.json \
  OUTPUT_LOCAL=data/training/checkpoints/aria_tooluse_protected_contract_dpo_v1.tgz \
  INTERMEDIATE_LOCAL=data/training/checkpoints/aria_tooluse_protected_contract_dpo_v1_failed_candidate.tgz \
  INTERMEDIATE_REMOTE=/workspace/eval/aria_tooluse_dpo_adapter.tgz \
  DIAGNOSTICS_LOCAL=data/eval_reports/aria_tooluse_protected_contract_dpo_v1_diagnostics.tgz \
  STATE_FILE=data/eval_reports/.tooluse_protected_contract_dpo_v1_pod_state \
  REMOTE_DPO_OUT=/workspace/checkpoints/aria_tooluse_protected_contract_dpo_v1 \
  MIN_CYCLE_DEADLINE=7200 CYCLE_DEADLINE=14400 \
  exec bash scripts/train/run_immutable_shell.sh scripts/train/run_tooluse_dpo.sh
