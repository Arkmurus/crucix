#!/usr/bin/env bash
# v0_4_pod_run — on-pod ARIA-LLM v0.4 SFT-distillation cycle (R-F1513/R-F1516).
#
# Mirrors v0_3_pod_run.sh (the PROVEN cycle that produced v0.3=0.22), trains v0.4
# on the FAILURE-MODE-WEIGHTED corpus (v0.3-clean + R-F1511 failure-modes).
#
# R-F1516 — VOLUME-FREE. The old build loaded the base from the US-KS-2 volume HF
# cache (offline) and served the v0.3 adapter from that volume. The volume was the
# trap (region-locked the pod to a DC that deleted pods mid-train), so we dropped
# it. Consequences handled here:
#   (1) Base downloads FRESH from an ungated mirror (default unsloth/mistral-7b-
#       instruct-v0.3, a bit-identical re-upload of the gated official repo) — no
#       HF token needed. A preflight asserts the architecture matches Mistral-7B
#       before we trust a single training step. Override with BASE_MODEL=... .
#   (2) No on-volume v0.3 adapter to re-serve → v0.4 is compared to the KNOWN
#       v0.3=0.22 (same eval harness, R-F1469) and the teacher ceiling 0.34.
#
# Serve via serve_eval_shim.py — NOT vLLM (this pod's driver is too old →
# EngineCore crash, R-F1455).
set -uo pipefail
# Ungated mirror by default (no HF token). Official gated repo via BASE_MODEL=... + HF_TOKEN.
BASE_MODEL="${BASE_MODEL:-unsloth/mistral-7b-instruct-v0.3}"  # bit-identical Mistral-7B-Instruct-v0.3 (R-F1516)
V03_BASELINE="${V03_BASELINE:-0.22}"                  # the champion number to beat (R-F1469, same harness)
TEACHER_CEILING="${TEACHER_CEILING:-0.34}"            # clean DeepSeek teacher (reference)
SFT_OUT="/workspace/checkpoints/aria_llm_v0_4_sft"    # v0.4 SFT adapter (produced here)
TRAIN_FILE="${TRAIN_FILE:-/workspace/datasets/aria_sft_distill_v04.jsonl}"
EVAL_SET="${EVAL_SET:-/workspace/datasets/aria_eval_500q.jsonl}"
EVAL_DIR="/workspace/eval"
LOGS="/workspace/logs"
SCRIPTS="/workspace/crucix/scripts/train"
PORT=8888
EPOCHS="${EPOCHS:-3}"
V04_REPORT="${EVAL_DIR}/aria_llm_v0_4_eval.json"
# R-F4347 (C-291) — PUT THE CACHE ON A DISK THAT HAS ROOM, AND FAIL FAST IF
# NONE DOES.
#
# This line hardcoded HF_HOME=/workspace/.cache/huggingface and its own comment
# called that "container disk". It is not: on the pod of record /workspace is a
# 20G VOLUME and / is a 120G overlay. Measured 2026-08-26 mid-failure:
#     /dev/md0   20G   20G  1.5M  100%  /workspace
#     overlay   120G   16M  120G    1%  /
# The base model is ~15G, so the download filled the volume and died with
# "OSError: No space left on device (os error 28)" AFTER pulling gigabytes —
# and because `export` (not ${HF_HOME:-...}) overwrote any inherited value, the
# cache could not be redirected from outside either.
#
# Worse, the driver still PRINTED A GATE VERDICT for the run: "judge-DD=0.3
# (n=500) | G1 accuracy: FAIL". No checkpoint existed — that number came from a
# stale local report, right after the log said "report not pulled". A training
# run that produced no model reported a score for it, which is the
# absence-reads-as-measurement shape §1 records for the Phase A gates.
#
# So: honour an inherited HF_HOME, otherwise pick whichever candidate has the
# most free space, and refuse to start when the winner cannot hold the model.
# Failing in one second with the number beats failing in ten minutes at ENOSPC.
# --- R-F4347:hf-cache BEGIN (extracted verbatim by test_rf4347) ---
# Candidates are overridable so the selection can be exercised against real
# directories in a test, and so a pod with a different disk layout needs no
# code edit. The defaults are the pod of record.
HF_CACHE_CANDIDATES="${HF_CACHE_CANDIDATES:-/workspace/.cache/huggingface /root/.cache/huggingface}"
_hf_free_mb(){ df -Pm "$1" 2>/dev/null | awk 'NR==2{print $4+0}'; }
if [ -z "${HF_HOME:-}" ]; then
  _best=""; _best_free=0
  for _cand in $HF_CACHE_CANDIDATES; do
    mkdir -p "$_cand" 2>/dev/null || continue
    _f=$(_hf_free_mb "$_cand"); [ -z "$_f" ] && _f=0
    if [ "$_f" -gt "$_best_free" ]; then _best="$_cand"; _best_free="$_f"; fi
  done
  export HF_HOME="${_best:-/workspace/.cache/huggingface}"
  echo "[hf-cache] HF_HOME=$HF_HOME (${_best_free} MB free)"
else
  mkdir -p "$HF_HOME" 2>/dev/null || true
  echo "[hf-cache] HF_HOME=$HF_HOME (inherited)"
