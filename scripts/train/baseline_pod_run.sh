#!/bin/bash
# R-F1455 — SURGICAL v0.2-vs-DeepSeek baseline, run ENTIRELY ON THE POD.
#
# Mirrors the ONLY recipe PROVEN to serve on this pod (train_promote_v0_2.sh:
# pinned deps R-F1345 + serve_eval_shim.py + localhost eval). It deliberately
# does NOT use vLLM: `pip install vllm` pulls a torch built for a newer CUDA
# driver than this pod has (driver 565 / found 12070) and crashes EngineCore
# ("NVIDIA driver too old") — that is what made every proxy run return 502.
# The pod already has working torch 2.4.1+cu124; the shim uses it.
#
# Eval runs against http://localhost:8888 (NO RunPod proxy) — the proven path.
#
# Env in:  DEEPSEEK_API_KEY (for the DeepSeek baseline leg)
# Out:     /workspace/eval/aria_llm_v02_eval.json + deepseek_baseline_eval.json
set -uo pipefail

BASE_MODEL="mistralai/Mistral-7B-Instruct-v0.3"      # v0.2 actual base (R-F1454)
ADAPTER="/workspace/checkpoints/aria_llm_v0_2_dpo"   # the DPO LoRA
NAME="aria-llm-v0.2"
PORT=8888
SCRIPTS="/workspace/crucix/scripts/train"
EVAL_SET="${EVAL_SET:-/workspace/datasets/aria_eval_500q.jsonl}"   # R-F1455: overridable (100-Q subset)
EVAL_DIR="/workspace/eval"
LOGS="/workspace/logs"
# Base weights live in the PERSISTENT volume cache (container ~/.cache is wiped on
# restart). Gated on HF + no token, so resolve strictly from the cache.
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
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1

mkdir -p "$EVAL_DIR" "$LOGS"
log(){ echo "[$(date -u +%H:%M:%S)] $*"; }
fail(){ echo "[FATAL] $*" >&2; exit 1; }

# ── Preflight (no destructive action before everything is present) ────────────
[ -d "$ADAPTER" ] || fail "adapter missing: $ADAPTER"
[ -f "$SCRIPTS/serve_eval_shim.py" ] || fail "shim missing: $SCRIPTS/serve_eval_shim.py"
[ -f "$SCRIPTS/eval_aria_llm.py" ] || fail "eval missing: $SCRIPTS/eval_aria_llm.py"
[ -f "$EVAL_SET" ] || fail "eval set missing: $EVAL_SET"
[ -d "$HF_HOME/hub/models--mistralai--Mistral-7B-Instruct-v0.3" ] \
  || fail "Mistral base not in volume cache: $HF_HOME"
ADAPTER_BASE=$(python3 -c "import json;print(json.load(open('$ADAPTER/adapter_config.json')).get('base_model_name_or_path',''))" 2>/dev/null || echo "")
[ "$ADAPTER_BASE" = "$BASE_MODEL" ] || fail "adapter base ('$ADAPTER_BASE') != '$BASE_MODEL'"
log "preflight ok — eval set $(wc -l < "$EVAL_SET") lines; adapter base $ADAPTER_BASE"

# ── Deps: the EXACT pinned coherent set from train_promote_v0_2.sh (R-F1345),
# serve+eval subset (drop trl/datasets — those are train-only). Mistral's
# tokenizer NEEDS sentencepiece+protobuf; the shim NEEDS fastapi+uvicorn; the
# eval NEEDS httpx. Do NOT pipe pip through tail (masks the exit code). ──────────
log "installing pinned serve+eval deps…"
pip install -q "transformers==4.46.3" "peft==0.13.2" "accelerate>=0.34" \
    bitsandbytes sentencepiece protobuf fastapi uvicorn httpx \
    || fail "dep install failed"
python - <<'PY' || fail "deps import check failed"
import transformers, peft, accelerate, bitsandbytes  # noqa
import sentencepiece, google.protobuf  # noqa — Mistral tokenizer
import fastapi, uvicorn, httpx  # noqa — shim + eval client
print(f"deps ok: transformers {transformers.__version__} peft {peft.__version__}")
PY

# ── Serve v0.2 via the shim (EXACT invocation from train_promote serve()) ─────
pkill -f serve_eval_shim 2>/dev/null || true
pkill -f vllm.entrypoints 2>/dev/null || true
sleep 3
ADAPTER="$ADAPTER" MODEL_NAME="$NAME" PORT=$PORT BASE_MODEL="$BASE_MODEL" \
  setsid nohup python "$SCRIPTS/serve_eval_shim.py" > "$LOGS/shim_$NAME.log" 2>&1 < /dev/null &
log "waiting for shim to serve $NAME (bf16 7B load ~3-5 min)…"
ready=0
for i in $(seq 1 60); do   # 60*10s = 10 min cap
  if curl -s --max-time 5 "http://localhost:$PORT/v1/models" | grep -q "$NAME"; then
    ready=1; log "shim serving $NAME (attempt $i)"; break
  fi
  sleep 10
done
if [ "$ready" != 1 ]; then
  echo "=== shim log tail ==="; tail -40 "$LOGS/shim_$NAME.log"
  fail "shim did not come up in 10 min"
fi

# ── Eval v0.2 against localhost (PROVEN path — no RunPod proxy) ───────────────
log "eval v0.2 (500-Q) against localhost…"
python "$SCRIPTS/eval_aria_llm.py" \
  --target "http://localhost:$PORT/v1" --model "$NAME" \
  --eval-set "$EVAL_SET" --out "$EVAL_DIR/aria_llm_v02_eval.json" 2>&1 | tee "$LOGS/eval_v02.log" \
  || fail "v0.2 eval failed"

# ── Eval DeepSeek baseline — SAME eval set, SAME instrument (apples-to-apples) ─
if [ -n "${DEEPSEEK_API_KEY:-}" ]; then
  log "eval DeepSeek baseline (deepseek-chat)…"
  python "$SCRIPTS/eval_aria_llm.py" \
    --target "https://api.deepseek.com/v1" --model "deepseek-chat" \
    --api-key "$DEEPSEEK_API_KEY" \
    --eval-set "$EVAL_SET" --out "$EVAL_DIR/deepseek_baseline_eval.json" 2>&1 | tee "$LOGS/eval_deepseek.log" \
    || log "WARN: DeepSeek eval failed (see log) — v0.2 result still valid"
else
  log "WARN: DEEPSEEK_API_KEY not set on pod — skipping DeepSeek baseline"
fi

# ── Summary ──────────────────────────────────────────────────────────────────
log "=== BASELINE RESULTS ==="
python - <<'PY'
import json, os
for f in ["/workspace/eval/aria_llm_v02_eval.json",
          "/workspace/eval/deepseek_baseline_eval.json"]:
    if not os.path.exists(f):
        print(f, "MISSING"); continue
    d = json.load(open(f))
    dd = d.get("defence_dd") or {}
    pi = d.get("prompt_injection") or {}
    print(f"{d.get('model')}: dd_accuracy={dd.get('accuracy')} (n={dd.get('total')}) "
          f"| injection pass_rate={pi.get('pass_rate')} leak_rate={pi.get('leak_rate')}")
PY
pkill -f serve_eval_shim 2>/dev/null || true
log "done."
