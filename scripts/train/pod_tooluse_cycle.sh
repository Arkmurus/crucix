#!/usr/bin/env bash
# R-F3413 — FULL tool-use cycle on the pod: SFT, then eval BASE and TRAINED.
#
# WHY BOTH SIDES. An absolute honesty rate says almost nothing on its own: it
# folds together what the base model already did and what training changed. The
# base is therefore evaluated FIRST, on the identical held-out rows through the
# identical scorer, so the reported result is a DELTA. A number without its
# baseline is the kind of figure that gets quoted for months and means nothing.
#
# BASELINE BEFORE TRAINING IS NOT AN OPTION. If the run dies after SFT, a
# baseline already exists and the hours are not wasted; the reverse ordering
# would leave a trained adapter with nothing to compare it to.
#
# ALWAYS WRITES THE SENTINEL (EXIT trap) so the on-pod self-stop watcher fires.
# R-F3400 gave that watcher an absolute deadline because a SIGKILL skips this
# trap entirely.
set -uo pipefail

# R-F3414 - the runner owns its own working directory. It used to be launched as
# `cd /workspace/crucix && ... setsid nohup bash ... &`, and that wrapper is what
# hung the launch: `&` backgrounds the whole `cd && ...` AND-LIST, which bash runs
# in a SUBSHELL, and only the inner command carried redirects. The subshell held
# ssh's stdout open, ssh never saw EOF, and the call sat there until the 75s TSSH
# timeout and was reported as a bare "FATAL launch". Owning the cwd here lets the
# launch line redirect EVERY fd, which is what allows ssh to return.
cd /workspace/crucix 2>/dev/null || true
export PYTHONPATH="/workspace/crucix${PYTHONPATH:+:$PYTHONPATH}"

BASE_MODEL="${BASE_MODEL:-mistralai/Mistral-7B-Instruct-v0.3}"
TRAIN_FILE="${TRAIN_FILE:-/workspace/datasets/aria_tooluse_train.jsonl}"
EVAL_FILE="${EVAL_FILE:-/workspace/datasets/aria_tooluse_eval.jsonl}"
GEN_FILE="${GEN_FILE:-$TRAIN_FILE}"
OUT_DIR="${OUT_DIR:-/workspace/checkpoints/aria_tooluse_v1}"
EPOCHS="${EPOCHS:-3}"
MAX_SEQ="${MAX_SEQ:-4096}"
PORT="${PORT:-8888}"
STATUS=/workspace/eval/_cycle_status
LOGS=/workspace/logs
EVALD=/workspace/eval
mkdir -p "$LOGS" "$EVALD" "$(dirname "$OUT_DIR")"

log(){ echo "[$(date -u +%H:%M:%S)] $*"; }
finish(){ rc=$?; echo "$rc" > "$STATUS"; log "cycle exit rc=$rc (sentinel written)"; }
trap finish EXIT

SHIM_PID=""
stop_shim(){ [ -n "$SHIM_PID" ] && kill "$SHIM_PID" 2>/dev/null; SHIM_PID=""; sleep 5; }

# Serve, wait for readiness, evaluate, tear down. A shim that never becomes
# ready is a FAILURE of that half, not a reason to score zero rows and call it a
# result — the caller checks the report was written.
serve_and_eval(){                      # $1 = adapter path or "", $2 = out json, $3 = tag
  local adapter="$1" out="$2" tag="$3"
  log "serving $tag (adapter='${adapter:-none}')…"
  BASE_MODEL="$BASE_MODEL" ADAPTER="$adapter" MODEL_NAME="aria-tooluse" PORT="$PORT" \
    python /workspace/crucix/scripts/train/serve_eval_shim.py >"$LOGS/shim_$tag.log" 2>&1 &
  SHIM_PID=$!
  local ready=0 i
  for i in $(seq 1 90); do
    if curl -sf "http://127.0.0.1:$PORT/v1/models" >/dev/null 2>&1; then ready=1; break; fi
    kill -0 "$SHIM_PID" 2>/dev/null || { log "FATAL shim died — see $LOGS/shim_$tag.log"; return 1; }
    sleep 10
  done
  [ "$ready" = 1 ] || { log "FATAL shim never became ready ($tag)"; stop_shim; return 1; }
  log "shim ready — evaluating $tag over $(wc -l < "$EVAL_FILE") held-out rows…"
  # R-F4372 (C-317) — the SCORER is selectable. eval_tooluse scores DD honesty
  # (citations, sanctions verdicts, no-false-clean); a coder trajectory has no
  # subject and no sources, so scoring one with the other reports a number about
  # a different capability. Default unchanged, so the DD cycle is untouched.
  python "/workspace/crucix/${EVAL_SCRIPT:-scripts/train/eval_tooluse.py}" \
    --eval-file "$EVAL_FILE" --target "http://127.0.0.1:$PORT/v1" \
    --model aria-tooluse --out "$out"
  local rc=$?
  stop_shim
  [ -s "$out" ] || { log "FATAL no report written for $tag"; return 1; }
  return "$rc"
}

