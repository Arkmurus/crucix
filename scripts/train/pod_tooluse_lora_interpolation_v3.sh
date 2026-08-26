#!/usr/bin/env bash
# R-F4249 — evaluate the three pre-registered alpha-band interpolations.
set -uo pipefail

BASE_MODEL="${BASE_MODEL:-mistralai/Mistral-7B-Instruct-v0.3}"
PARENT="${PARENT:?need PARENT}"
CANDIDATE="${CANDIDATE:?need CANDIDATE}"
EVAL_FILE="${EVAL_FILE:-/workspace/datasets/aria_tooluse_eval.jsonl}"
EXPECTED_ROWS="${EXPECTED_ROWS:-168}"
ALPHAS="${ALPHAS:-0.8 0.875 0.95}"
SCRIPTS=/workspace/crucix/scripts/train
EVALD=/workspace/eval
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
mkdir -p "$EVALD" "$LOGS" /workspace/checkpoints/interpolations
rm -f "$EVALD/_cycle_status"

log(){ echo "[$(date -u +%H:%M:%S)] [lora-interpolation] $*"; }
fail(){ echo "[FATAL] $*" >&2; exit 1; }
on_exit(){ local rc=$?; echo "$rc" > "$EVALD/_cycle_status" 2>/dev/null || true; }
trap on_exit EXIT
require_watchdog(){
  [ -s "$EVALD/_watchdog_pid" ] \
    && kill -0 "$(cat "$EVALD/_watchdog_pid")" 2>/dev/null \
    || fail "self-stop watchdog unavailable"
}
stop_shim(){
  [ -z "${SHIM_PID:-}" ] || kill "$SHIM_PID" 2>/dev/null || true
  [ -z "${SHIM_PID:-}" ] || wait "$SHIM_PID" 2>/dev/null || true
  SHIM_PID=""
}
trap 'stop_shim; on_exit' EXIT

for path in "$PARENT" "$CANDIDATE"; do
  [ -f "$path/adapter_config.json" ] || fail "adapter config missing: $path"
  [ -f "$path/adapter_model.safetensors" ] || fail "adapter weights missing: $path"
done
[ -s "$EVAL_FILE" ] || fail "held-out eval missing"
[ "$ALPHAS" = "0.8 0.875 0.95" ] || fail "alpha set differs from registration"
python - "$EVAL_FILE" "$EXPECTED_ROWS" <<'PY' || fail "held-out row count"
import sys
rows = [line for line in open(sys.argv[1], encoding="utf-8") if line.strip()]
if len(rows) != int(sys.argv[2]):
    raise SystemExit(f"expected {sys.argv[2]} rows, got {len(rows)}")
PY
require_watchdog

log "installing pinned evaluation runtime"
pip install -q "transformers==4.46.3" "peft==0.13.2" \
  "accelerate>=0.34" bitsandbytes sentencepiece protobuf fastapi uvicorn httpx \
  || fail "dependency installation failed"
python - <<'PY' || fail "CUDA bf16 runtime unavailable"
import torch
if not torch.cuda.is_available() or not torch.cuda.is_bf16_supported():
    raise SystemExit("required CUDA bf16 runtime unavailable")
PY
require_watchdog

for alpha in $ALPHAS; do
  tag=$(printf '%s' "$alpha" | tr -d '.')
  adapter=/workspace/checkpoints/interpolations/alpha_$tag
  log "building alpha=$alpha"
  python -m scripts.train.interpolate_lora_adapters \
    --parent "$PARENT" --candidate "$CANDIDATE" --output "$adapter" --alpha "$alpha" \
    || fail "interpolation failed for alpha=$alpha"
  require_watchdog

  model="aria-tooluse-interpolation-$tag"
  ADAPTER="$adapter" MODEL_NAME="$model" PORT=$PORT BASE_MODEL="$BASE_MODEL" \
    python "$SCRIPTS/serve_eval_shim.py" >"$LOGS/interpolation_${tag}_shim.log" 2>&1 &
  SHIM_PID=$!
  for i in $(seq 1 60); do
    curl -fsS --max-time 5 "http://localhost:$PORT/v1/models" | grep -q "$model" && break
    [ "$i" -eq 60 ] && fail "evaluation shim unavailable for alpha=$alpha"
    sleep 10
  done
  require_watchdog

  report="$EVALD/aria_tooluse_lora_interpolation_v3_alpha_${tag}.json"
  python -m scripts.train.eval_tooluse --target "http://localhost:$PORT/v1" \
    --model "$model" --eval-file "$EVAL_FILE" --out "$report" \
    2>&1 | tee "$LOGS/interpolation_${tag}_eval.log" \
    || fail "evaluation failed for alpha=$alpha"
  python - "$report" "$EXPECTED_ROWS" <<'PY' || fail "report incomplete"
import json, sys
d = json.load(open(sys.argv[1], encoding="utf-8")); n = int(sys.argv[2])
if d.get("complete") is not True or d.get("total") != n or len(d.get("rows") or []) != n:
    raise SystemExit("interpolation report incomplete")
PY
  stop_shim
  log "alpha=$alpha complete"
done

log "all registered interpolation arms complete"
