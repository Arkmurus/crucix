#!/usr/bin/env bash
# R-F3891 — continue only positive SFT from an accepted adapter parent.
set -uo pipefail

BASE_MODEL="${BASE_MODEL:-mistralai/Mistral-7B-Instruct-v0.3}"
SFT_ADAPTER="${SFT_ADAPTER:-}"
SFT_FILE=/workspace/datasets/aria_tooluse_retention_sft.jsonl
EXPECTED_SFT_ROWS="${EXPECTED_SFT_ROWS:-0}"
PROBE_FILE=/workspace/datasets/aria_tooluse_curve_probe.jsonl
BEFORE_PROBE=/workspace/eval/aria_tooluse_curve_raw_probe.json
EVAL_FILE=/workspace/datasets/aria_tooluse_eval.jsonl
SFT_OUT="${DPO_OUT:-/workspace/checkpoints/aria_tooluse_sft_child}"
REPORT=/workspace/eval/aria_tooluse_dpo_eval.json
ARCHIVE=/workspace/eval/aria_tooluse_dpo_adapter.tgz
AFTER_PROBE=/workspace/eval/aria_tooluse_sft_child_probe.json
VERDICT=/workspace/eval/aria_tooluse_sft_child_verdict.json
DIAGNOSTICS=/workspace/eval/aria_tooluse_curve_diagnostics.tgz
EXPECTED_EVAL_ROWS="${EXPECTED_EVAL_ROWS:-168}"
SCRIPTS=/workspace/crucix/scripts/train
LOGS=/workspace/logs
PORT=8888
export HF_HOME=/workspace/.cache/huggingface
cd /workspace/crucix || exit 1
mkdir -p "$SFT_OUT" /workspace/eval "$LOGS"
rm -f /workspace/eval/_cycle_status
log(){ echo "[$(date -u +%H:%M:%S)] [tooluse-sft-continue] $*"; }
fail(){ echo "[FATAL] $*" >&2; exit 1; }
collect(){
  [ ! -f "$AFTER_PROBE" ] || tar -czf "$DIAGNOSTICS" -C /workspace/eval \
    "$(basename "$AFTER_PROBE")" "$(basename "$VERDICT")" 2>/dev/null || true
}
on_exit(){ local rc=$?; collect; echo "$rc" > /workspace/eval/_cycle_status 2>/dev/null || true; }
trap on_exit EXIT
require_watchdog(){
  [ -s /workspace/eval/_watchdog_pid ] || fail "self-stop watchdog unavailable"
  local watchdog_pid
  watchdog_pid=$(cat /workspace/eval/_watchdog_pid)
  kill -0 "$watchdog_pid" 2>/dev/null || fail "self-stop watchdog unavailable"
}
validate_count(){ python - "$1" "$2" <<'PY'
import sys
rows = [line for line in open(sys.argv[1], encoding="utf-8") if line.strip()]
if len(rows) != int(sys.argv[2]):
    raise SystemExit(f"expected {sys.argv[2]} rows, got {len(rows)}")
PY
}

[ -n "$SFT_ADAPTER" ] || fail "SFT parent path unavailable"
[ -f "$SFT_ADAPTER/adapter_config.json" ] || fail "accepted SFT parent missing"
for file in "$SFT_FILE" "$PROBE_FILE" "$BEFORE_PROBE" "$EVAL_FILE"; do
  [ -s "$file" ] || fail "missing $file"
done
[ "$EXPECTED_SFT_ROWS" -gt 0 ] 2>/dev/null || fail "expected SFT count unavailable"
validate_count "$SFT_FILE" "$EXPECTED_SFT_ROWS" || fail "SFT count"
validate_count "$PROBE_FILE" 30 || fail "probe count"
validate_count "$EVAL_FILE" "$EXPECTED_EVAL_ROWS" || fail "held-out count"
for script in sft_train.py serve_eval_shim.py eval_tooluse.py build_tooluse_corpus.py learning_curve_gate.py; do
  [ -f "$SCRIPTS/$script" ] || fail "$script missing"
