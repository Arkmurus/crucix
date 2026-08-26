#!/usr/bin/env bash
# R-F4336 (C-281) — launch the sovereign vLLM so the adapter is ADDRESSABLE.
#
# WHY THIS FILE EXISTS. On 2026-08-25 the CLI served the untuned base model for
# an unknown period because the running server had been started ad-hoc, over
# SSH, with the base and the LoRA sharing ONE served name. Confirmed from the
# live process cmdline:
#
#   --served-model-name aria-llm-v0.4-dpo \
#   --lora-modules      aria-llm-v0.4-dpo=/root/adapters/aria_llm_v0_4_dpo
#
# vLLM resolves an incoming `model` against base served-names BEFORE LoRA names,
# so the base won every request and the fine-tune was inert. The launch command
# was NOT in the repo — which is precisely why it could drift. A tracked
# launcher that refuses the bad shape is the root fix; a one-off correct
# relaunch would drift again the next time someone typed it by hand.
#
# R-F4344 — THE FIRST VERSION OF THIS SCRIPT WOULD HAVE BROKEN TOOL CALLING.
# It omitted `--enable-auto-tool-choice` and `--tool-call-parser`, which the
# live server carries and without which vLLM never emits a `tool_calls` block at
# all: every turn would have come back as prose and looked exactly like the
# model being bad. Caught by diffing the running cmdline before relaunching
# rather than trusting the script. It also defaulted --max-lora-rank to 64 while
# the adapter is r=32. The lesson is the general one: a replacement launcher must
# be diffed against the process it replaces, not written from first principles.
set -euo pipefail

BASE_MODEL="${BASE_MODEL:?BASE_MODEL is required (path or HF id of the base weights)}"
ADAPTER_PATH="${ADAPTER_PATH:?ADAPTER_PATH is required (e.g. /root/adapters/aria_llm_v0_4_dpo)}"
# The operator-facing id. This is what ARIA_LLM_MODEL must be set to, and it
# must name the ADAPTER — that is the whole point.
SERVED_NAME="${SERVED_NAME:?SERVED_NAME is required (e.g. aria-llm-v0.4-dpo)}"
# The base keeps its OWN distinct name so the two can never collide.
BASE_NAME="${BASE_NAME:-aria-llm-base}"
# Mistral's template lives with the base weights; keep tokenizer and model in step.
TOKENIZER="${TOKENIZER:-$BASE_MODEL}"
# Without these vLLM returns tool calls as PROSE and the agent makes zero calls.
TOOL_PARSER="${TOOL_PARSER:-mistral}"
PORT="${PORT:-8888}"
MAXLEN="${MAXLEN:-32768}"
GPU_UTIL="${GPU_UTIL:-0.90}"
# Must be >= the adapter's own `r` (read it from adapter_config.json if unsure).
LORA_RANK="${LORA_RANK:-32}"

# ---- the guard, before anything expensive -----------------------------------
if [ "$BASE_NAME" = "$SERVED_NAME" ]; then
  echo "REFUSING TO START: base name and adapter name are both '${SERVED_NAME}'." >&2
  echo "  A shared id makes the adapter unaddressable and silently serves the" >&2
  echo "  BASE model. Set BASE_NAME to something else (default: aria-llm-base)." >&2
  exit 2
fi
if [ ! -d "$ADAPTER_PATH" ]; then
  echo "REFUSING TO START: ADAPTER_PATH '${ADAPTER_PATH}' is not a directory." >&2
  echo "  Serving with a missing adapter would fall back to the base model," >&2
  echo "  which is the failure this launcher exists to prevent." >&2
  exit 2
fi
# The adapter's rank must fit under --max-lora-rank or vLLM refuses to load it
# and (worse) can fall back to serving the base alone.
if [ -f "${ADAPTER_PATH}/adapter_config.json" ]; then
  _r="$(python3 -c "import json;print(json.load(open('${ADAPTER_PATH}/adapter_config.json')).get('r',0))" 2>/dev/null || echo 0)"
  if [ "${_r:-0}" -gt "${LORA_RANK}" ]; then
    echo "REFUSING TO START: adapter rank r=${_r} exceeds --max-lora-rank ${LORA_RANK}." >&2
    exit 2
  fi
fi

pkill -f jupyter 2>/dev/null || true
pkill -f vllm.entrypoints.openai 2>/dev/null || true
sleep 5

mkdir -p /workspace/logs
nohup python -m vllm.entrypoints.openai.api_server \
  --model "$BASE_MODEL" \
  --tokenizer "$TOKENIZER" \
  --served-model-name "$BASE_NAME" \
  --enable-lora \
  --lora-modules "${SERVED_NAME}=${ADAPTER_PATH}" \
  --max-lora-rank "$LORA_RANK" \
  --max-model-len "$MAXLEN" \
  --gpu-memory-utilization "$GPU_UTIL" \
  --enable-auto-tool-choice \
  --tool-call-parser "$TOOL_PARSER" \
  --host 0.0.0.0 --port "$PORT" \
  > /workspace/logs/vllm_sovereign.log 2>&1 &

echo "vLLM starting on :${PORT} — base='${BASE_NAME}' adapter='${SERVED_NAME}'"

# ---- verify the inventory, because starting is not serving -------------------
for _ in $(seq 1 90); do
  sleep 10
  body="$(curl -s -m 10 "http://127.0.0.1:${PORT}/v1/models" || true)"
  [ -n "$body" ] || continue
  n="$(printf '%s' "$body" | python3 -c "
import json,sys
try: d=json.load(sys.stdin)
except Exception: print(-1); raise SystemExit
print(sum(1 for e in d.get('data',[]) if e.get('id')=='${SERVED_NAME}'))" 2>/dev/null || echo -1)"
  case "$n" in
    1) echo "OK: '${SERVED_NAME}' resolves to exactly one model — adapter is addressable."; exit 0 ;;
    0) continue ;;                       # still booting
    -1) continue ;;                      # inventory not parseable yet
    *) echo "FAILED: '${SERVED_NAME}' matches ${n} served models — collision." >&2
       echo "  The adapter is NOT addressable. Check --served-model-name." >&2
       exit 3 ;;
  esac
done
echo "FAILED: '${SERVED_NAME}' never appeared in the model inventory." >&2
exit 4
