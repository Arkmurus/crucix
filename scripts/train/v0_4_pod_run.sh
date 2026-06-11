#!/usr/bin/env bash
# v0_4_pod_run — on-pod ARIA-LLM v0.4 SFT-distillation cycle (R-F1513).
#
# Mirrors v0_3_pod_run.sh (the PROVEN cycle that produced v0.3=0.22) with two
# changes: (1) trains v0.4 on the FAILURE-MODE-WEIGHTED corpus (v0.3-clean +
# R-F1511 failure-modes), (2) the champion to beat is now v0.3 (it beat v0.2
# 0.22>0.154), so the comparison leg serves the v0.3 SFT adapter. DeepSeek
# (teacher, 0.34) is the reference ceiling.
#
# Serve via serve_eval_shim.py — NOT vLLM (this pod's driver is too old →
# EngineCore crash, R-F1455). Base loads from the volume HF cache, offline.
set -uo pipefail
BASE_MODEL="mistralai/Mistral-7B-Instruct-v0.3"        # v0.2/v0.3 base (R-F1454)
SFT_OUT="/workspace/checkpoints/aria_llm_v0_4_sft"     # v0.4 SFT adapter (produced here)
V03_ADAPTER="/workspace/checkpoints/aria_llm_v0_3_sft" # the champion: v0.3 SFT LoRA (on the volume)
TRAIN_FILE="${TRAIN_FILE:-/workspace/datasets/aria_sft_distill_v04.jsonl}"
EVAL_SET="${EVAL_SET:-/workspace/datasets/aria_eval_500q.jsonl}"
EVAL_DIR="/workspace/eval"
LOGS="/workspace/logs"
SCRIPTS="/workspace/crucix/scripts/train"
PORT=8888
EPOCHS="${EPOCHS:-3}"
V04_REPORT="${EVAL_DIR}/aria_llm_v0_4_eval.json"
V03_REPORT="${EVAL_DIR}/aria_llm_v0_3_eval_vs_v04.json"
export HF_HOME=/workspace/.cache/huggingface
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
mkdir -p "$EVAL_DIR" "$LOGS" "$(dirname "$SFT_OUT")"
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

log "=== ARIA-LLM v0.4 SFT-distillation cycle (failure-mode-weighted) ==="
[ -f "$TRAIN_FILE" ] || fail "train corpus missing: $TRAIN_FILE"
[ -s "$TRAIN_FILE" ] || fail "train corpus empty: $TRAIN_FILE"
[ -f "$EVAL_SET" ]   || fail "eval set missing: $EVAL_SET"
[ -f "$SCRIPTS/sft_train.py" ]       || fail "sft_train.py missing: $SCRIPTS"
[ -f "$SCRIPTS/serve_eval_shim.py" ] || fail "serve_eval_shim.py missing: $SCRIPTS"
[ -f "$SCRIPTS/eval_aria_llm.py" ]   || fail "eval_aria_llm.py missing: $SCRIPTS"
[ -d "$HF_HOME/hub/models--mistralai--Mistral-7B-Instruct-v0.3" ] \
  || fail "Mistral base not in volume cache: $HF_HOME"
log "preflight ok — train $(wc -l < "$TRAIN_FILE") lines; eval $(wc -l < "$EVAL_SET") lines; epochs=$EPOCHS"
[ -d "$V03_ADAPTER" ] || log "WARN: v0.3 adapter missing ($V03_ADAPTER) — will eval v0.4 alone, no champion comparison"

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

log "SFT training v0.4 → $SFT_OUT (epochs=$EPOCHS, 4-bit) …"
python "$SCRIPTS/sft_train.py" \
  --base-model "$BASE_MODEL" \
  --train-file "$TRAIN_FILE" \
  --output-dir "$SFT_OUT" \
  --epochs "$EPOCHS" \
  --max-seq-len 4096 \
  --load-in-4bit 2>&1 | tee "$LOGS/sft_train_v0_4.log"
if [ ! -d "$SFT_OUT" ] || [ -z "$(ls -A "$SFT_OUT" 2>/dev/null)" ]; then
  fail "SFT training produced no checkpoint at $SFT_OUT — see $LOGS/sft_train_v0_4.log"
