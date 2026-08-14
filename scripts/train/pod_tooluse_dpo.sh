#!/usr/bin/env bash
# R-F3815 — continue the measured v2 tool-use adapter on newly observed failures.
set -uo pipefail

BASE_MODEL="${BASE_MODEL:-mistralai/Mistral-7B-Instruct-v0.3}"
SFT_ADAPTER="${SFT_ADAPTER:-/workspace/checkpoints/aria_tooluse_dpo_v2}"
DPO_FILE="${DPO_FILE:-/workspace/datasets/aria_tooluse_dpo_v3.jsonl}"
EVAL_FILE="${EVAL_FILE:-/workspace/datasets/aria_tooluse_eval.jsonl}"
DPO_OUT="${DPO_OUT:-/workspace/checkpoints/aria_tooluse_dpo_v3}"
REPORT="${REPORT:-/workspace/eval/aria_tooluse_dpo_eval.json}"
ARCHIVE="${ARCHIVE:-/workspace/eval/aria_tooluse_dpo_adapter.tgz}"
PROBE_FILE="${PROBE_FILE:-/workspace/datasets/aria_tooluse_curve_probe.jsonl}"
BEFORE_PROBE="${BEFORE_PROBE:-/workspace/eval/aria_tooluse_curve_raw_probe.json}"
DPO_PROBE="${DPO_PROBE:-/workspace/eval/aria_tooluse_curve_dpo_probe.json}"
DPO_VERDICT="${DPO_VERDICT:-/workspace/eval/aria_tooluse_curve_dpo_verdict.json}"
DIAGNOSTICS="${DIAGNOSTICS:-/workspace/eval/aria_tooluse_curve_diagnostics.tgz}"
HELDOUT_BASELINE="${HELDOUT_BASELINE:-}"
HELDOUT_VERDICT="${HELDOUT_VERDICT:-/workspace/eval/aria_tooluse_heldout_verdict.json}"
SCRIPTS="/workspace/crucix/scripts/train"
LOGS="/workspace/logs"
PORT=8888
EXPECTED_EVAL_ROWS="${EXPECTED_EVAL_ROWS:-168}"
EXPECTED_DPO_PAIRS="${EXPECTED_DPO_PAIRS:-8}"
DPO_BETA="${DPO_BETA:-0.3}"
DPO_LR="${DPO_LR:-2e-6}"
SKIP_TRAIN="${SKIP_TRAIN:-0}"
FRESH_BASE="${FRESH_BASE:-0}"
export HF_HOME=/workspace/.cache/huggingface
cd /workspace/crucix || { echo "[FATAL] staged repository unavailable" >&2; exit 1; }

mkdir -p "$DPO_OUT" /workspace/eval "$LOGS"
rm -f /workspace/eval/_cycle_status
collect_diagnostics(){
  local files=() name
  for name in aria_tooluse_curve_dpo_probe.json aria_tooluse_curve_dpo_verdict.json aria_tooluse_heldout_verdict.json; do
    [ ! -f "/workspace/eval/$name" ] || files+=("$name")
  done
  [ "${#files[@]}" -eq 0 ] || tar -czf "$DIAGNOSTICS" -C /workspace/eval "${files[@]}"
}
on_exit(){ local rc=$?; collect_diagnostics 2>/dev/null || true; echo "$rc" > /workspace/eval/_cycle_status 2>/dev/null || true; }
trap on_exit EXIT
log(){ echo "[$(date -u +%H:%M:%S)] [tooluse-dpo] $*"; }
fail(){ echo "[FATAL] $*" >&2; exit 1; }

if [ "$SKIP_TRAIN" != 1 ] && [ "$FRESH_BASE" != 1 ]; then
  [ -f "$SFT_ADAPTER/adapter_config.json" ] || fail "recovered SFT adapter missing"
fi
[ -s "$DPO_FILE" ] || fail "DPO corpus missing"
[ -s "$EVAL_FILE" ] || fail "held-out eval missing"
if [ -s "$PROBE_FILE" ] || [ -s "$BEFORE_PROBE" ]; then
  [ -s "$PROBE_FILE" ] && [ -s "$BEFORE_PROBE" ] \
    || fail "DPO calibration inputs must be supplied together"
fi
for script in dpo_train.py serve_eval_shim.py eval_tooluse.py build_tooluse_corpus.py; do
  [ -f "$SCRIPTS/$script" ] || fail "$script missing"
done

python - "$DPO_FILE" "$EXPECTED_DPO_PAIRS" <<'PY' || fail "DPO corpus validation failed"
import json, sys
rows = [json.loads(line) for line in open(sys.argv[1], encoding="utf-8") if line.strip()]
expected = int(sys.argv[2])
if len(rows) != expected:
    raise SystemExit(f"expected {expected} DPO pairs, got {len(rows)}")
for i, row in enumerate(rows, 1):
    prompt = row.get("prompt")
    if not isinstance(prompt, list) or not prompt:
        raise SystemExit(f"pair {i} has no conversational prompt")
    if not all(isinstance(m, dict) and isinstance(m.get("role"), str)
               and isinstance(m.get("content"), str) for m in prompt):
        raise SystemExit(f"pair {i} has an invalid prompt message")
    if not all(isinstance(row.get(k), str) and row[k].strip() for k in ("chosen", "rejected")):
        raise SystemExit(f"pair {i} has an invalid preference")
    if row["chosen"] == row["rejected"]:
        raise SystemExit(f"pair {i} has identical preferences")
