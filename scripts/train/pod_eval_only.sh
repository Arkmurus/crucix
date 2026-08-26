#!/usr/bin/env bash
# On-pod EVAL-ONLY runner (R-F17xx) — serve the EXISTING on-volume LoRA adapter
# and run the 500-Q open-book eval. NO training. Operator approved an offline
# eval of the best own-model (v0.7); this serves the adapter already on the
# volume at SFT_OUT and evals it — it never retrains.
#
# Mirrors v0_4_pod_run.sh's serve()/eval_model() exactly so the result is
# comparable to the prior v0.7 number (0.308) on the identical harness.
set -uo pipefail
BASE_MODEL="${BASE_MODEL:-unsloth/mistral-7b-instruct-v0.3}"
SFT_OUT="${SFT_OUT:-/workspace/checkpoints/aria_llm_v0_4_sft}"   # last-trained adapter (v0.7 if it was last)
EVAL_SET="${EVAL_SET:-/workspace/datasets/aria_eval_openbook.jsonl}"
EVAL_DIR="/workspace/eval"
LOGS="/workspace/logs"
SCRIPTS="/workspace/crucix/scripts/train"
PORT=8888
NAME="${NAME:-aria-llm-v0.7}"
REPORT="${EVAL_DIR}/aria_llm_v0_7_eval_rerun.json"
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
mkdir -p "$EVAL_DIR" "$LOGS"
rm -f "$EVAL_DIR/_cycle_status"
trap 'rc=$?; echo "$rc" > "$EVAL_DIR/_cycle_status" 2>/dev/null || true' EXIT
log(){ echo "[$(date -u +%H:%M:%S)] $*"; }
fail(){ echo "[FATAL] $*" >&2; exit 1; }

# --- Identify the adapter on the volume (honesty: report what we are evaling) ---
[ -f "$SFT_OUT/adapter_config.json" ] || fail "no adapter at $SFT_OUT — nothing to eval (the v0.7 adapter is not on this volume)"
log "adapter present at $SFT_OUT"
ls -la "$SFT_OUT" | tee "$LOGS/adapter_listing.log"
# Surface any provenance the cycle left behind so we know the version.
for marker in "$SFT_OUT/training_args.json" "$SFT_OUT/README.md" /workspace/logs/v0_7_cycle.log /workspace/datasets/aria_grounded_v3.jsonl; do
  [ -e "$marker" ] && log "provenance: $marker $(stat -c '%y' "$marker" 2>/dev/null)"
done

serve(){  # serve <lora_path> <name>
  local lora="$1" name="$2"
  pkill -f serve_eval_shim 2>/dev/null || true
  sleep 3
  HF_HOME="$HF_HOME" ADAPTER="$lora" MODEL_NAME="$name" PORT=$PORT BASE_MODEL="$BASE_MODEL" \
    setsid nohup python "$SCRIPTS/serve_eval_shim.py" > "$LOGS/shim_${name}.log" 2>&1 < /dev/null &
  for i in $(seq 1 60); do
    if curl -s --max-time 5 "http://localhost:$PORT/v1/models" | grep -q "$name"; then
      log "shim serving $name (attempt $i)"; return 0
    fi
    sleep 10
  done
  echo "=== shim log tail ($name) ==="; tail -40 "$LOGS/shim_${name}.log"; return 1
}

eval_model(){  # eval_model <name> <out>
  local name="$1" out="$2"
  DEEPSEEK_API_KEY="${DEEPSEEK_API_KEY:-}" \
  python "$SCRIPTS/eval_aria_llm.py" \
    --target "http://localhost:$PORT/v1" --model "$name" \
    --eval-set "$EVAL_SET" --out "$out" 2>&1 | tee "$LOGS/eval_${name}.log"
}

log "serving existing adapter (NO training)…"
serve "$SFT_OUT" "$NAME" || fail "could not serve $NAME"
log "running 500-Q open-book eval ($(wc -l < "$EVAL_SET") Q)…"
eval_model "$NAME" "$REPORT" || fail "eval failed"
log "DONE — report at $REPORT"
python - "$REPORT" <<'PY'
import json,sys
d=json.load(open(sys.argv[1],encoding="utf-8")); dd=d.get("defence_dd") or {}; pi=d.get("prompt_injection") or {}
print(f"[verdict] v0.7 (rerun, open-book): judge-DD={dd.get('accuracy')} (n={dd.get('total')}) | leak_rate={pi.get('leak_rate')}")
print("[verdict] DeepSeek parity = 0.336 → G1", "PASS" if (dd.get('accuracy') or 0) >= 0.336 else "FAIL")
PY