fi
[ -f "$SFT_OUT/adapter_config.json" ] || fail "no adapter_config.json in $SFT_OUT — training did not save a LoRA"
log "SFT complete — adapter saved to $SFT_OUT"

serve "$SFT_OUT" "aria-llm-v0.4" || fail "could not serve v0.4"
log "coherence smoke (v0.4) before the full eval…"
COH=$(coherence_ok "aria-llm-v0.4"); log "coherence(v0.4): $COH"
[ "$COH" = "COHERENT" ] || fail "v0.4 is DEGENERATE (coherence gate) — inspect $LOGS/sft_train_v0_4.log + $SFT_OUT"
log "eval v0.4 (500-Q, judge) against localhost…"
eval_model "aria-llm-v0.4" "$V04_REPORT" || fail "v0.4 eval failed"

if [ -d "$V03_ADAPTER" ]; then
  serve "$V03_ADAPTER" "aria-llm-v0.3" || log "WARN: v0.3 would not serve — skipping champion comparison"
  if curl -s --max-time 5 "http://localhost:$PORT/v1/models" | grep -q "aria-llm-v0.3"; then
    log "eval v0.3 champion (500-Q, judge) against localhost…"
    eval_model "aria-llm-v0.3" "$V03_REPORT" || log "WARN: v0.3 eval failed — see log"
  fi
fi

if [ -n "${DEEPSEEK_API_KEY:-}" ]; then
  log "eval DeepSeek teacher baseline (deepseek-chat, ceiling reference)…"
  python "$SCRIPTS/eval_aria_llm.py" \
    --target "https://api.deepseek.com/v1" --model "deepseek-chat" \
    --api-key "$DEEPSEEK_API_KEY" \
    --eval-set "$EVAL_SET" --out "$EVAL_DIR/deepseek_baseline_eval.json" 2>&1 \
    | tee "$LOGS/eval_deepseek.log" || log "WARN: DeepSeek baseline eval failed"
fi

log "=== v0.4 CYCLE VERDICT ==="
python - "$V04_REPORT" "$V03_REPORT" <<'PY'
import json, sys
def load(p):
    try: return json.load(open(p, encoding="utf-8"))
    except Exception: return None
def dd(r): return ((r or {}).get("defence_dd") or (r or {}).get("dd_eval") or {})
def pi(r): return ((r or {}).get("prompt_injection") or {})
v4, v3 = load(sys.argv[1]), load(sys.argv[2])
a4, n4 = dd(v4).get("accuracy"), dd(v4).get("total")
a3, n3 = dd(v3).get("accuracy"), dd(v3).get("total")
l4, l3 = pi(v4).get("leak_rate"), pi(v3).get("leak_rate")
print(f"v0.4 judge-DD: {a4} (n={n4}) | injection leak_rate={l4}")
print(f"v0.3 judge-DD: {a3} (n={n3}) | injection leak_rate={l3}  (teacher ceiling = 0.34)")
if a4 is None:
    print("VERDICT: INCOMPLETE — v0.4 report missing/invalid."); sys.exit(0)
if a3 is None:
    print("VERDICT: v0.4 evaluated; no valid v0.3 comparison this run."); sys.exit(0)
acc_ok = a4 >= a3
leak_ok = (l4 is None or l3 is None) or (l4 <= l3)
if acc_ok and leak_ok:
    print(f"VERDICT: PROMOTE v0.4 ✅ (acc {a4:.3f} >= {a3:.3f}; leak ok). Failure-mode distillation moved the number toward 0.34.")
else:
    why = []
    if not acc_ok: why.append(f"acc regressed ({a4:.3f} < {a3:.3f})")
    if not leak_ok: why.append(f"leak regressed ({l4} > {l3})")
    print(f"VERDICT: KEEP v0.3 — v0.4 did not clear the bar ({'; '.join(why)}).")
PY

pkill -f serve_eval_shim 2>/dev/null || true
log "done — shim stopped. (Pod stop is the orchestrator's EXIT trap.)"
