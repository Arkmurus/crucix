#!/usr/bin/env bash
# R-F4059 — measure the protected-positive candidate on novel resolution traces.
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.."; pwd)
cd "$ROOT"
hash(){ sha256sum "$1" | awk '{print $1}'; }

QUEUE=data/training/tooluse_novel_resolution_generation_queue.jsonl
ADAPTER=data/training/checkpoints/aria_tooluse_protected_positive_v1_failed_candidate.tgz
EVAL=data/training/split_v1/eval.jsonl
GOLDEN=data/eval_frozen/aria_eval_500q.jsonl

test "$(hash "$QUEUE")" = df0f3671a42ede8f41d60db98f6f56a2063dcebcab4017aee6fa7585e5faf9f7
test "$(hash "$ADAPTER")" = 223ba1dc99d0e65aafdfa3f5190d57e0e8dfdd4013f9fab5be3994af63384998
test "$(hash "$EVAL")" = d24be361fb30ff0e51272b2a7338be2924b8df5428d55a469f1c907bd28c3b00
test "$(hash "$GOLDEN")" = 4af4f76dbaf8fa3be341b97c94c5d654ef0e354704b974044fc8e64ddcdd296c

REPO="$ROOT" ARIA_POD_CREATE_API=graphql ARIA_MAX_GPU_HOURLY_USD=1.60 \
  QUEUE="$QUEUE" EVAL_LOCAL="$EVAL" GOLDEN="$GOLDEN" \
  ADAPTER_LOCAL="$ADAPTER" \
  REPORT_LOCAL=data/eval_reports/aria_tooluse_protected_positive_v1_resolution_generations.json \
  STATE_FILE=data/eval_reports/.tooluse_protected_positive_v1_resolution_generation_pod_state \
  exec bash scripts/train/run_tooluse_generation.sh
