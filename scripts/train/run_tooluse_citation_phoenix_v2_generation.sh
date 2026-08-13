#!/usr/bin/env bash
# R-F3956 - harvest novel citation-axis failures from the accepted v5 parent.
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.."; pwd)
cd "$ROOT"
hash(){ sha256sum "$1" | awk '{print $1}'; }

QUEUE=data/training/tooluse_citation_phoenix_v2_generation_queue.jsonl
ADAPTER=data/training/checkpoints/aria_tooluse_curve_sft_v5.tgz
EVAL=data/training/split_v1/eval.jsonl
GOLDEN=data/eval_frozen/aria_eval_500q.jsonl

test "$(hash "$QUEUE")" = ddfa3eb36f189750feb5a03decfeb668098be07a99e8b4d1b26b99de7a2c3210
test "$(hash "$ADAPTER")" = 99030c720f6db869f1fb4829d3389ee98f49cb67fea7b5169ca2f1b90417dac8
test "$(hash "$EVAL")" = d24be361fb30ff0e51272b2a7338be2924b8df5428d55a469f1c907bd28c3b00
test "$(hash "$GOLDEN")" = 4af4f76dbaf8fa3be341b97c94c5d654ef0e354704b974044fc8e64ddcdd296c

QUEUE="$QUEUE" EVAL_LOCAL="$EVAL" GOLDEN="$GOLDEN" \
  ADAPTER_LOCAL="$ADAPTER" \
  REPORT_LOCAL=data/eval_reports/aria_tooluse_citation_phoenix_v2_generations.json \
  STATE_FILE=data/eval_reports/.tooluse_citation_phoenix_v2_generation_pod_state \
  exec bash scripts/train/run_tooluse_generation.sh
