#!/usr/bin/env bash
# R-F3843 — raw-base chosen-only retention SFT, then genuine-failure DPO.
set -uo pipefail
BASE_MODEL="${BASE_MODEL:-mistralai/Mistral-7B-Instruct-v0.3}"
SFT_FILE="${SFT_FILE:-/workspace/datasets/aria_tooluse_retention_sft.jsonl}"
DPO_FILE="${DPO_FILE:-/workspace/datasets/aria_tooluse_dpo_v3.jsonl}"
EVAL_FILE="${EVAL_FILE:-/workspace/datasets/aria_tooluse_eval.jsonl}"
SFT_OUT="${SFT_OUT:-/workspace/checkpoints/aria_tooluse_mixed_sft}"
DPO_OUT="${DPO_OUT:-/workspace/checkpoints/aria_tooluse_mixed_dpo}"
SFT_ARCHIVE="${SFT_ARCHIVE:-/workspace/eval/aria_tooluse_mixed_sft.tgz}"
DPO_ARCHIVE="${ARCHIVE:-/workspace/eval/aria_tooluse_dpo_adapter.tgz}"
REPORT="${REPORT:-/workspace/eval/aria_tooluse_dpo_eval.json}"
EXPECTED_SFT_ROWS="${EXPECTED_SFT_ROWS:-24}"
EXPECTED_DPO_PAIRS="${EXPECTED_DPO_PAIRS:-51}"
EXPECTED_EVAL_ROWS="${EXPECTED_EVAL_ROWS:-168}"
SCRIPTS=/workspace/crucix/scripts/train
LOGS=/workspace/logs
PORT=8888
# R-F4350 (C-295) — ONE definition of which disk holds the HF cache.
# This line used to hardcode the cache onto /workspace, a 20G volume whose own
# comment mis-named it the container disk; see hf_cache_select.sh for the
# measurement and why it fails closed.
_hfsel=""
for _d in "$(dirname "${BASH_SOURCE[0]:-$0}")" /workspace/crucix/scripts/train /workspace; do
  [ -f "$_d/hf_cache_select.sh" ] && { _hfsel="$_d/hf_cache_select.sh"; break; }
done
[ -n "$_hfsel" ] || { echo "[FATAL] hf_cache_select.sh not found — refusing to guess a cache disk." >&2; exit 1; }
. "$_hfsel"
hf_cache_select || exit 1
cd /workspace/crucix || exit 1
mkdir -p /workspace/checkpoints /workspace/eval "$LOGS"
rm -f /workspace/eval/_cycle_status
trap 'rc=$?; echo "$rc" > /workspace/eval/_cycle_status 2>/dev/null || true' EXIT
log(){ echo "[$(date -u +%H:%M:%S)] [tooluse-mixed] $*"; }
fail(){ echo "[FATAL] $*" >&2; exit 1; }
require_watchdog(){
  [ -s /workspace/eval/_watchdog_pid ] || fail "self-stop watchdog pid missing"
  kill -0 "$(cat /workspace/eval/_watchdog_pid)" 2>/dev/null || fail "self-stop watchdog is not alive"
}
validate_rows(){
  python - "$1" "$2" <<'PY'
import json, sys
rows=[json.loads(line) for line in open(sys.argv[1], encoding="utf-8") if line.strip()]
if len(rows) != int(sys.argv[2]):
    raise SystemExit(f"expected {sys.argv[2]} rows, got {len(rows)}")
PY
}
for file in "$SFT_FILE" "$DPO_FILE" "$EVAL_FILE"; do [ -s "$file" ] || fail "missing $file"; done
validate_rows "$SFT_FILE" "$EXPECTED_SFT_ROWS" || fail "retention SFT incomplete"
validate_rows "$DPO_FILE" "$EXPECTED_DPO_PAIRS" || fail "DPO input incomplete"
validate_rows "$EVAL_FILE" "$EXPECTED_EVAL_ROWS" || fail "eval input incomplete"
log "installing pinned runtime"
pip install -q "transformers==4.46.3" "peft==0.13.2" "trl==0.12.2" \
  "accelerate>=0.34" bitsandbytes datasets sentencepiece protobuf fastapi uvicorn httpx || fail "dependency installation failed"
