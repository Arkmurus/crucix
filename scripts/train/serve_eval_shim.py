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
import json as _json
import threading

import torch
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
from transformers import (
    AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig, TextIteratorStreamer,
)
from peft import PeftModel

BASE = os.environ.get("BASE_MODEL", "mistralai/Mistral-7B-Instruct-v0.3")
# R-F1362: ADAPTER is OPTIONAL. With no adapter the shim serves the BASE model
# directly (e.g. Qwen2.5-14B-Instruct as ARIA's sovereign brain); with one it
# serves base+LoRA (e.g. the Mistral v0.2 DPO adapter).
ADAPTER = os.environ.get("ADAPTER", "").strip()
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
_tok = AutoTokenizer.from_pretrained(ADAPTER or BASE, trust_remote_code=True)
if _tok.pad_token is None:
    _tok.pad_token = _tok.eos_token
_base = AutoModelForCausalLM.from_pretrained(
    BASE, torch_dtype=torch.bfloat16, device_map="auto",
    quantization_config=_bnb, trust_remote_code=True,
)
_model = PeftModel.from_pretrained(_base, ADAPTER) if ADAPTER else _base  # R-F1362
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


def parse_tool_calls(text):
    """R-F4372 (C-317) — recover tool calls a model wrote as TEXT.

    THE SHIM RETURNED ONLY `content` AND IGNORED `tools`. That was harmless for
    `eval_tooluse.py`, which scores the model's final PROSE answer, and fatal
    for any eval that measures whether she CALLS a tool: every response would
    score "answered in prose" for the base AND the trained adapter alike — a
    flat result that reads as "training did nothing" rather than as a broken
    instrument.

    Two wire formats, because the shim must not be tied to one base:

        ChatML / Qwen   <tool_call>{"name": ..., "arguments": {...}}</tool_call>
        Mistral         [TOOL_CALLS] [{"name": ..., "arguments": {...}}]

    Returns a list of OpenAI-shaped tool_calls, or [] — and [] genuinely means
    "she wrote prose", which is a real measurement, not a parse failure.
    """
    calls, body = [], (text or "").strip()

    def _emit(obj):
        if not isinstance(obj, dict):
            return
        name = obj.get("name")
        if not name:
            return
        args = obj.get("arguments", {})
        if not isinstance(args, str):
            args = _json.dumps(args, ensure_ascii=False)
        calls.append({
            "id": f"call{len(calls):05d}", "type": "function",
            "function": {"name": name, "arguments": args},
        })

    if "<tool_call>" in body:
        for chunk in body.split("<tool_call>")[1:]:
            frag = chunk.split("</tool_call>")[0].strip()
            try:
                _emit(_json.loads(frag))
            except Exception:
                continue
        return calls

    if "[TOOL_CALLS]" in body:
        frag = body.split("[TOOL_CALLS]", 1)[1].strip()
        # The array may be followed by prose; take the first balanced value.
        dec = _json.JSONDecoder()
        try:
            parsed, _ = dec.raw_decode(frag)
        except Exception:
            return calls
        for obj in (parsed if isinstance(parsed, list) else [parsed]):
            _emit(obj)
    return calls


def _blocking_generate(messages, max_tokens, temperature, tools=None):
    # `tools=` is passed ONLY when the caller sent them: a tokenizer whose
    # template does not accept the kwarg must keep working exactly as before,
    # and the DD eval sends no tools at all.
    try:
        prompt = _tok.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True,
            **({"tools": tools} if tools else {}),
        )
    except TypeError:
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


# R-F1361: token-by-token streaming via TextIteratorStreamer in a worker thread.
# aria-intel's /chat/stream (and the CLI) POST stream:true and expect OpenAI SSE
# ("data: {delta}\n\n" … "data: [DONE]"). Without it the provider's stream parser
# got a single JSON blob and fell back to DeepSeek ($0) → degraded. Streaming also
# keeps the connection alive token-by-token, dodging single-shot read timeouts.
def _start_stream(messages, max_tokens, temperature):
    prompt = _tok.apply_chat_template(
        messages, tokenize=False, add_generation_prompt=True,
    )
    inputs = _tok(prompt, return_tensors="pt", truncation=True,
                  max_length=8192).to(_model.device)
    streamer = TextIteratorStreamer(
        _tok, skip_prompt=True, skip_special_tokens=True,
    )
    kw = dict(
        **inputs, max_new_tokens=max_tokens, streamer=streamer,
        do_sample=temperature > 0, temperature=max(temperature, 0.01),
        pad_token_id=_tok.pad_token_id,
    )
    t = threading.Thread(target=lambda: _model.generate(**kw), daemon=True)
    t.start()
    return streamer, t


async def _sse(messages, max_tokens, temperature):
    async with _gpu_lock:  # serialise GPU across the whole stream
        streamer, t = await asyncio.to_thread(
            _start_stream, messages, max_tokens, temperature,
        )
        _SENTINEL = object()

        def _next():
            try:
                return next(streamer)
            except StopIteration:
                return _SENTINEL

        while True:
            piece = await asyncio.to_thread(_next)
            if piece is _SENTINEL:
                break
            if piece:
                chunk = {"id": "shim", "object": "chat.completion.chunk",
                         "model": MODEL_NAME,
                         "choices": [{"index": 0, "delta": {"content": piece},
                                      "finish_reason": None}]}
                yield f"data: {_json.dumps(chunk)}\n\n"
        done = {"id": "shim", "object": "chat.completion.chunk", "model": MODEL_NAME,
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}
        yield f"data: {_json.dumps(done)}\n\n"
        yield "data: [DONE]\n\n"
        await asyncio.to_thread(t.join)


@app.post("/v1/chat/completions")
async def chat(req: Request):
    body = await req.json()
    messages = body.get("messages", [])
    max_tokens = int(body.get("max_tokens", 512))
    temperature = float(body.get("temperature", 0.3))
    if body.get("stream"):
        return StreamingResponse(
            _sse(messages, max_tokens, temperature),
            media_type="text/event-stream",
        )
    tools = body.get("tools") or None
    async with _gpu_lock:  # serialise GPU; one generation at a time
        text = await asyncio.to_thread(
            _blocking_generate, messages, max_tokens, temperature, tools,
        )
    # R-F4372 — only when tools were OFFERED. Parsing unconditionally could turn
    # prose that merely quotes a call into an executed one, and would change the
    # payload the DD eval has always received.
    message = {"role": "assistant", "content": text}
    finish = "stop"
    if tools:
        calls = parse_tool_calls(text)
        if calls:
            message = {"role": "assistant", "content": None, "tool_calls": calls}
            finish = "tool_calls"
    return {
        "id": "shim", "object": "chat.completion", "model": MODEL_NAME,
        "choices": [{"index": 0, "message": message, "finish_reason": finish}],
    }


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="warning")
