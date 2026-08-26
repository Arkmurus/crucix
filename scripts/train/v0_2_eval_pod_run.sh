#!/bin/bash
# R-F1474 — v0.2 500-Q eval, run ON THE POD. Closes the v0.3-vs-v0.2 head-to-head.
#
# The 2026-06-09 cycle trained v0.3 (judge-DD 0.22 / 500) and evaluated it, but the
# v0.2 comparison leg never finished before the pod went away — so we have v0.3's
# absolute score but no apples-to-apples verdict. This re-serves the PERSISTED v0.2
# DPO adapter and evals it on the SAME frozen 500-Q with the SAME validated judge.
# NO training — adapter already exists on the volume. Mirrors v0_3_pod_run.sh's
# serve()/eval_model() exactly (shim, NOT vLLM; localhost:8888; judge auto-on).
#
# Env in:  DEEPSEEK_API_KEY (judge), EVAL_SET (default below).
# Out:     /workspace/eval/aria_llm_v0_2_eval.json
set -uo pipefail

BASE_MODEL="mistralai/Mistral-7B-Instruct-v0.3"
V02_ADAPTER="/workspace/checkpoints/aria_llm_v0_2_dpo"
EVAL_SET="${EVAL_SET:-/workspace/datasets/aria_eval_500q.jsonl}"
EVAL_DIR="/workspace/eval"; LOGS="/workspace/logs"; SCRIPTS="/workspace/crucix/scripts/train"
PORT=8888
V02_REPORT="${EVAL_DIR}/aria_llm_v0_2_eval.json"
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
rm -f "$EVAL_DIR/_v0_2_status"
trap 'rc=$?; echo "$rc" > "$EVAL_DIR/_v0_2_status" 2>/dev/null || true' EXIT
log(){ echo "[$(date -u +%H:%M:%S)] $*"; }
fail(){ echo "[FATAL] $*" >&2; exit 1; }

serve(){  # serve <lora_path> <name> — shim only
  local lora="$1" name="$2"
  pkill -f serve_eval_shim 2>/dev/null || true; sleep 3
  HF_HOME="$HF_HOME" ADAPTER="$lora" MODEL_NAME="$name" PORT=$PORT BASE_MODEL="$BASE_MODEL" \
    setsid nohup python "$SCRIPTS/serve_eval_shim.py" > "$LOGS/shim_${name}.log" 2>&1 < /dev/null &
  for i in $(seq 1 60); do
    if curl -s --max-time 5 "http://localhost:$PORT/v1/models" | grep -q "$name"; then
      log "shim serving $name (attempt $i)"; return 0
    fi; sleep 10
  done
  echo "=== shim log tail ($name) ==="; tail -40 "$LOGS/shim_${name}.log"; return 1
}

coherence_ok(){
  python - "$1" <<'PY'
import json, sys, urllib.request
name = sys.argv[1]
def ask(q):
    body = json.dumps({"model": name, "messages": [{"role":"user","content":q}],
                       "max_tokens": 150, "temperature": 0.3}).encode()
    req = urllib.request.Request("http://localhost:8888/v1/chat/completions", body, {"Content-Type":"application/json"})
    return json.load(urllib.request.urlopen(req, timeout=120))["choices"][0]["message"]["content"]
QS = ["What is the main due-diligence risk with a sanctions-adjacent counterparty?",
      "In two sentences, why does ultimate beneficial ownership matter in KYC?",
      "What should a compliance team verify before onboarding a new supplier?"]
bad = 0
for q in QS:
    try: t = ask(q)
    except Exception as e: print(f"  ask failed: {e}", file=sys.stderr); bad += 1; continue
    deg = any(m in t for m in ("tier=","self_hosted","[Layers","comp_web","=10000")) or len(t.strip()) < 25
    if deg: bad += 1; print(f"  DEGENERATE: {t[:120]!r}", file=sys.stderr)
print("COHERENT" if bad == 0 else "DEGENERATE")
PY
}

log "=== ARIA-LLM v0.2 500-Q eval (comparison leg) ==="
[ -d "$V02_ADAPTER" ] || fail "v0.2 adapter missing on volume: $V02_ADAPTER"
[ -f "$V02_ADAPTER/adapter_config.json" ] || fail "no adapter_config.json in $V02_ADAPTER"
[ -f "$EVAL_SET" ] || fail "eval set missing: $EVAL_SET"
[ -f "$SCRIPTS/serve_eval_shim.py" ] || fail "serve_eval_shim.py missing: $SCRIPTS"
[ -f "$SCRIPTS/eval_aria_llm.py" ]   || fail "eval_aria_llm.py missing: $SCRIPTS"
[ -d "$HF_HOME/hub/models--mistralai--Mistral-7B-Instruct-v0.3" ] || fail "Mistral base not in volume cache"
log "preflight ok — eval $(wc -l < "$EVAL_SET") Q against v0.2 adapter"

# Container site-packages are wiped on restart; only /workspace persists -> reinstall.
log "installing pinned serve+eval deps…"
pip install -q "transformers==4.46.3" "peft==0.13.2" "accelerate>=0.34" \
    bitsandbytes sentencepiece protobuf fastapi uvicorn httpx || fail "dep install failed"

serve "$V02_ADAPTER" "aria-llm-v0.2" || fail "could not serve v0.2"
COH=$(coherence_ok "aria-llm-v0.2"); log "coherence(v0.2): $COH"
[ "$COH" = "COHERENT" ] || fail "v0.2 DEGENERATE on coherence gate — inspect $LOGS/shim_aria-llm-v0.2.log"

log "eval v0.2 (500-Q, judge) against localhost…"
DEEPSEEK_API_KEY="${DEEPSEEK_API_KEY:-}" \
python "$SCRIPTS/eval_aria_llm.py" \
  --target "http://localhost:$PORT/v1" --model "aria-llm-v0.2" \
  --eval-set "$EVAL_SET" --out "$V02_REPORT" 2>&1 | tee "$LOGS/eval_v0_2.log" || fail "v0.2 eval failed"

[ -s "$V02_REPORT" ] || fail "v0.2 report not written: $V02_REPORT"
log "=== v0.2 eval complete ==="
python - "$V02_REPORT" <<'PY'
import json, sys
d = json.load(open(sys.argv[1], encoding="utf-8"))
dd = d.get("defence_dd") or d.get("dd_eval") or {}; pi = d.get("prompt_injection") or {}
print(f"v0.2 judge-DD: {dd.get('accuracy')} (n={dd.get('total')}) | injection leak_rate={pi.get('leak_rate')}")
PY
pkill -f serve_eval_shim 2>/dev/null || true
log "done — shim stopped. (Pod stop is the orchestrator's EXIT trap.)"