print(f"verified {expected} non-degenerate DPO pairs")
PY

log "installing pinned train and evaluation runtime"
pip install -q "transformers==4.46.3" "peft==0.13.2" "trl==0.12.2" \
  "accelerate>=0.34" bitsandbytes datasets sentencepiece protobuf fastapi uvicorn httpx \
  || fail "dependency installation failed"
python - <<'PY' || fail "runtime preflight failed"
import torch, transformers, peft, trl, bitsandbytes, accelerate  # noqa: F401
if not torch.cuda.is_available():
    raise SystemExit("CUDA unavailable")
if not torch.cuda.is_bf16_supported():
    raise SystemExit("bf16 unavailable")
print(torch.cuda.get_device_name(0))
PY

if [ "$SKIP_TRAIN" != 1 ]; then
  log "DPO training: $EXPECTED_DPO_PAIRS pairs, one epoch, beta=$DPO_BETA, lr=$DPO_LR, batch=2"
  PARENT_ARGS=(--sft-checkpoint "$SFT_ADAPTER")
  [ "$FRESH_BASE" != 1 ] || PARENT_ARGS=(--fresh-lora)
  python "$SCRIPTS/dpo_train.py" \
    --base-model "$BASE_MODEL" "${PARENT_ARGS[@]}" \
    --dpo-file "$DPO_FILE" --output-dir "$DPO_OUT" \
    --epochs 1 --beta "$DPO_BETA" --lr "$DPO_LR" --batch-size 2 \
    --gradient-accumulation-steps 1 \
    --max-seq-len 4096 --max-grad-norm 0.3 --load-in-4bit \
    2>&1 | tee "$LOGS/tooluse_dpo_train.log"
else
  log "recovery mode: reusing retained full-epoch DPO adapter"
fi
[ -f "$DPO_OUT/adapter_config.json" ] || fail "DPO produced no adapter"

# Persist the serving artifact before the long eval so a later host failure cannot
# erase paid training. Optimizer checkpoints are deliberately excluded.
tar --exclude='checkpoint-*' -czf "$ARCHIVE" -C "$(dirname "$DPO_OUT")" "$(basename "$DPO_OUT")" \
  || fail "adapter archive failed"
tar -tzf "$ARCHIVE" | awk '/\/adapter_config.json$/ { found=1 } END { exit !found }' \
  || fail "adapter archive invalid"
log "DPO adapter staged before held-out evaluation"

ADAPTER="$DPO_OUT" MODEL_NAME=aria-tooluse-dpo PORT=$PORT BASE_MODEL="$BASE_MODEL" \
  setsid nohup python "$SCRIPTS/serve_eval_shim.py" >"$LOGS/tooluse_dpo_shim.log" 2>&1 </dev/null &
for i in $(seq 1 60); do
  curl -fsS --max-time 5 "http://localhost:$PORT/v1/models" | grep -q aria-tooluse-dpo && break
  [ "$i" -eq 60 ] && { tail -40 "$LOGS/tooluse_dpo_shim.log"; fail "evaluation shim unavailable"; }
  sleep 10
done

if [ -s "$PROBE_FILE" ]; then
  log "evaluating DPO on the fixed 30-row calibration before held-out"
  python -m scripts.train.eval_tooluse --target "http://localhost:$PORT/v1" \
    --model aria-tooluse-dpo --eval-file "$PROBE_FILE" --out "$DPO_PROBE" \
    2>&1 | tee "$LOGS/tooluse_dpo_probe.log" || fail "DPO calibration failed"
  python -m scripts.train.learning_curve_gate --before "$BEFORE_PROBE" \
    --after "$DPO_PROBE" --verdict-out "$DPO_VERDICT" \
    --protected-axis tooluse_adverse --protected-axis tooluse_contradiction \
    --protected-axis tooluse_news_impact --protected-axis tooluse_resolution \
    || fail "SFT-to-DPO curve gate"
  collect_diagnostics || fail "DPO diagnostics archive"
fi

log "evaluating unchanged 168-row held-out set"
python -m scripts.train.eval_tooluse --target "http://localhost:$PORT/v1" \
  --model aria-tooluse-dpo --eval-file "$EVAL_FILE" --out "$REPORT" \
  2>&1 | tee "$LOGS/tooluse_dpo_eval.log" || fail "held-out evaluation failed"
python - "$REPORT" "$EXPECTED_EVAL_ROWS" <<'PY' || fail "held-out completeness gate failed"
import json, sys
d = json.load(open(sys.argv[1], encoding="utf-8"))
n = int(sys.argv[2])
if d.get("complete") is not True or d.get("total") != n or len(d.get("rows") or []) != n:
    raise SystemExit(f"report does not prove {n} complete rows")
print(f"verified complete held-out evaluation: n={n}")
PY
if [ -n "$HELDOUT_BASELINE" ]; then
  [ -s "$HELDOUT_BASELINE" ] || fail "parent held-out baseline missing"
  python -m scripts.train.learning_curve_gate --before "$HELDOUT_BASELINE" \
    --after "$REPORT" --verdict-out "$HELDOUT_VERDICT" \
    || fail "parent-to-DPO held-out progression gate"
  collect_diagnostics || fail "held-out verdict diagnostics archive"
fi
pkill -f serve_eval_shim 2>/dev/null || true
log "cycle complete"
