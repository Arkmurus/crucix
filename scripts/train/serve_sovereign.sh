#!/usr/bin/env bash
# R-F4336 (C-281) — launch the sovereign vLLM so the adapter is ADDRESSABLE.
#
# WHY THIS FILE EXISTS. On 2026-08-25 the CLI served the untuned base model for
# an unknown period because the running server had been started ad-hoc, over
# SSH, with the base and the LoRA sharing ONE served name:
#
#   --served-model-name aria-llm-v0.4-dpo \
#   --lora-modules      aria-llm-v0.4-dpo=/root/adapters/aria_llm_v0_4_dpo
#
# vLLM resolves an incoming `model` against base served-names BEFORE LoRA names,
# so the base won every request and the fine-tune was inert. The launch command
# was NOT in the repo — which is precisely why it could drift. A tracked
# launcher that refuses the bad shape is the root fix; a one-off correct
# relaunch would drift again the next time someone typed it by hand.
set -euo pipefail

BASE_MODEL="${BASE_MODEL:-mistralai/Mistral-7B-Instruct-v0.3}"
ADAPTER_PATH="${ADAPTER_PATH:?ADAPTER_PATH is required (e.g. /root/adapters/aria_llm_v0_4_dpo)}"
# The operator-facing id. This is what ARIA_LLM_MODEL must be set to, and it
# must name the ADAPTER — that is the whole point.
SERVED_NAME="${SERVED_NAME:?SERVED_NAME is required (e.g. aria-llm-v0.4-dpo)}"
# The base keeps its OWN distinct name so the two can never collide.
BASE_NAME="${BASE_NAME:-aria-llm-base}"
PORT="${PORT:-8888}"
MAXLEN="${MAXLEN:-32768}"
LORA_RANK="${LORA_RANK:-64}"

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

pkill -f jupyter 2>/dev/null || true
pkill -f vllm.entrypoints.openai 2>/dev/null || true
sleep 2

mkdir -p /workspace/logs
nohup python -m vllm.entrypoints.openai.api_server \
  --model "$BASE_MODEL" \
  --served-model-name "$BASE_NAME" \
  --enable-lora \
  --lora-modules "${SERVED_NAME}=${ADAPTER_PATH}" \
  --max-loras 1 \
  --max-lora-rank "$LORA_RANK" \
  --max-model-len "$MAXLEN" \
  --gpu-memory-utilization 0.9 \
  --host 0.0.0.0 --port "$PORT" \
  > /workspace/logs/vllm_sovereign.log 2>&1 &

echo "vLLM starting on :${PORT} — base='${BASE_NAME}' adapter='${SERVED_NAME}'"

# ---- verify the inventory, because starting is not serving -------------------
for _ in $(seq 1 90); do
  sleep 10
  body="$(curl -s -m 10 "http://127.0.0.1:${PORT}/v1/models" || true)"
  [ -n "$body" ] || continue
  n="$(printf '%s' "$body" | python -c "
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