fi
# ~15G for a 7B in bf16 plus room to unpack; overridable for a smaller base.
HF_MIN_FREE_MB="${HF_MIN_FREE_MB:-18000}"
_free=$(_hf_free_mb "$HF_HOME"); [ -z "$_free" ] && _free=0
if [ "$_free" -lt "$HF_MIN_FREE_MB" ]; then
  echo "[FATAL] HF_HOME=$HF_HOME has ${_free} MB free, need ${HF_MIN_FREE_MB} MB." >&2
  echo "        The base model is ~15G. Free space, or set HF_HOME to a bigger disk." >&2
  df -Pm / /workspace 2>/dev/null >&2
  exit 1
fi
# --- R-F4347:hf-cache END ---
# ONLINE — base must download fresh (no volume cache). HF_TOKEN honoured if set.
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
log "preflight ok — train $(wc -l < "$TRAIN_FILE") lines; eval $(wc -l < "$EVAL_SET") lines; epochs=$EPOCHS"
log "champion to beat: v0.3=$V03_BASELINE (R-F1469 harness); teacher ceiling=$TEACHER_CEILING"

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

# R-F1516: verify the (ungated-mirror) base IS Mistral-7B-Instruct-v0.3 before we
# trust ANY training on it. Downloads only config.json (~1KB). The v0.3 signature
# is vocab_size=32768 (v0.2 was 32000) + 32 layers + hidden 4096 — a mislabeled or
# wrong-version mirror fails here, NOT silently after a 1.5h train + a bad eval.
if [ "${ARIA_SKIP_ARCH_CHECK:-0}" = "1" ]; then
  # R-F1667: the v0.8 14B experiment uses a different base (e.g. Qwen2.5-14B).
  # The pipeline is model-agnostic (sft_train + serve use apply_chat_template +
  # AutoModel trust_remote_code), so skip the Mistral-7B signature guard here.
  log "ARCH CHECK SKIPPED (ARIA_SKIP_ARCH_CHECK=1) — base=$BASE_MODEL (non-Mistral experiment)"
else
log "verifying base architecture is Mistral-7B-Instruct-v0.3 ($BASE_MODEL) …"
python - "$BASE_MODEL" <<'PY' || fail "base-model architecture check failed — refusing to train on a non-v0.3 base"
import sys
from transformers import AutoConfig
cfg = AutoConfig.from_pretrained(sys.argv[1])
want = {"model_type": "mistral", "vocab_size": 32768, "num_hidden_layers": 32,
        "hidden_size": 4096, "intermediate_size": 14336}
bad = {k: (getattr(cfg, k, None), v) for k, v in want.items() if getattr(cfg, k, None) != v}
if bad:
    print(f"BASE MISMATCH (got, want): {bad}", file=sys.stderr); sys.exit(1)
print(f"base OK: Mistral-7B-Instruct-v0.3 signature confirmed (vocab=32768, 32L, 4096d)")
PY
fi

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

# R-F1516: no on-volume v0.3 adapter to re-serve — v0.4 is compared to the KNOWN
# v0.3=$V03_BASELINE (same eval set + same judge, R-F1469), not a live re-eval.

if [ -n "${DEEPSEEK_API_KEY:-}" ] && [ -z "${SKIP_TEACHER_EVAL:-}" ]; then  # R-F1540: SKIP_TEACHER_EVAL=1 halves the run
  log "eval DeepSeek teacher baseline (deepseek-chat, ceiling reference)…"
  python "$SCRIPTS/eval_aria_llm.py" \
    --target "https://api.deepseek.com/v1" --model "deepseek-chat" \
    --api-key "$DEEPSEEK_API_KEY" \
    --eval-set "$EVAL_SET" --out "$EVAL_DIR/deepseek_baseline_eval.json" 2>&1 \
    | tee "$LOGS/eval_deepseek.log" || log "WARN: DeepSeek baseline eval failed"
fi

log "=== v0.4 CYCLE VERDICT ==="
V03_BASELINE="$V03_BASELINE" TEACHER_CEILING="$TEACHER_CEILING" \
python - "$V04_REPORT" <<'PY'
import json, sys, os
def load(p):
    try: return json.load(open(p, encoding="utf-8"))
    except Exception: return None
def dd(r): return ((r or {}).get("defence_dd") or (r or {}).get("dd_eval") or {})
def pi(r): return ((r or {}).get("prompt_injection") or {})
v4 = load(sys.argv[1])
a3 = float(os.environ.get("V03_BASELINE", "0.22"))      # known champion (R-F1469 harness)
ceiling = float(os.environ.get("TEACHER_CEILING", "0.34"))
a4, n4 = dd(v4).get("accuracy"), dd(v4).get("total")
l4 = pi(v4).get("leak_rate")
print(f"v0.4 judge-DD: {a4} (n={n4}) | injection leak_rate={l4}")
print(f"v0.3 champion (known): {a3}  | teacher ceiling = {ceiling}")
if a4 is None:
    print("VERDICT: INCOMPLETE — v0.4 report missing/invalid."); sys.exit(0)
if a4 >= a3:
    pct = (a4 - a3) / a3 * 100 if a3 else 0.0
    print(f"VERDICT: PROMOTE v0.4 ✅ (acc {a4:.3f} >= {a3:.3f}, +{pct:.0f}% rel). Failure-mode distillation moved the number toward {ceiling}.")
else:
    print(f"VERDICT: KEEP v0.3 — v0.4 did not clear the bar (acc {a4:.3f} < {a3:.3f}).")
PY

pkill -f serve_eval_shim 2>/dev/null || true
log "done — shim stopped. (Pod stop is the orchestrator's EXIT trap.)"
