#!/usr/bin/env bash
# R-F3843 — build, prove, and launch one bounded mixed-retention candidate.
set -euo pipefail
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO=$(cd "$SCRIPT_DIR/../.." && pwd); cd "$REPO"
PYBIN="${PYBIN:-.venv/Scripts/python.exe}"
TRAIN="${TRAIN:-data/training/tooluse_base_balanced_v1_queue.jsonl}"
DPO="${DPO:-data/training/aria_tooluse_base_balanced_v1_dpo.jsonl}"
EVAL="${EVAL:-data/training/split_v2/eval.jsonl}"
GOLDEN="${GOLDEN:-data/eval_frozen/aria_eval_500q.jsonl}"
SFT="${SFT:-data/training/aria_tooluse_mixed_retention_v1.jsonl}"
MANIFEST="${MANIFEST:-data/eval_reports/tooluse_mixed_v1_manifest.json}"
"$PYBIN" -m scripts.train.build_mixed_tooluse_cycle --train "$TRAIN" --dpo "$DPO" \
  --eval "$EVAL" --golden "$GOLDEN" --quota 6 --sft-out "$SFT" --manifest-out "$MANIFEST"
"$PYBIN" - "$MANIFEST" <<'PY'
import json, sys
d=json.load(open(sys.argv[1], encoding="utf-8"))
if d.get("complete") is not True or len(d.get("signal_axes") or []) != 10:
    raise SystemExit("mixed-retention manifest incomplete")
PY
DPO_SHA=$(sha256sum "$DPO" | awk '{print $1}')
EVAL_SHA=$(sha256sum "$EVAL" | awk '{print $1}')
SFT_SHA=$(sha256sum "$SFT" | awk '{print $1}')
FRESH_BASE=1 EXPECTED_DPO_PAIRS=51 DPO_SHA256="$DPO_SHA" EVAL_SHA256="$EVAL_SHA" SFT_SHA256="$SFT_SHA" \
  POD_RUNNER=scripts/train/pod_tooluse_mixed.sh SFT_LOCAL="$SFT" DPO_LOCAL="$DPO" \
  EVAL_LOCAL="$EVAL" TRAIN_PROOF="$TRAIN" REPORT_LOCAL=data/eval_reports/aria_tooluse_mixed_v1_eval.json \
  OUTPUT_LOCAL=data/training/checkpoints/aria_tooluse_mixed_v1.tgz \
  INTERMEDIATE_LOCAL=data/training/checkpoints/aria_tooluse_mixed_sft_v1.tgz \
  STATE_FILE=data/eval_reports/.tooluse_mixed_v1_pod_state \
  REMOTE_DPO_OUT=/workspace/checkpoints/aria_tooluse_mixed_dpo CYCLE_DEADLINE=10800 \
  bash scripts/train/run_tooluse_dpo.sh
