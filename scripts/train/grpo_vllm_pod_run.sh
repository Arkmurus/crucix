#!/usr/bin/env bash
# R-F2037 (Phase 3) — vLLM-accelerated GRPO RLVR cycle. The 2026-06-27 non-vLLM run
# was ~12 min/step (4-bit HF generate) -> ~11h for the full set, impractical. vLLM
# colocate (a bf16 copy of the policy sharing the GPU, LoRA-synced each step) makes
# GRPO 10-50x faster, so the FULL 631-prompt answerable-balanced dataset (R-F2033)
# is feasible in ~1h. Starts from the saved adapter (uploaded as init).
#
# REQUIRES an A100-80 (vLLM holds its own weights + KV cache ALONGSIDE training).
# A SMOKE gate runs a tiny vLLM GRPO first so the (version-sensitive) vLLM+trl env
# aborts cheaply (CLAUDE.md §24) instead of wasting a paid run.
set -uo pipefail
BASE_MODEL="${BASE_MODEL:-unsloth/mistral-7b-instruct-v0.3}"
INIT_ADAPTER="${INIT_ADAPTER:-/workspace/checkpoints/aria_llm_init}"
GRPO_OUT="/workspace/checkpoints/aria_llm_grpo_v2"
PROMPTS="${PROMPTS:-/workspace/datasets/aria_grpo_prompts_v1.jsonl}"   # full 631 (answerable-balanced)
EVAL_SET="${EVAL_SET:-/workspace/datasets/aria_eval_500q.jsonl}"
EVAL_DIR="/workspace/eval"; LOGS="/workspace/logs"; SCRIPTS="/workspace/crucix/scripts/train"
PORT=8888
NUM_GEN="${NUM_GEN:-6}"; GRPO_EPOCHS="${GRPO_EPOCHS:-1}"; GRPO_LR="${GRPO_LR:-1e-6}"
BATCH="${BATCH:-6}"; VLLM_GPU_MEM="${VLLM_GPU_MEM:-0.30}"
GRPO_REPORT="${EVAL_DIR}/aria_llm_grpo_v2_eval.json"
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
export PYTHONPATH="/workspace/crucix:${PYTHONPATH:-}"
mkdir -p "$EVAL_DIR" "$LOGS" "$(dirname "$GRPO_OUT")" /workspace/datasets
rm -f "$EVAL_DIR/_cycle_status"
trap 'rc=$?; echo "$rc" > "$EVAL_DIR/_cycle_status" 2>/dev/null || true' EXIT
log(){ echo "[$(date -u +%H:%M:%S)] $*"; }
fail(){ echo "[FATAL] $*" >&2; exit 1; }

log "=== ARIA-LLM GRPO RLVR + vLLM (R-F2037, answerable-aware reward R-F2033) ==="
[ -s "$PROMPTS" ] || fail "GRPO prompts missing: $PROMPTS"
[ -f "$EVAL_SET" ] || fail "eval set missing: $EVAL_SET"
[ -f "$INIT_ADAPTER/adapter_config.json" ] || fail "init adapter missing: $INIT_ADAPTER"
for s in grpo_train.py serve_eval_shim.py eval_aria_llm.py; do [ -f "$SCRIPTS/$s" ] || fail "$s missing"; done
log "preflight ok — prompts $(wc -l < "$PROMPTS") | eval $(wc -l < "$EVAL_SET") | init $INIT_ADAPTER"

# GPU must be big enough for colocate (vLLM weights + KV + training). Warn if <70GB.
GMEM=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits 2>/dev/null | head -1)
log "GPU memory: ${GMEM:-?} MiB"
[ -n "$GMEM" ] && [ "$GMEM" -lt 70000 ] && log "WARN: GPU < 70GB — vLLM colocate may OOM; consider A100-80 or lower NUM_GEN/VLLM_GPU_MEM."

log "installing vLLM + colocate-capable trl (version-sensitive — smoke-gated below)…"
# vLLM pulls its own torch build; install it FIRST, then a colocate-capable trl
# (vllm_mode is trl>=0.17) + matching transformers/peft. If this combo is wrong the
# SMOKE gate below fails cheaply with the exact error to fix the pins.
pip install -q "vllm==0.7.3" || fail "vllm install failed"
pip install -q "trl==0.17.0" "transformers==4.49.0" "peft==0.14.0" \
    "accelerate>=1.4" "datasets>=3.2" bitsandbytes sentencepiece protobuf \
    fastapi uvicorn httpx || fail "trl/transformers install failed"
