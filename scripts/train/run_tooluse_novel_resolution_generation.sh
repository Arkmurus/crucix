#!/usr/bin/env bash
# R-F4027 — measure the failed Phoenix adapter on novel live resolution traces.
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.."; pwd)
cd "$ROOT"
hash(){ sha256sum "$1" | awk '{print $1}'; }

QUEUE=data/training/tooluse_novel_resolution_generation_queue.jsonl
ADAPTER=data/training/checkpoints/aria_tooluse_citation_phoenix_v3_failed_candidate.tgz
EVAL=data/training/split_v1/eval.jsonl
GOLDEN=data/eval_frozen/aria_eval_500q.jsonl

test "$(hash "$QUEUE")" = f90af8e61cda160c98214ae45230f504360f31a6560a045bf035b3334157ae74
test "$(hash "$ADAPTER")" = 9ad61c99ca0e0c735ff9346085d5d6491e6a21820a9219f71f15bb951a51a31a
test "$(hash "$EVAL")" = 8d3d33eca57caccbea25d0d5b499cda0b5aa9e5dbc30ba823d55b39ead573176
test "$(hash "$GOLDEN")" = 4af4f76dbaf8fa3be341b97c94c5d654ef0e354704b974044fc8e64ddcdd296c

QUEUE="$QUEUE" EVAL_LOCAL="$EVAL" GOLDEN="$GOLDEN" \
  ADAPTER_LOCAL="$ADAPTER" \
  REPORT_LOCAL=data/eval_reports/aria_tooluse_novel_resolution_generations.json \
  STATE_FILE=data/eval_reports/.tooluse_novel_resolution_generation_pod_state \
  exec bash scripts/train/run_tooluse_generation.sh