log "=== ARIA tool-use FULL cycle ==="
[ -s "$TRAIN_FILE" ] || { log "FATAL train file missing: $TRAIN_FILE"; exit 1; }
[ -s "$EVAL_FILE" ]  || { log "FATAL eval file missing: $EVAL_FILE"; exit 1; }
log "train $(wc -l < "$TRAIN_FILE") rows | eval $(wc -l < "$EVAL_FILE") rows | epochs=$EPOCHS"

log "installing pinned deps…"
pip install -q "transformers==4.46.3" "peft==0.13.2" "trl==0.12.2" \
    "accelerate>=0.34" bitsandbytes datasets sentencepiece protobuf \
    fastapi uvicorn httpx || { log "FATAL dep install"; exit 1; }

# R-F3718 — prove the complete paid recipe before loading model weights. A
# previous cycle trained an adapter successfully and only then discovered that
# its serving environment lacked uvicorn. Train -> serve -> evaluate is one
# capability and must pass as one.
log "verifying train -> serve -> eval runtime..."
python - <<'PY' || { log "FATAL runtime recipe preflight"; exit 1; }
import importlib
import sys

required = (
    "accelerate", "bitsandbytes", "datasets", "fastapi", "httpx", "peft",
    "torch", "transformers", "trl", "uvicorn",
)
failed = []
for name in required:
    try:
        importlib.import_module(name)
    except Exception as exc:
        failed.append(f"{name}: {type(exc).__name__}: {exc}")
if failed:
    print("runtime imports failed:\n- " + "\n- ".join(failed), file=sys.stderr)
    raise SystemExit(1)

import torch
if not torch.cuda.is_available():
    print("CUDA unavailable - refusing paid GPU training", file=sys.stderr)
    raise SystemExit(1)
if not torch.cuda.is_bf16_supported():
    print("GPU does not support bf16 required by this recipe", file=sys.stderr)
    raise SystemExit(1)
print(f"runtime OK: CUDA {torch.version.cuda}, GPU {torch.cuda.get_device_name(0)}")
PY

log "verifying base architecture…"
# R-F4372 (C-317) — the signature is DECLARED, not hardcoded, so a deliberate
# base change is possible and an ACCIDENTAL one is still caught. The default is
# the Mistral signature this gate has always enforced, so an unset variable
# behaves exactly as before — and an EMPTY one falls back to that default rather
# than matching everything, because a guard you can disable by clearing a
# variable is not a guard.
python - "$BASE_MODEL" "${BASE_SIGNATURE:-}" <<'PY' || { log "FATAL base mismatch"; exit 1; }
import json
import sys
from transformers import AutoConfig

MISTRAL = {"model_type": "mistral", "vocab_size": 32768,
           "num_hidden_layers": 32, "hidden_size": 4096,
           "intermediate_size": 14336}
raw = (sys.argv[2] or "").strip()
try:
    want = json.loads(raw) if raw else MISTRAL
except Exception as exc:
    print(f"BASE_SIGNATURE is not JSON ({exc}) - refusing to run unguarded",
          file=sys.stderr)
    sys.exit(1)
if not isinstance(want, dict) or not want:
    print("BASE_SIGNATURE must be a non-empty object - refusing to run "
          "unguarded", file=sys.stderr)
    sys.exit(1)

cfg = AutoConfig.from_pretrained(sys.argv[1])
bad = {k: (getattr(cfg, k, None), v) for k, v in want.items()
       if getattr(cfg, k, None) != v}
if bad:
    print(f"BASE MISMATCH: {bad}", file=sys.stderr); sys.exit(1)
print(f"base OK: signature confirmed for {sys.argv[1]} ({want.get('model_type')})")
PY

# ---- 1. BASELINE FIRST -----------------------------------------------------
serve_and_eval "" "$EVALD/tooluse_eval_base.json" "base" \
  || { log "FATAL baseline eval failed"; exit 1; }
log "baseline recorded"

# ---- 2. SFT ----------------------------------------------------------------
log "SFT → $OUT_DIR (epochs=$EPOCHS, 4-bit train, max_seq=$MAX_SEQ)…"
python /workspace/crucix/scripts/train/sft_train.py \
  --base-model "$BASE_MODEL" --train-file "$TRAIN_FILE" --output-dir "$OUT_DIR" \
  --epochs "$EPOCHS" --max-seq-len "$MAX_SEQ" --load-in-4bit 2>&1 | tee "$LOGS/sft.log"
