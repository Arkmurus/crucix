"""serve_eval_shim — minimal OpenAI-compatible server for ARIA-LLM eval (R-F1340).

vLLM blew the pod's 30GB container disk (it re-installs torch/xformers). This
shim serves a base+LoRA checkpoint for the 500-Q eval using ONLY libraries
already on the pod (torch/transformers/peft/bitsandbytes), so there's no disk
explosion. Slower than vLLM but fine for a one-off eval.

Exposes the two routes eval_aria_llm.py needs:
  GET  /v1/models
  POST /v1/chat/completions   (reads messages, returns choices[0].message)

Env:
  BASE_MODEL   default mistralai/Mistral-7B-Instruct-v0.3
  ADAPTER      LoRA checkpoint dir (required)
  MODEL_NAME   served id (default aria-llm)
  PORT         default 8888
"""
import os

import torch
import uvicorn
from fastapi import FastAPI, Request
from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
from peft import PeftModel

BASE = os.environ.get("BASE_MODEL", "mistralai/Mistral-7B-Instruct-v0.3")
ADAPTER = os.environ["ADAPTER"]
MODEL_NAME = os.environ.get("MODEL_NAME", "aria-llm")
PORT = int(os.environ.get("PORT", "8888"))

# R-F1359: default to bf16, NOT 4-bit. 4-bit (bitsandbytes) inference is
# ~1.6 tok/s here — 125s for a 3-sentence answer — which times out every client
# (CLI 30s, aria-intel ~90s) → degraded mode. The 7B fits easily in bf16 on an
# 80GB A100 (~14GB) and runs ~10x faster, making it usable interactively. Set
# SHIM_4BIT=1 only on a small GPU that can't hold bf16.
_use_4bit = os.environ.get("SHIM_4BIT", "0") == "1"
_dtype_label = "4-bit" if _use_4bit else "bf16"
print(f"[shim] loading base={BASE} adapter={ADAPTER} ({_dtype_label})…", flush=True)
_bnb = BitsAndBytesConfig(
    load_in_4bit=True, bnb_4bit_compute_dtype=torch.bfloat16,
    bnb_4bit_quant_type="nf4", bnb_4bit_use_double_quant=True,
) if _use_4bit else None
_tok = AutoTokenizer.from_pretrained(ADAPTER, trust_remote_code=True)
if _tok.pad_token is None:
    _tok.pad_token = _tok.eos_token
_base = AutoModelForCausalLM.from_pretrained(
    BASE, torch_dtype=torch.bfloat16, device_map="auto",
    quantization_config=_bnb, trust_remote_code=True,
)
_model = PeftModel.from_pretrained(_base, ADAPTER)
_model.eval()
print("[shim] model ready", flush=True)

app = FastAPI()

# R-F1358: serialise GPU access + keep the event loop free. The previous version
# called _model.generate() (blocking, 30-120s) directly inside the async handler,
# which froze uvicorn's event loop — so even /v1/models health checks timed out,
# aria-intel marked aria_llm "down", and timed-out generations piled up as
# zombies. Now generation runs in a worker thread (event loop stays responsive)
# and a lock serialises GPU use so concurrent requests queue cleanly.
import asyncio
_gpu_lock = asyncio.Lock()


def _blocking_generate(messages, max_tokens, temperature):
    prompt = _tok.apply_chat_template(
        messages, tokenize=False, add_generation_prompt=True,
    )
    inputs = _tok(prompt, return_tensors="pt", truncation=True,
                  max_length=8192).to(_model.device)
    with torch.no_grad():
        out = _model.generate(
            **inputs, max_new_tokens=max_tokens,
            do_sample=temperature > 0, temperature=max(temperature, 0.01),
            pad_token_id=_tok.pad_token_id,
        )
    return _tok.decode(
        out[0][inputs["input_ids"].shape[1]:], skip_special_tokens=True,
    )


@app.get("/v1/models")
def models():
    return {"object": "list", "data": [{"id": MODEL_NAME, "object": "model"}]}


@app.post("/v1/chat/completions")
async def chat(req: Request):
    body = await req.json()
    messages = body.get("messages", [])
    max_tokens = int(body.get("max_tokens", 512))
    temperature = float(body.get("temperature", 0.3))
    async with _gpu_lock:  # serialise GPU; one generation at a time
        text = await asyncio.to_thread(
            _blocking_generate, messages, max_tokens, temperature,
        )
    return {
        "id": "shim", "object": "chat.completion", "model": MODEL_NAME,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": text},
                     "finish_reason": "stop"}],
    }


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="warning")
