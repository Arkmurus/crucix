#!/usr/bin/env bash
# R-F3880 — evaluate one retained SFT adapter without mutating it.
set -uo pipefail

BASE_MODEL="${BASE_MODEL:-mistralai/Mistral-7B-Instruct-v0.3}"
SFT_ADAPTER="${SFT_ADAPTER:-}"
EVAL_FILE="${EVAL_FILE:-/workspace/datasets/aria_tooluse_eval.jsonl}"
REPORT="${REPORT:-/workspace/eval/aria_tooluse_dpo_eval.json}"
ARCHIVE="${ARCHIVE:-/workspace/eval/aria_tooluse_dpo_adapter.tgz}"
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
mkdir -p /workspace/eval "$LOGS"
rm -f /workspace/eval/_cycle_status

log(){ echo "[$(date -u +%H:%M:%S)] [tooluse-adapter-eval] $*"; }
fail(){ echo "[FATAL] $*" >&2; exit 1; }
on_exit(){ local rc=$?; echo "$rc" > /workspace/eval/_cycle_status 2>/dev/null || true; }
trap on_exit EXIT
require_watchdog(){
  [ -s /workspace/eval/_watchdog_pid ] \
    && kill -0 "$(cat /workspace/eval/_watchdog_pid)" 2>/dev/null \
    || fail "self-stop watchdog unavailable"
}

[ -n "$SFT_ADAPTER" ] || fail "SFT adapter path unavailable"
[ -f "$SFT_ADAPTER/adapter_config.json" ] || fail "retained SFT adapter missing"
[ -s "$EVAL_FILE" ] || fail "held-out eval missing"
for script in serve_eval_shim.py eval_tooluse.py build_tooluse_corpus.py; do
  [ -f "$SCRIPTS/$script" ] || fail "$script missing"
done
python - "$EVAL_FILE" "$EXPECTED_EVAL_ROWS" <<'PY' || fail "held-out input count"
import sys
rows = [line for line in open(sys.argv[1], encoding="utf-8") if line.strip()]
if len(rows) != int(sys.argv[2]):
    raise SystemExit(f"expected {sys.argv[2]} held-out rows, got {len(rows)}")
PY

require_watchdog
log "installing pinned evaluation runtime"
pip install -q "transformers==4.46.3" "peft==0.13.2" \
  "accelerate>=0.34" bitsandbytes sentencepiece protobuf fastapi uvicorn httpx \
  || fail "dependency installation failed"
python - <<'PY' || fail "runtime preflight failed"
import torch, transformers, peft  # noqa: F401
if not torch.cuda.is_available() or not torch.cuda.is_bf16_supported():
    raise SystemExit("required CUDA bf16 runtime unavailable")
print(torch.cuda.get_device_name(0))
PY

# Persist exactly what will be served before the long evaluation. This does not
# train or merge the adapter; it proves the measured artifact survived intact.
tar --exclude='checkpoint-*' -czf "$ARCHIVE" -C "$(dirname "$SFT_ADAPTER")" \
  "$(basename "$SFT_ADAPTER")" || fail "adapter archive failed"
tar -tzf "$ARCHIVE" | awk '/\/adapter_config.json$/ { found=1 } END { exit !found }' \
  || fail "adapter archive invalid"
log "retained SFT adapter staged before held-out evaluation"

ADAPTER="$SFT_ADAPTER" MODEL_NAME=aria-tooluse-sft PORT=$PORT BASE_MODEL="$BASE_MODEL" \
  setsid nohup python "$SCRIPTS/serve_eval_shim.py" >"$LOGS/tooluse_sft_shim.log" 2>&1 </dev/null &
for i in $(seq 1 60); do
  curl -fsS --max-time 5 "http://localhost:$PORT/v1/models" | grep -q aria-tooluse-sft && break
  [ "$i" -eq 60 ] && { tail -40 "$LOGS/tooluse_sft_shim.log"; fail "evaluation shim unavailable"; }
  sleep 10
done
require_watchdog
log "evaluating retained SFT on unchanged held-out set"
python -m scripts.train.eval_tooluse --target "http://localhost:$PORT/v1" \
  --model aria-tooluse-sft --eval-file "$EVAL_FILE" --out "$REPORT" \
  2>&1 | tee "$LOGS/tooluse_dpo_eval.log" || fail "held-out evaluation failed"
python - "$REPORT" "$EXPECTED_EVAL_ROWS" <<'PY' || fail "held-out completeness gate failed"
import json, sys
d = json.load(open(sys.argv[1], encoding="utf-8"))
n = int(sys.argv[2])
if d.get("complete") is not True or d.get("total") != n or len(d.get("rows") or []) != n:
    raise SystemExit(f"report does not prove {n} complete rows")
print(f"verified retained-SFT held-out evaluation: n={n}")
PY
pkill -f serve_eval_shim 2>/dev/null || true
log "evaluation complete"