done

require_watchdog
pip install -q "transformers==4.46.3" "peft==0.13.2" "trl==0.12.2" \
  "accelerate>=0.34" bitsandbytes datasets sentencepiece protobuf fastapi uvicorn httpx \
  || fail "dependencies"
python - <<'PY' || fail "runtime"
import torch, transformers, peft, trl
if not torch.cuda.is_available() or not torch.cuda.is_bf16_supported():
    raise SystemExit("required CUDA bf16 runtime unavailable")
PY
log "positive SFT continuation: $EXPECTED_SFT_ROWS rows from accepted parent"
python "$SCRIPTS/sft_train.py" --base-model "$BASE_MODEL" \
  --sft-checkpoint "$SFT_ADAPTER" --train-file "$SFT_FILE" --output-dir "$SFT_OUT" \
  --epochs 1 --lora-rank 32 --lora-alpha 64 --lr 1e-5 --batch-size 2 \
  --max-seq-len 4096 --load-in-4bit --completion-only-loss \
  2>&1 | tee "$LOGS/tooluse_sft_continue.log" || fail "SFT continuation"
[ -f "$SFT_OUT/adapter_config.json" ] || fail "SFT continuation produced no adapter"
tar --exclude='checkpoint-*' -czf "$ARCHIVE" -C "$(dirname "$SFT_OUT")" \
  "$(basename "$SFT_OUT")" || fail "adapter archive"
tar -tzf "$ARCHIVE" | awk '/\/adapter_config.json$/ { found=1 } END { exit !found }' \
  || fail "adapter archive invalid"
log "positive SFT child staged before evaluation"

ADAPTER="$SFT_OUT" MODEL_NAME=aria-tooluse-sft-child PORT=$PORT BASE_MODEL="$BASE_MODEL" \
  setsid nohup python "$SCRIPTS/serve_eval_shim.py" >"$LOGS/tooluse_sft_child_shim.log" 2>&1 </dev/null &
for i in $(seq 1 60); do
  if python -c "import urllib.request; data=urllib.request.urlopen('http://localhost:$PORT/v1/models', timeout=5).read(); assert b'aria-tooluse-sft-child' in data" 2>/dev/null; then
    break
  fi
  if [ "$i" -eq 60 ]; then
    fail "evaluation shim unavailable"
  fi
  sleep 10
done
require_watchdog
python -m scripts.train.eval_tooluse --target "http://localhost:$PORT/v1" \
  --model aria-tooluse-sft-child --eval-file "$PROBE_FILE" --out "$AFTER_PROBE" \
  2>&1 | tee "$LOGS/tooluse_sft_child_probe.log" || fail "calibration eval"
python "$SCRIPTS/learning_curve_gate.py" --before "$BEFORE_PROBE" --after "$AFTER_PROBE" \
  --verdict-out "$VERDICT" --protected-axis tooluse_adverse \
  --protected-axis tooluse_contradiction --protected-axis tooluse_news_impact \
  --protected-axis tooluse_resolution || fail "positive SFT calibration gate"
collect
require_watchdog
log "evaluating positive SFT child on unchanged held-out set"
python -m scripts.train.eval_tooluse --target "http://localhost:$PORT/v1" \
  --model aria-tooluse-sft-child --eval-file "$EVAL_FILE" --out "$REPORT" \
  2>&1 | tee "$LOGS/tooluse_dpo_eval.log" || fail "held-out evaluation"
python - "$REPORT" "$EXPECTED_EVAL_ROWS" <<'PY' || fail "held-out completeness"
import json, sys
d = json.load(open(sys.argv[1], encoding="utf-8")); n = int(sys.argv[2])
if d.get("complete") is not True or d.get("total") != n or len(d.get("rows") or []) != n:
    raise SystemExit(f"report does not prove {n} complete rows")
print(f"verified positive-SFT held-out evaluation: n={n}")
PY
pkill -f serve_eval_shim 2>/dev/null || true
log "positive SFT continuation complete"
