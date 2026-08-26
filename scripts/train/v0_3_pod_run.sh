#!/bin/bash
# R-F1470 — ARIA-LLM v0.3 SFT-distillation cycle, run ENTIRELY ON THE POD.
#
# First REAL training cycle (data/_V03_TRAINING_RUNBOOK.md): SFT-distill DeepSeek
# into ARIA's own 7B on the 500-pair distillation corpus, then eval v0.3 vs v0.2
# on the frozen 500-Q with the VALIDATED LLM judge (R-F1456/1468). Mirrors the
# ONLY recipe PROVEN to serve on this pod (train_promote_v0_2.sh + baseline_pod_run.sh):
# pinned coherent deps (R-F1345) + serve_eval_shim.py + localhost eval.
#
# It deliberately does NOT use vLLM: `pip install vllm` pulls a torch built for a
# newer CUDA driver than this pod has -> EngineCore crash ("NVIDIA driver too old")
# -> every proxy run returns 502 (R-F1455). The pod's working torch + the shim are used.
#
# Eval runs against http://localhost:8888 (NO RunPod proxy) — the proven path.
# The judge auto-fires when DEEPSEEK_API_KEY is set AND the eval set carries
# expected_answer (the re-exported 500-Q does — R-F1456/1469).
#
# Env in:  DEEPSEEK_API_KEY (answer-judge + DeepSeek baseline leg), EPOCHS (default 3),
#          TRAIN_FILE / EVAL_SET (defaults below).
# Out:     /workspace/eval/aria_llm_v0_3_eval.json + aria_llm_v0_2_eval.json
#          (+ deepseek_baseline_eval.json if the key is set)
set -uo pipefail

BASE_MODEL="mistralai/Mistral-7B-Instruct-v0.3"      # v0.2's base (R-F1454)
SFT_OUT="/workspace/checkpoints/aria_llm_v0_3_sft"   # v0.3 SFT adapter (produced here)
V02_ADAPTER="/workspace/checkpoints/aria_llm_v0_2_dpo"  # the existing v0.2 DPO LoRA
TRAIN_FILE="${TRAIN_FILE:-/workspace/datasets/aria_sft_distill_500.jsonl}"
EVAL_SET="${EVAL_SET:-/workspace/datasets/aria_eval_500q.jsonl}"
EVAL_DIR="/workspace/eval"
LOGS="/workspace/logs"
SCRIPTS="/workspace/crucix/scripts/train"
PORT=8888
EPOCHS="${EPOCHS:-3}"
V03_REPORT="${EVAL_DIR}/aria_llm_v0_3_eval.json"
V02_REPORT="${EVAL_DIR}/aria_llm_v0_2_eval.json"
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

mkdir -p "$EVAL_DIR" "$LOGS" "$(dirname "$SFT_OUT")"
# R-F1470: completion sentinel — the orchestrator runs this DETACHED and polls
# for this file (the exit code) so an SSH drop over the 2-3h run can't lose the
# result. Clear any stale one, then write the real exit code on ANY exit.
rm -f "$EVAL_DIR/_cycle_status"
trap 'rc=$?; echo "$rc" > "$EVAL_DIR/_cycle_status" 2>/dev/null || true' EXIT
log(){ echo "[$(date -u +%H:%M:%S)] $*"; }
fail(){ echo "[FATAL] $*" >&2; exit 1; }

serve(){  # serve <lora_path> <name> — shim ONLY (vLLM crashes on this pod's driver)
  local lora="$1" name="$2"
  pkill -f serve_eval_shim 2>/dev/null || true
  pkill -f vllm.entrypoints 2>/dev/null || true
  sleep 3
  HF_HOME="$HF_HOME" ADAPTER="$lora" MODEL_NAME="$name" PORT=$PORT BASE_MODEL="$BASE_MODEL" \
    setsid nohup python "$SCRIPTS/serve_eval_shim.py" > "$LOGS/shim_${name}.log" 2>&1 < /dev/null &
  for i in $(seq 1 60); do   # cached bf16 7B load ~3-5 min; 60*10s = 10 min cap
    if curl -s --max-time 5 "http://localhost:$PORT/v1/models" | grep -q "$name"; then
      log "shim serving $name (attempt $i)"; return 0
    fi
    sleep 10
  done
  echo "=== shim log tail ($name) ==="; tail -40 "$LOGS/shim_${name}.log"
  return 1
}

