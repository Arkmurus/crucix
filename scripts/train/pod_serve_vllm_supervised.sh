#!/bin/bash
# ARIA-LLM serving on vLLM â€” WITH TOOL CALLING.
#
# WHY THIS EXISTS: the fallback shim is a minimal chat server with no
# function-calling, so the coder CLI on it has NO read/edit/run tools â€” it
# degrades to a chat box (R-F2166 warns loudly about exactly this). Tools are
# the reason for vLLM.
#
# WHY A LOCAL PATH AND NOT THE REPO ID: pointing --model at
# "mistralai/Mistral-7B-Instruct-v0.3" made huggingface_hub RE-DOWNLOAD the
# weights even with HF_HUB_OFFLINE=1 (HF_HOME does not reach vLLM's engine
# subprocess), and the download landed on /workspace â€” a quota'd network FS â€”
# dying with "Disk quota exceeded (os error 122)". Passing the resolved snapshot
# directory removes hub resolution from the path entirely. The pod's own
# /etc/rp_environment sets no HF override, so the cache location is ours to pick.
#
# Supervised loop: the model crashing restarts it; /start.sh restores the tmux
# session across a pod reboot. Neither alone covers both failures.
mkdir -p /root/logs /root/.cache/vllm
exec >> /root/logs/vllm_serve.log 2>&1

SNAP=$(ls -d /root/.cache/huggingface/hub/models--mistralai--Mistral-7B-Instruct-v0.3/snapshots/*/ 2>/dev/null | head -1)
if [ -z "$SNAP" ]; then
  echo "FATAL: no local snapshot of the base model â€” refusing to fall back to a"
  echo "hub download, which is what filled the quota and killed the engine."
  exit 1
fi

export HF_HOME=/root/.cache/huggingface
export HF_HUB_OFFLINE=1
export VLLM_CACHE_ROOT=/root/.cache/vllm
export XDG_CACHE_HOME=/root/.cache
export OUTLINES_CACHE_DIR=/root/.cache/outlines

while true; do
  echo "=== vllm supervisor: starting $(date -u) snapshot=$SNAP ==="
  python -m vllm.entrypoints.openai.api_server \
    --model "$SNAP" \
    --tokenizer "$SNAP" \
    --served-model-name aria-llm-base \
    --enable-lora \
    --lora-modules aria-llm-v0.4-dpo=/root/adapters/aria_llm_v0_4_dpo \
    --max-lora-rank 32 \
    --max-model-len 32768 \
    --gpu-memory-utilization ${GPU_FRAC:-0.90} \
    --enable-auto-tool-choice \
    --tool-call-parser mistral \
    --port ${PORT:-8888} \
    --host 0.0.0.0
  echo "=== vllm supervisor: exited rc=$? at $(date -u); restart in 20s ==="
  sleep 20
done