[ -f "$OUT_DIR/adapter_config.json" ] || { log "FATAL no LoRA produced"; exit 1; }
log "adapter written"
# Trainer checkpoint-* directories contain duplicate adapters plus optimizer
# state. They are useful for resuming SFT, but not for serving or DPO from the
# completed adapter, and made the recovery archive 2.1 GB in R-F3744. Persist
# only the final serving artifact so a slow host link cannot strand it.
tar -C "$(dirname "$OUT_DIR")" -czf "$EVALD/aria_tooluse_candidate_adapter.tgz" \
  --exclude="$(basename "$OUT_DIR")/checkpoint-*" \
  "$(basename "$OUT_DIR")" || { log "FATAL adapter archive failed"; exit 1; }
[ -s "$EVALD/aria_tooluse_candidate_adapter.tgz" ] \
  || { log "FATAL adapter archive empty"; exit 1; }
log "adapter archive staged for harvest"

# ---- 3. TRAINED ------------------------------------------------------------
serve_and_eval "$OUT_DIR" "$EVALD/tooluse_eval_trained.json" "trained" \
  || { log "FATAL trained eval failed"; exit 1; }

# ---- 3b. GENERATE OVER THE TRAIN SPLIT, for preference pairs ---------------
# R-F3432. DPO needs the model's OWN fabrications as the rejected side, and they
# must come from the TRAIN split: pairs built from eval failures would train on
# the held-out set and destroy the only honest measure - invisibly, because the
# score would improve. This reuses the already-trained adapter, so it costs one
# generation pass rather than another train.
if [ "${GEN_TRAIN:-1}" = "1" ]; then
  log "generating over the TRAIN split for preference pairs..."
  BASE_MODEL="$BASE_MODEL" ADAPTER="$OUT_DIR" MODEL_NAME="aria-tooluse" PORT="$PORT"     python /workspace/crucix/scripts/train/serve_eval_shim.py >"$LOGS/shim_gen.log" 2>&1 &
  SHIM_PID=$!
  gen_ready=0
  for i in $(seq 1 90); do
    curl -sf "http://127.0.0.1:$PORT/v1/models" >/dev/null 2>&1 && { gen_ready=1; break; }
    kill -0 "$SHIM_PID" 2>/dev/null || break
    sleep 10
  done
  if [ "$gen_ready" = 1 ]; then
    # R-F3433 - BOUNDED. Measured 14.6s/row on the trained model, so all 488
    # train rows would take ~119 min and overrun the cycle deadline mid-pass.
    # At ~12% failure that still yields ~18 pairs per round, and the pass is
    # cumulative across cycles. Generating a partial set is fine; overrunning
    # the deadline would lose the measurement too.
    if python /workspace/crucix/scripts/train/eval_tooluse.py \
         --eval-file "$GEN_FILE" --target "http://127.0.0.1:$PORT/v1" \
         --limit "${GEN_LIMIT:-150}" --model aria-tooluse \
         --out "$EVALD/tooluse_train_generations.json" \
       && [ -s "$EVALD/tooluse_train_generations.json" ]; then
      log "train generations written"
    else
      log "WARN: train generation failed or wrote no report - no preference pairs this round"
    fi
  else
    # NOT fatal: the cycle's measured result stands on its own. A missing
    # generation pass means no NEW pairs this round, not a failed cycle.
    log "WARN: generation shim never became ready - no preference pairs this round"
  fi
  stop_shim
fi

# ---- 4. the comparison, computed here so the driver cannot fudge it --------
python - "$EVALD/tooluse_eval_base.json" "$EVALD/tooluse_eval_trained.json" \
        > "$EVALD/tooluse_cycle_result.json" <<'PY'
import json, sys
b = json.load(open(sys.argv[1], encoding="utf-8"))
t = json.load(open(sys.argv[2], encoding="utf-8"))
per = {a["label"]: a for a in b["per_axis"]}
axes = []
for a in t["per_axis"]:
    bb = per.get(a["label"], {})
    axes.append({"label": a["label"], "total": a["total"],
                 "base_rate": bb.get("honest_rate"), "trained_rate": a["honest_rate"]})
print(json.dumps({
    "cycle": "tooluse_full_v1",
    "n_eval": t["total"],
    "base_honest": b["honest"], "base_rate": b["honest_rate"],
    "trained_honest": t["honest"], "trained_rate": t["honest_rate"],
    "delta": (None if (t["honest_rate"] is None or b["honest_rate"] is None)
              else round(t["honest_rate"] - b["honest_rate"], 4)),
    "per_axis": axes,
    "trained_failure_classes": t["failure_classes"][:8],
    "note": "scored by the corpus's own validator; a pass means the answer "
            "would have been accepted into the corpus",
}, indent=2))
PY
log "=== RESULT ==="; cat "$EVALD/tooluse_cycle_result.json"
log "=== cycle complete ==="
