#!/usr/bin/env bash
# R-F3401 — SMOKE CYCLE, on-pod runner. Proves the tool-use SFT pipeline runs
# end to end on real hardware, for the price of a coffee, BEFORE any full run.
#
# WHAT THIS IS FOR. Every check that can be made without a GPU is already made
# on the free side of the spend (R-F3395 preflight: schema, subjects, split
# disjointness, golden contamination, real-tokenizer rendering, token budget,
# base-model signature). What remains unproven is only what needs the hardware:
# that the deps resolve, the 4-bit base loads, TRL accepts the rendered column,
# and a LoRA adapter actually lands on disk. This run answers exactly that.
#
# WHAT IT IS NOT. It is not a capability measurement. 338 rows over one epoch
# tells you the pipeline is correct, not that ARIA improved. Do not read a loss
# curve from this as evidence about the model.
#
# ALWAYS WRITES THE SENTINEL. The EXIT trap records the return code to
# _cycle_status, which the on-pod self-stop watcher waits for. R-F3400 gave that
# watcher an absolute deadline precisely because a SIGKILL skips this trap.
set -uo pipefail

BASE_MODEL="${BASE_MODEL:-mistralai/Mistral-7B-Instruct-v0.3}"
TRAIN_FILE="${TRAIN_FILE:-/workspace/datasets/aria_tooluse_train.jsonl}"
EVAL_FILE="${EVAL_FILE:-/workspace/datasets/aria_tooluse_eval.jsonl}"
OUT_DIR="${OUT_DIR:-/workspace/checkpoints/aria_tooluse_smoke}"
EPOCHS="${EPOCHS:-1}"
MAX_SEQ="${MAX_SEQ:-4096}"
STATUS=/workspace/eval/_cycle_status
LOGS=/workspace/logs
mkdir -p "$LOGS" /workspace/eval "$(dirname "$OUT_DIR")"

log(){ echo "[$(date -u +%H:%M:%S)] $*"; }
finish(){ rc=$?; echo "$rc" > "$STATUS"; log "smoke cycle exit rc=$rc (sentinel written)"; }
trap finish EXIT

log "=== ARIA tool-use SFT smoke cycle ==="
[ -s "$TRAIN_FILE" ] || { log "FATAL: train file missing/empty: $TRAIN_FILE"; exit 1; }
[ -s "$EVAL_FILE" ]  || { log "FATAL: eval file missing/empty: $EVAL_FILE"; exit 1; }
log "train $(wc -l < "$TRAIN_FILE") rows | eval $(wc -l < "$EVAL_FILE") rows"

log "installing pinned deps…"
pip install -q "transformers==4.46.3" "peft==0.13.2" "trl==0.12.2" \
    "accelerate>=0.34" bitsandbytes datasets sentencepiece protobuf \
    || { log "FATAL dep install"; exit 1; }
python - <<'PY' || { log "FATAL dep import"; exit 1; }
import transformers, peft, trl, bitsandbytes, accelerate  # noqa
from trl import SFTTrainer, SFTConfig  # noqa
print(f"deps ok: transformers {transformers.__version__} peft {peft.__version__} trl {trl.__version__}")
PY

# The same architecture signature the local pre-flight checks — re-checked here
# because the pod resolves the model name against the Hub independently, and a
# name compare would pass on a fork or a mirror that then fails at load.
log "verifying base architecture…"
python - "$BASE_MODEL" <<'PY' || { log "FATAL base mismatch"; exit 1; }
import sys
from transformers import AutoConfig
cfg = AutoConfig.from_pretrained(sys.argv[1])
want = {"model_type": "mistral", "vocab_size": 32768, "num_hidden_layers": 32,
        "hidden_size": 4096, "intermediate_size": 14336}
bad = {k: (getattr(cfg, k, None), v) for k, v in want.items() if getattr(cfg, k, None) != v}
if bad:
    print(f"BASE MISMATCH (got, want): {bad}", file=sys.stderr); sys.exit(1)
print("base OK: Mistral-7B-Instruct-v0.3 signature confirmed")
PY

# Prove the tool calls survive the trainer's OWN data path on this machine.
# The local run proved it against the local tokenizer; this proves the pod's
# copy of the template does the same, before an hour of GPU is spent on it.
log "verifying tool calls survive the trainer's render…"
TRAIN_FILE="$TRAIN_FILE" BASE_MODEL="$BASE_MODEL" python - <<'PY' || { log "FATAL render check"; exit 1; }
import json, os, importlib.util, sys
spec = importlib.util.spec_from_file_location("sft", "/workspace/crucix/scripts/train/sft_train.py")
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
from transformers import AutoTokenizer
tok = AutoTokenizer.from_pretrained(os.environ["BASE_MODEL"])
rows = [json.loads(l) for l in open(os.environ["TRAIN_FILE"], encoding="utf-8") if l.strip()]
tc = [r for r in rows if any(msg.get("tool_calls") for msg in r["messages"])]
lost = [i for i, x in enumerate(tc) if "[TOOL_CALLS]" not in m._render_text(tok, m._format_chat(x))]
print(f"rows={len(rows)} with_tool_calls={len(tc)} losing_tool_calls={len(lost)}")
if lost:
    print(f"FATAL: {len(lost)} rows lose their tool calls in the trainer's render", file=sys.stderr)
    sys.exit(1)
PY

log "SFT → $OUT_DIR (epochs=$EPOCHS, 4-bit, max_seq=$MAX_SEQ)…"
python /workspace/crucix/scripts/train/sft_train.py \
  --base-model "$BASE_MODEL" --train-file "$TRAIN_FILE" --output-dir "$OUT_DIR" \
  --epochs "$EPOCHS" --max-seq-len "$MAX_SEQ" --load-in-4bit \
  2>&1 | tee "$LOGS/smoke_sft.log"

[ -f "$OUT_DIR/adapter_config.json" ] || { log "FATAL: no LoRA produced at $OUT_DIR"; exit 1; }
log "adapter present: $(ls -la "$OUT_DIR" | wc -l) files"

# A machine-readable result the driver pulls before the pod dies. Container disk
# is ephemeral (volume-free per R-F1516), so anything not pulled is lost.
python - "$OUT_DIR" "$TRAIN_FILE" > /workspace/eval/aria_tooluse_smoke.json <<'PY'
import json, os, sys
out, train = sys.argv[1], sys.argv[2]
adapter = os.path.join(out, "adapter_model.safetensors")
print(json.dumps({
    "cycle": "tooluse_sft_smoke",
    "pipeline_ok": True,
    "adapter_bytes": os.path.getsize(adapter) if os.path.exists(adapter) else 0,
    "train_rows": sum(1 for l in open(train, encoding="utf-8") if l.strip()),
    "note": "pipeline proof only - NOT a capability measurement",
}, indent=2))
PY
log "=== smoke cycle complete ==="
