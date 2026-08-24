#!/usr/bin/env bash
# R-F4243 — length-controlled resolution DPO from the same accepted parent.
#
# Identical to run_tooluse_resolution_boundary_dpo_v1.sh in every pin except the
# preference file. That is deliberate: parent adapter, probe, both baselines and
# the held-out eval are byte-identical, so any movement is attributable to the
# curriculum change and nothing else. v1 scored 157/168 with resolution 11/16;
# this asks whether removing the length confound (32 -> 64 pairs, every pair
# gaining a twin whose rejection sits on the other side of the length divide)
# moves the axis the nine previous candidates could not.
#
# R-F4244 — both baselines are now the CURRENT-scorer rescores. The previously
# pinned ones carried no scorer_version and read 155/168 where the same answers
# score 161/168 today, handing every candidate a +6 aggregate and +2
# protected-axis head start. The gate refuses that comparison now; these pins
# are what let it run at all. The calibration probe is unchanged at 17/30 — the
# bias was in the held-out set only.
set -euo pipefail
ROOT=$(cd "$(dirname "$BASH_SOURCE")/../.."; pwd); cd "$ROOT"
hash(){ sha256sum "$1" | awk '{print $1}'; }

PARENT=data/training/checkpoints/aria_tooluse_curve_sft_v5.tgz
PROBE=data/training/aria_tooluse_curve_v5_probe.jsonl
BASELINE=data/eval_reports/aria_tooluse_curve_v5_sft_rf4160_rescored.json
HELDOUT_BASELINE=data/eval_reports/aria_tooluse_curve_sft_v5_heldout_rf4160_rescored.json
DPO=data/training/aria_tooluse_resolution_length_control_v1.jsonl
MANIFEST=data/eval_reports/aria_tooluse_resolution_length_control_v1_manifest.json
EVAL=data/training/split_v1/eval.jsonl
TRAIN_PROOF=data/training/aria_tooluse_resolution_branch_expansion_v1.jsonl

test "$(hash "$PARENT")" = 99030c720f6db869f1fb4829d3389ee98f49cb67fea7b5169ca2f1b90417dac8
test "$(hash "$PROBE")" = 72b7eca2a90db4d1e3a6a4448a2d17f3b2a0dd165f82ab96fd975720d0227c5c
test "$(hash "$BASELINE")" = 13bb7c703a5f4b0dfcdfb63853ab5c2ff87de592c939a62841e7368d310e012e
test "$(hash "$HELDOUT_BASELINE")" = 5c84628ae9466ff3138f4b3290ffa34fc5e48bbae807bc82d9c7e0eb80a76cfd
test "$(hash "$DPO")" = 1fa66bb61a5712306c73ce32cd291e850995ce1b41dc22532bb7b99765ae451a
test "$(hash "$MANIFEST")" = 08c9554039d0c3beff0dea0f54fc059350f839a9cb19e6c515a293c6e19a0ed1
test "$(hash "$EVAL")" = 8d3d33eca57caccbea25d0d5b499cda0b5aa9e5dbc30ba823d55b39ead573176

REPO="$ROOT" ARIA_POD_CREATE_API=graphql ARIA_MAX_GPU_HOURLY_USD=1.60 \
  TRAINING_RECIPE_KIND=tooluse_dpo_length_controlled_accepted_continuation \
  FRESH_BASE=0 EXPECTED_DPO_PAIRS=64 DPO_GRAD_ACCUM=4 DPO_EXPECTED_UPDATES=8 \
  PROTECTED_DPO_AXES=tooluse_resolution \
  POD_RUNNER=scripts/train/pod_tooluse_dpo.sh \
  ADAPTER_LOCAL="$PARENT" ADAPTER_SHA256="$(hash "$PARENT")" \
  PROBE_LOCAL="$PROBE" PROBE_SHA256="$(hash "$PROBE")" \
  BASELINE_LOCAL="$BASELINE" BASELINE_SHA256="$(hash "$BASELINE")" \
  HELDOUT_BASELINE_LOCAL="$HELDOUT_BASELINE" HELDOUT_BASELINE_SHA256="$(hash "$HELDOUT_BASELINE")" \
  DPO_LOCAL="$DPO" DPO_SHA256="$(hash "$DPO")" \
  EVAL_LOCAL="$EVAL" TRAIN_PROOF="$TRAIN_PROOF" \
  REPORT_LOCAL=data/eval_reports/aria_tooluse_resolution_length_control_v1_eval.json \
  OUTPUT_LOCAL=data/training/checkpoints/aria_tooluse_resolution_length_control_v1.tgz \
  INTERMEDIATE_LOCAL=data/training/checkpoints/aria_tooluse_resolution_length_control_v1_failed_candidate.tgz \
  INTERMEDIATE_REMOTE=/workspace/eval/aria_tooluse_dpo_adapter.tgz \
  DIAGNOSTICS_LOCAL=data/eval_reports/aria_tooluse_resolution_length_control_v1_diagnostics.tgz \
  STATE_FILE=data/eval_reports/.tooluse_resolution_length_control_v1_pod_state \
  REMOTE_DPO_OUT=/workspace/checkpoints/aria_tooluse_resolution_length_control_v1 \
  MIN_CYCLE_DEADLINE=7200 CYCLE_DEADLINE=14400 \
  exec bash scripts/train/run_immutable_shell.sh scripts/train/run_tooluse_dpo.sh