eval_model(){  # eval_model <name> <out> — localhost, judge auto-on via DEEPSEEK_API_KEY
  # The judge fires inside eval_aria_llm.py when DEEPSEEK_API_KEY is set AND the
  # eval set carries expected_answer (verified: eval_aria_llm.py:251-274). No
  # enable-flag is read by this harness (ARIA cross-check, R-F1470).
  local name="$1" out="$2"
  DEEPSEEK_API_KEY="${DEEPSEEK_API_KEY:-}" \
  python "$SCRIPTS/eval_aria_llm.py" \
    --target "http://localhost:$PORT/v1" --model "$name" \
    --eval-set "$EVAL_SET" --out "$out" 2>&1 | tee "$LOGS/eval_${name}.log"
}

coherence_ok(){  # coherence_ok <name> — 3-prompt smoke before trusting a 500-Q eval
  local name="$1"
  python - "$name" <<'PY'
import json, sys, urllib.request
name = sys.argv[1]
def ask(q):
    body = json.dumps({"model": name, "messages": [{"role":"user","content":q}],
                       "max_tokens": 150, "temperature": 0.3}).encode()
    req = urllib.request.Request("http://localhost:8888/v1/chat/completions",
                                 body, {"Content-Type":"application/json"})
    return json.load(urllib.request.urlopen(req, timeout=120))["choices"][0]["message"]["content"]
QS = ["What is the main due-diligence risk with a sanctions-adjacent counterparty?",
      "In two sentences, why does ultimate beneficial ownership matter in KYC?",
      "What should a compliance team verify before onboarding a new supplier?"]
bad = 0
for q in QS:
    try:
        t = ask(q)
    except Exception as e:
        print(f"  ask failed: {e}", file=sys.stderr); bad += 1; continue
    deg = any(m in t for m in ("tier=","self_hosted","[Layers","comp_web","=10000")) or len(t.strip()) < 25
    rep = any(t.count(t[i:i+12]) > 6 for i in range(0, max(0, len(t)-12), 12)) if len(t) >= 12 else False
    if deg or rep:
        bad += 1; print(f"  DEGENERATE sample: {t[:120]!r}", file=sys.stderr)
print("COHERENT" if bad == 0 else "DEGENERATE")
PY
}

# ── Preflight (abort before any paid/destructive step) ─────────────────────────
log "=== ARIA-LLM v0.3 SFT-distillation cycle ==="
[ -f "$TRAIN_FILE" ] || fail "train corpus missing: $TRAIN_FILE"
[ -s "$TRAIN_FILE" ] || fail "train corpus empty: $TRAIN_FILE"
[ -f "$EVAL_SET" ]   || fail "eval set missing: $EVAL_SET"
[ -f "$SCRIPTS/sft_train.py" ]       || fail "sft_train.py missing: $SCRIPTS"
[ -f "$SCRIPTS/serve_eval_shim.py" ] || fail "serve_eval_shim.py missing: $SCRIPTS"
[ -f "$SCRIPTS/eval_aria_llm.py" ]   || fail "eval_aria_llm.py missing: $SCRIPTS"
[ -d "$HF_HOME/hub/models--mistralai--Mistral-7B-Instruct-v0.3" ] \
  || fail "Mistral base not in volume cache: $HF_HOME"
log "preflight ok — train $(wc -l < "$TRAIN_FILE") lines; eval $(wc -l < "$EVAL_SET") lines; epochs=$EPOCHS"
[ -d "$V02_ADAPTER" ] || log "WARN: v0.2 adapter missing ($V02_ADAPTER) — will eval v0.3 alone, no comparison"

# ── Deps: the EXACT pinned coherent set from train_promote_v0_2.sh (R-F1345) ───
# trl/datasets are REQUIRED for SFT training (baseline_pod_run dropped them as
# serve-only). sentencepiece+protobuf for Mistral's tokenizer; fastapi/uvicorn for
# the shim; httpx for the eval client. Do NOT pipe pip through tail (masks failure).
log "installing pinned train+serve+eval deps…"
pip install -q "transformers==4.46.3" "peft==0.13.2" "trl==0.12.2" \
    "accelerate>=0.34" bitsandbytes datasets sentencepiece protobuf \
    fastapi uvicorn httpx \
    || fail "dep install failed"
python - <<'PY' || fail "deps import check failed — aborting before train"
import transformers, peft, trl, bitsandbytes, accelerate  # noqa
import sentencepiece, google.protobuf  # noqa — Mistral tokenizer
import fastapi, uvicorn, httpx  # noqa — shim + eval client
from trl import SFTTrainer, SFTConfig  # noqa
print(f"deps ok: transformers {transformers.__version__} peft {peft.__version__} trl {trl.__version__}")
PY

# ── SFT train (QLoRA, single A100) ─────────────────────────────────────────────
log "SFT training v0.3 → $SFT_OUT (epochs=$EPOCHS, 4-bit) …"
python "$SCRIPTS/sft_train.py" \
  --base-model "$BASE_MODEL" \
  --train-file "$TRAIN_FILE" \
  --output-dir "$SFT_OUT" \
  --epochs "$EPOCHS" \
  --max-seq-len 4096 \
  --load-in-4bit 2>&1 | tee "$LOGS/sft_train_v0_3.log"

