#!/usr/bin/env bash
# R-F3949 - harvest genuine train-split failures from the accepted v5 parent.
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.."; pwd)
cd "$ROOT"
hash(){ sha256sum "$1" | awk '{print $1}'; }

QUEUE=data/training/tooluse_citation_phoenix_generation_queue.jsonl
ADAPTER=data/training/checkpoints/aria_tooluse_curve_sft_v5.tgz
EVAL=data/training/split_v1/eval.jsonl
GOLDEN=data/eval_frozen/aria_eval_500q.jsonl

test "$(hash "$QUEUE")" = 6ba510c2489e046e5a1488352fd38fc6dc05b7f85c613891a80f91346c9c754b
test "$(hash "$ADAPTER")" = 99030c720f6db869f1fb4829d3389ee98f49cb67fea7b5169ca2f1b90417dac8
test "$(hash "$EVAL")" = 8d3d33eca57caccbea25d0d5b499cda0b5aa9e5dbc30ba823d55b39ead573176
test "$(hash "$GOLDEN")" = 4af4f76dbaf8fa3be341b97c94c5d654ef0e354704b974044fc8e64ddcdd296c

QUEUE="$QUEUE" EVAL_LOCAL="$EVAL" GOLDEN="$GOLDEN" \
  ADAPTER_LOCAL="$ADAPTER" \
  REPORT_LOCAL=data/eval_reports/aria_tooluse_citation_phoenix_generations.json \
  STATE_FILE=data/eval_reports/.tooluse_citation_phoenix_generation_pod_state \
  exec bash scripts/train/run_tooluse_generation.sh
