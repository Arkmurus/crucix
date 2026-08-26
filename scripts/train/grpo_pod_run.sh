#!/usr/bin/env bash
# R-F2010 — on-pod GRPO RLVR cycle: GRPO (grounding reward) -> serve -> eval.
#
# Starts GRPO from a SAVED adapter (uploaded by the driver) so we need only ONE
# trl env (GRPO needs trl>=0.14; the proven SFT/DPO pin 0.12.2). The reward is the
# objective grounding_reward (R-F1942) — fabricated [Source:] -> ~0. A SMOKE gate
# runs a 2-step GRPO on 4 prompts FIRST so a trl/transformers API mismatch aborts
# cheaply (CLAUDE.md §24 avoid-loss) instead of wasting a multi-hour cycle.
set -uo pipefail
BASE_MODEL="${BASE_MODEL:-unsloth/mistral-7b-instruct-v0.3}"
INIT_ADAPTER="${INIT_ADAPTER:-/workspace/checkpoints/aria_llm_init}"   # uploaded SFT/DPO adapter
GRPO_OUT="${GRPO_OUT:-/workspace/checkpoints/aria_llm_grpo_v1}"   # R-F2558 overridable — don't clobber a prior winner
PROMPTS="${PROMPTS:-/workspace/datasets/aria_grpo_prompts_v1.jsonl}"
EVAL_SET="${EVAL_SET:-/workspace/datasets/aria_eval_500q.jsonl}"
EVAL_DIR="/workspace/eval"; LOGS="/workspace/logs"; SCRIPTS="/workspace/crucix/scripts/train"
PORT=8888
NUM_GEN="${NUM_GEN:-4}"; GRPO_EPOCHS="${GRPO_EPOCHS:-1}"; GRPO_LR="${GRPO_LR:-1e-6}"
MAX_PROMPTS="${MAX_PROMPTS:-220}"           # cap first cycle for tractable wall-clock
GRPO_REPORT="${GRPO_REPORT:-${EVAL_DIR}/aria_llm_grpo_v1_eval.json}"
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

serve(){  # serve <lora> <name>
  local lora="$1" name="$2"; pkill -f serve_eval_shim 2>/dev/null || true; sleep 3
  HF_HOME="$HF_HOME" ADAPTER="$lora" MODEL_NAME="$name" PORT=$PORT BASE_MODEL="$BASE_MODEL" \
    setsid nohup python "$SCRIPTS/serve_eval_shim.py" > "$LOGS/shim_${name}.log" 2>&1 < /dev/null &
  for i in $(seq 1 60); do
    curl -s --max-time 5 "http://localhost:$PORT/v1/models" | grep -q "$name" && { log "shim serving $name ($i)"; return 0; }
    sleep 10
  done; echo "=== shim log ==="; tail -40 "$LOGS/shim_${name}.log"; return 1
}
eval_model(){ DEEPSEEK_API_KEY="${DEEPSEEK_API_KEY:-}" python "$SCRIPTS/eval_aria_llm.py" \
    --target "http://localhost:$PORT/v1" --model "$1" --eval-set "$EVAL_SET" --out "$2" 2>&1 | tee "$LOGS/eval_$1.log"; }

log "=== ARIA-LLM GRPO RLVR cycle (grounding reward, volume-free) ==="
[ -s "$PROMPTS" ] || fail "GRPO prompts missing: $PROMPTS"
[ -f "$EVAL_SET" ] || fail "eval set missing: $EVAL_SET"
[ -f "$INIT_ADAPTER/adapter_config.json" ] || fail "init adapter missing: $INIT_ADAPTER"
for s in grpo_train.py serve_eval_shim.py eval_aria_llm.py; do [ -f "$SCRIPTS/$s" ] || fail "$s missing"; done
log "preflight ok — prompts $(wc -l < "$PROMPTS") (cap $MAX_PROMPTS) | eval $(wc -l < "$EVAL_SET") | init $INIT_ADAPTER"

log "installing GRPO-capable deps (trl>=0.14)…"
pip install -q "transformers==4.48.3" "trl==0.16.1" "peft==0.14.0" \
    "accelerate>=1.2.1" "datasets>=3.2" bitsandbytes sentencepiece protobuf \
    fastapi uvicorn httpx || fail "dep install failed"
python - <<'PY' || fail "GRPO import check failed — wrong trl/transformers combo"
import trl, transformers, peft
from trl import GRPOTrainer, GRPOConfig  # the whole point
print(f"deps ok: trl {trl.__version__} transformers {transformers.__version__} peft {peft.__version__}")
PY

# cap the dataset for a tractable first cycle
head -n "$MAX_PROMPTS" "$PROMPTS" > /workspace/datasets/_grpo_capped.jsonl
CAP=/workspace/datasets/_grpo_capped.jsonl
log "dataset capped to $(wc -l < "$CAP") prompts"

# ── SMOKE GATE: tiny GRPO (4 prompts, 1 step) — abort cheaply on API mismatch ──
log "GRPO SMOKE gate (4 prompts, fast) before the full run…"
head -n 4 "$CAP" > /workspace/datasets/_grpo_smoke.jsonl
if ! python "$SCRIPTS/grpo_train.py" --base-model "$BASE_MODEL" --sft-checkpoint "$INIT_ADAPTER" \
      --dataset /workspace/datasets/_grpo_smoke.jsonl --output-dir /workspace/checkpoints/_grpo_smoke \
      --num-generations 4 --epochs 1 --batch-size 4 --max-completion-len 128 --load-in-4bit \
      2>&1 | tee "$LOGS/grpo_smoke.log" | tail -25; then
  fail "GRPO smoke FAILED — trl/transformers GRPO API mismatch; not burning a full cycle. See $LOGS/grpo_smoke.log"
fi
[ -f /workspace/checkpoints/_grpo_smoke/adapter_config.json ] || fail "smoke produced no adapter — aborting"
log "SMOKE PASS — GRPO env works. Running full cycle."

# ── FULL GRPO ──
log "GRPO training → $GRPO_OUT (gen=$NUM_GEN epochs=$GRPO_EPOCHS lr=$GRPO_LR) …"
python "$SCRIPTS/grpo_train.py" --base-model "$BASE_MODEL" --sft-checkpoint "$INIT_ADAPTER" \
  --dataset "$CAP" --output-dir "$GRPO_OUT" \
  --num-generations "$NUM_GEN" --epochs "$GRPO_EPOCHS" --lr "$GRPO_LR" \
  --batch-size "$NUM_GEN" --max-completion-len 640 --load-in-4bit 2>&1 | tee "$LOGS/grpo_train.log"
[ -f "$GRPO_OUT/adapter_config.json" ] || fail "GRPO produced no adapter — see $LOGS/grpo_train.log"
log "GRPO complete — adapter at $GRPO_OUT"

# ── serve + eval (answers; objective grounding scored locally by the driver) ──
serve "$GRPO_OUT" "aria-llm-grpo-v1" || fail "could not serve grpo-v1"
log "eval grpo-v1 (500-Q, judge for record; objective grounding scored off-pod)…"
eval_model "aria-llm-grpo-v1" "$GRPO_REPORT" || fail "grpo-v1 eval failed"
pkill -f serve_eval_shim 2>/dev/null || true
log "done — GRPO cycle complete. (Pod stop = self-stop watcher.)"