python - <<'PY' || fail "runtime preflight failed"
import torch, transformers, peft, trl
assert torch.cuda.is_available() and torch.cuda.is_bf16_supported()
assert transformers.__version__ == "4.46.3"
assert peft.__version__ == "0.13.2"
assert trl.__version__ == "0.12.2"
PY
require_watchdog
log "chosen-only retention SFT: $EXPECTED_SFT_ROWS rows"
python "$SCRIPTS/sft_train.py" --base-model "$BASE_MODEL" --train-file "$SFT_FILE" \
  --output-dir "$SFT_OUT" --epochs 1 --lora-rank 32 --lora-alpha 64 --lr 2e-5 \
  --batch-size 2 --max-seq-len 4096 --load-in-4bit 2>&1 | tee "$LOGS/tooluse_mixed_sft.log" || fail "retention SFT failed"
[ -f "$SFT_OUT/adapter_config.json" ] || fail "retention SFT produced no adapter"
tar --exclude='checkpoint-*' -czf "$SFT_ARCHIVE.tmp" -C "$(dirname "$SFT_OUT")" "$(basename "$SFT_OUT")" || fail "SFT archive failed"
tar -tzf "$SFT_ARCHIVE.tmp" | awk '/\/adapter_config.json$/ { found=1 } END { exit !found }' || fail "SFT archive invalid"
mv "$SFT_ARCHIVE.tmp" "$SFT_ARCHIVE"
require_watchdog
log "genuine-failure DPO: $EXPECTED_DPO_PAIRS pairs"
python "$SCRIPTS/dpo_train.py" --base-model "$BASE_MODEL" --sft-checkpoint "$SFT_OUT" \
  --dpo-file "$DPO_FILE" --output-dir "$DPO_OUT" --epochs 1 --beta 0.3 --lr 2e-6 \
  --batch-size 2 --gradient-accumulation-steps 1 --max-seq-len 4096 \
  --max-grad-norm 0.3 --load-in-4bit 2>&1 | tee "$LOGS/tooluse_dpo_train.log" || fail "DPO failed"
[ -f "$DPO_OUT/adapter_config.json" ] || fail "DPO produced no adapter"
tar --exclude='checkpoint-*' -czf "$DPO_ARCHIVE.tmp" -C "$(dirname "$DPO_OUT")" "$(basename "$DPO_OUT")" || fail "DPO archive failed"
tar -tzf "$DPO_ARCHIVE.tmp" | awk '/\/adapter_config.json$/ { found=1 } END { exit !found }' || fail "DPO archive invalid"
mv "$DPO_ARCHIVE.tmp" "$DPO_ARCHIVE"
require_watchdog
ADAPTER="$DPO_OUT" MODEL_NAME=aria-tooluse-dpo PORT=$PORT BASE_MODEL="$BASE_MODEL" \
  setsid nohup python "$SCRIPTS/serve_eval_shim.py" >"$LOGS/tooluse_dpo_shim.log" 2>&1 </dev/null &
for i in $(seq 1 60); do
  curl -fsS --max-time 5 "http://localhost:$PORT/v1/models" | grep -q aria-tooluse-dpo && break
  [ "$i" -eq 60 ] && fail "evaluation shim unavailable"; sleep 10
done
python -m scripts.train.eval_tooluse --target "http://localhost:$PORT/v1" --model aria-tooluse-dpo \
  --eval-file "$EVAL_FILE" --out "$REPORT" 2>&1 | tee "$LOGS/tooluse_dpo_eval.log" || fail "held-out evaluation failed"
python - "$REPORT" "$EXPECTED_EVAL_ROWS" <<'PY' || fail "held-out report incomplete"
import json, sys
d=json.load(open(sys.argv[1], encoding="utf-8")); n=int(sys.argv[2])
assert d.get("complete") is True and d.get("total") == n and len(d.get("rows") or []) == n
PY
pkill -f serve_eval_shim 2>/dev/null || true
log "mixed cycle complete"
