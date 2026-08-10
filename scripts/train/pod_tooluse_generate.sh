#!/usr/bin/env bash
# R-F3744 — collect real candidate failures over a TRAIN-only task queue.
set -uo pipefail

cd /workspace/crucix 2>/dev/null || exit 1
export PYTHONPATH="/workspace/crucix${PYTHONPATH:+:$PYTHONPATH}"

BASE_MODEL="${BASE_MODEL:-mistralai/Mistral-7B-Instruct-v0.3}"
BASE_ONLY="${BASE_ONLY:-0}"
ADAPTER="${ADAPTER:-/workspace/checkpoints/aria_tooluse_v1}"
TRAIN_FILE="${TRAIN_FILE:-/workspace/datasets/aria_tooluse_dpo_generation.jsonl}"
OUT="${OUT:-/workspace/eval/tooluse_train_generations.json}"
PORT="${PORT:-8888}"
STATUS=/workspace/eval/_cycle_status
LOGS=/workspace/logs
mkdir -p "$LOGS" "$(dirname "$OUT")"

log(){ echo "[$(date -u +%H:%M:%S)] $*"; }
SHIM_PID=""
stop_shim(){ [ -n "$SHIM_PID" ] && kill "$SHIM_PID" 2>/dev/null; SHIM_PID=""; }
finish(){ rc=$?; stop_shim; echo "$rc" > "$STATUS"; log "generation exit rc=$rc"; }
trap finish EXIT

[ "$BASE_ONLY" = 1 ] || [ -f "$ADAPTER/adapter_config.json" ] \
  || { log "FATAL candidate adapter missing"; exit 1; }
[ -s "$TRAIN_FILE" ] || { log "FATAL train generation queue missing"; exit 1; }

log "installing pinned serving/evaluation runtime"
pip install -q "transformers==4.46.3" "peft==0.13.2" \
  "accelerate>=0.34" bitsandbytes sentencepiece protobuf \
  fastapi uvicorn httpx \
  || { log "FATAL runtime install"; exit 1; }
python - <<'PY' || { log "FATAL runtime preflight"; exit 1; }
import importlib
import sys

failed = []
for name in ("accelerate", "bitsandbytes", "fastapi", "httpx", "peft",
             "torch", "transformers", "uvicorn"):
    try:
        importlib.import_module(name)
    except Exception as exc:
        failed.append(f"{name}: {type(exc).__name__}: {exc}")
if failed:
    print("runtime imports failed:\n- " + "\n- ".join(failed), file=sys.stderr)
    raise SystemExit(1)

import torch
if not torch.cuda.is_available():
    raise SystemExit("CUDA unavailable - refusing paid generation")
print(f"runtime OK: CUDA {torch.version.cuda}, GPU {torch.cuda.get_device_name(0)}")
PY

log "serving generation model base_only=$BASE_ONLY"
SERVE_ADAPTER="$ADAPTER"
[ "$BASE_ONLY" != 1 ] || SERVE_ADAPTER=""
BASE_MODEL="$BASE_MODEL" ADAPTER="$SERVE_ADAPTER" MODEL_NAME="aria-tooluse" PORT="$PORT" \
  python scripts/train/serve_eval_shim.py >"$LOGS/shim_generation.log" 2>&1 &
SHIM_PID=$!
ready=0
for _ in $(seq 1 90); do
  if curl -sf "http://127.0.0.1:$PORT/v1/models" >/dev/null 2>&1; then
    ready=1; break
  fi
  kill -0 "$SHIM_PID" 2>/dev/null || { log "FATAL generation shim died"; exit 1; }
  sleep 10
done
[ "$ready" = 1 ] || { log "FATAL generation shim never became ready"; exit 1; }

log "generating over $(wc -l < "$TRAIN_FILE") train-only tasks"
python scripts/train/eval_tooluse.py \
  --eval-file "$TRAIN_FILE" --target "http://127.0.0.1:$PORT/v1" \
  --model aria-tooluse --out "$OUT"
[ -s "$OUT" ] || { log "FATAL generation report missing"; exit 1; }
python - "$OUT" <<'PY' || exit 1
import json, sys
d = json.load(open(sys.argv[1], encoding="utf-8"))
if not d.get("complete") or len(d.get("rows") or []) != int(d.get("total", -1)):
    raise SystemExit("generation report is incomplete")
print(f"verified complete generation report: {d['total']} rows")
PY
log "train generations complete"