python - <<'PY' || fail "vLLM+GRPO import/capability check failed — fix the pins, then re-run"
import torch, vllm, trl, transformers, peft, inspect
from vllm import LLM  # noqa
from trl import GRPOTrainer, GRPOConfig
fields = set(getattr(GRPOConfig, "__dataclass_fields__", {}))
assert "use_vllm" in fields and "vllm_mode" in fields, \
    f"GRPOConfig lacks use_vllm/vllm_mode (trl {trl.__version__}) — need a colocate-capable trl"
print(f"OK: vllm {vllm.__version__} trl {trl.__version__} transformers {transformers.__version__} torch {torch.__version__}")
PY

# ── SMOKE GATE: tiny vLLM GRPO (4 prompts) — abort cheaply on any vLLM/trl mismatch ──
log "vLLM GRPO SMOKE gate (4 prompts) before the full run…"
head -n 4 "$PROMPTS" > /workspace/datasets/_grpo_vllm_smoke.jsonl
if ! python "$SCRIPTS/grpo_train.py" --base-model "$BASE_MODEL" --sft-checkpoint "$INIT_ADAPTER" \
      --dataset /workspace/datasets/_grpo_vllm_smoke.jsonl --output-dir /workspace/checkpoints/_grpo_vllm_smoke \
      --num-generations 4 --batch-size 4 --epochs 1 --max-completion-len 128 \
      --use-vllm --vllm-gpu-mem "$VLLM_GPU_MEM" \
      2>&1 | tee "$LOGS/grpo_vllm_smoke.log" | tail -30; then
  fail "vLLM GRPO smoke FAILED — see $LOGS/grpo_vllm_smoke.log (likely vllm/trl/torch pin or GPU-mem). Not burning a full run."
fi
[ -f /workspace/checkpoints/_grpo_vllm_smoke/adapter_config.json ] || fail "vLLM smoke produced no adapter — aborting"
log "SMOKE PASS — vLLM GRPO env works. Running FULL cycle on $(wc -l < "$PROMPTS") prompts."

# ── FULL GRPO (vLLM, full answerable-balanced dataset) ──
log "GRPO+vLLM training → $GRPO_OUT (gen=$NUM_GEN batch=$BATCH epochs=$GRPO_EPOCHS lr=$GRPO_LR) …"
python "$SCRIPTS/grpo_train.py" --base-model "$BASE_MODEL" --sft-checkpoint "$INIT_ADAPTER" \
  --dataset "$PROMPTS" --output-dir "$GRPO_OUT" \
  --num-generations "$NUM_GEN" --batch-size "$BATCH" --epochs "$GRPO_EPOCHS" --lr "$GRPO_LR" \
  --max-completion-len 512 --use-vllm --vllm-gpu-mem "$VLLM_GPU_MEM" \
  2>&1 | tee "$LOGS/grpo_vllm_train.log"
[ -f "$GRPO_OUT/adapter_config.json" ] || fail "GRPO produced no adapter — see $LOGS/grpo_vllm_train.log"
log "GRPO+vLLM complete — adapter at $GRPO_OUT"

# ── serve + eval (answers; the OBJECTIVE answerable-aware grounding is scored off-pod) ──
pkill -f serve_eval_shim 2>/dev/null || true; sleep 3
ADAPTER="$GRPO_OUT" MODEL_NAME="aria-llm-grpo-v2" PORT=$PORT BASE_MODEL="$BASE_MODEL" \
  setsid nohup python "$SCRIPTS/serve_eval_shim.py" > "$LOGS/shim_grpo_v2.log" 2>&1 < /dev/null &
for i in $(seq 1 60); do curl -s --max-time 5 "http://localhost:$PORT/v1/models" | grep -q aria-llm-grpo-v2 && { log "serving"; break; }; sleep 10; done
DEEPSEEK_API_KEY="${DEEPSEEK_API_KEY:-}" python "$SCRIPTS/eval_aria_llm.py" \
  --target "http://localhost:$PORT/v1" --model "aria-llm-grpo-v2" --eval-set "$EVAL_SET" --out "$GRPO_REPORT" \
  2>&1 | tee "$LOGS/eval_grpo_v2.log"
pkill -f serve_eval_shim 2>/dev/null || true
log "done — GRPO+vLLM cycle complete. (Pod stop = self-stop watcher.)"