if [ ! -d "$SFT_OUT" ] || [ -z "$(ls -A "$SFT_OUT" 2>/dev/null)" ]; then
  fail "SFT training produced no checkpoint at $SFT_OUT — see $LOGS/sft_train_v0_3.log"
fi
# A LoRA adapter must include adapter_config.json — guard against a half-write.
[ -f "$SFT_OUT/adapter_config.json" ] || fail "no adapter_config.json in $SFT_OUT — training did not save a LoRA"
log "SFT complete — adapter saved to $SFT_OUT"

# ── Serve v0.3 → coherence smoke → eval (500-Q, judge) ─────────────────────────
serve "$SFT_OUT" "aria-llm-v0.3" || fail "could not serve v0.3"
log "coherence smoke (v0.3) before the full eval…"
COH=$(coherence_ok "aria-llm-v0.3"); log "coherence(v0.3): $COH"
[ "$COH" = "COHERENT" ] || fail "v0.3 is DEGENERATE (coherence gate) — inspect $LOGS/sft_train_v0_3.log + $SFT_OUT"
log "eval v0.3 (500-Q, judge) against localhost…"
eval_model "aria-llm-v0.3" "$V03_REPORT" || fail "v0.3 eval failed"

# ── Serve v0.2 → eval (SAME 500-Q + judge — apples-to-apples) ──────────────────
if [ -d "$V02_ADAPTER" ]; then
  serve "$V02_ADAPTER" "aria-llm-v0.2" || log "WARN: v0.2 would not serve — skipping comparison leg"
  if curl -s --max-time 5 "http://localhost:$PORT/v1/models" | grep -q "aria-llm-v0.2"; then
    log "eval v0.2 (500-Q, judge) against localhost…"
    eval_model "aria-llm-v0.2" "$V02_REPORT" || log "WARN: v0.2 eval failed — see log"
  fi
fi

# ── DeepSeek baseline (same set, same instrument) — optional ───────────────────
if [ -n "${DEEPSEEK_API_KEY:-}" ]; then
  log "eval DeepSeek baseline (deepseek-chat)…"
  python "$SCRIPTS/eval_aria_llm.py" \
    --target "https://api.deepseek.com/v1" --model "deepseek-chat" \
    --api-key "$DEEPSEEK_API_KEY" \
    --eval-set "$EVAL_SET" --out "$EVAL_DIR/deepseek_baseline_eval.json" 2>&1 \
    | tee "$LOGS/eval_deepseek.log" || log "WARN: DeepSeek baseline eval failed"
fi

# ── Verdict (Friday-style): PROMOTE v0.3 only if it does not regress v0.2 ───────
log "=== v0.3 CYCLE VERDICT ==="
python - "$V03_REPORT" "$V02_REPORT" <<'PY'
import json, os, sys
def load(p):
    try: return json.load(open(p, encoding="utf-8"))
    except Exception: return None
def dd(r): return ((r or {}).get("defence_dd") or (r or {}).get("dd_eval") or {})
def pi(r): return ((r or {}).get("prompt_injection") or {})
v3, v2 = load(sys.argv[1]), load(sys.argv[2])
a3, n3 = dd(v3).get("accuracy"), dd(v3).get("total")
a2, n2 = dd(v2).get("accuracy"), dd(v2).get("total")
l3, l2 = pi(v3).get("leak_rate"), pi(v2).get("leak_rate")
print(f"v0.3 judge-DD: {a3} (n={n3}) | injection leak_rate={l3}")
print(f"v0.2 judge-DD: {a2} (n={n2}) | injection leak_rate={l2}")
if a3 is None:
    print("VERDICT: INCOMPLETE — v0.3 report missing/invalid."); sys.exit(0)
if a2 is None:
    print("VERDICT: v0.3 evaluated; no valid v0.2 comparison this run."); sys.exit(0)
acc_ok = a3 >= a2
leak_ok = (l3 is None or l2 is None) or (l3 <= l2)
if acc_ok and leak_ok:
    print(f"VERDICT: PROMOTE v0.3 ✅ (acc {a3:.3f} >= {a2:.3f}; leak ok). First distillation cycle moved the number.")
else:
    why = []
    if not acc_ok: why.append(f"acc regressed ({a3:.3f} < {a2:.3f})")
    if not leak_ok: why.append(f"leak regressed ({l3} > {l2})")
    print(f"VERDICT: KEEP v0.2 — v0.3 did not clear the bar ({'; '.join(why)}).")
PY

pkill -f serve_eval_shim 2>/dev/null || true
log "done — shim stopped. (Pod stop is the orchestrator's EXIT trap.)"
