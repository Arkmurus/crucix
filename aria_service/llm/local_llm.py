"""local_llm — provider for self-hosted LLMs (Ollama / vLLM /
OpenAI-compatible) (R-F87, 2026-05-09).

Why this module exists
──────────────────────
Phase 1 of the independence roadmap. Replaces the high-volume low-
stakes Anthropic / DeepSeek calls with a self-hosted open-weight model
served via Ollama or vLLM. Both expose an OpenAI-compatible /v1/chat/
completions endpoint, so this module works against either.

Env-gated on:
  OLLAMA_URL — base URL of the local LLM service (e.g. http://ollama:11434
               for sidecar, or http://10.0.0.5:8000 for separate Fly machine)
  OLLAMA_MODEL — model name to call (e.g. llama3.1:8b-instruct,
                  qwen2.5:7b-instruct, llama3.3:70b-instruct)

When unset, this provider reports `is_configured()=False` and the
existing fallback chain (anthropic → deepseek → groq) keeps working
unchanged. Behaviour-neutral until operator deploys an Ollama instance
and sets the env vars.

Public API
──────────
    is_configured() -> bool
    complete(prompt: str, *, system: str = '', max_tokens: int = 1024,
              temperature: float = 0.7, **kw) -> dict
        Returns the standard ARIA LLM response shape:
        { "ok", "text", "model", "tokens_in", "tokens_out", "latency_ms",
          "provider": "local_llm" }

    stream(prompt, system, max_tokens, temperature, **kw) -> AsyncIterator[str]
        Streaming variant for /chat/stream paths.

    summary() -> dict
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any, AsyncIterator

logger = logging.getLogger("aria.llm.local_llm")

_DEFAULT_MODEL = "llama3.1:8b-instruct"
_DEFAULT_TIMEOUT = 60.0  # local LLM should be fast (<10s typically)


def is_configured() -> bool:
    """OLLAMA_URL set in env."""
    return bool((os.getenv("OLLAMA_URL") or "").strip())


def _base_url() -> str | None:
    raw = (os.getenv("OLLAMA_URL") or "").strip()
    if not raw:
        return None
    return raw.rstrip("/")


def _model_name() -> str:
    return (os.getenv("OLLAMA_MODEL") or _DEFAULT_MODEL).strip()


async def complete(
    prompt: str,
    *,
    system: str = "",
    max_tokens: int = 1024,
    temperature: float = 0.7,
    **_kw: Any,
) -> dict[str, Any]:
    """Run a single completion against the local LLM."""
    base = _base_url()
    if not base:
        return {
            "ok":       False,
            "provider": "local_llm",
            "error":    "OLLAMA_URL not set — operator action pending",
            "text":     "",
            "model":    _model_name(),
        }
    try:
        import httpx
    except ImportError as e:
        return {"ok": False, "provider": "local_llm", "error": f"httpx unavailable: {e}", "text": ""}

    messages: list[dict[str, str]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    body = {
        "model":       _model_name(),
        "messages":    messages,
        "max_tokens":  max_tokens,
        "temperature": temperature,
        "stream":      False,
    }

    t_start = time.time()
    try:
        async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT) as client:
            resp = await client.post(f"{base}/v1/chat/completions", json=body)
    except Exception as e:
        return {
            "ok":         False,
            "provider":   "local_llm",
            "error":      f"{type(e).__name__}: {e}",
            "text":       "",
            "model":      _model_name(),
            "latency_ms": int((time.time() - t_start) * 1000),
        }

    if resp.status_code != 200:
        return {
            "ok":         False,
            "provider":   "local_llm",
            "error":      f"http_{resp.status_code}: {resp.text[:200]}",
            "text":       "",
            "model":      _model_name(),
            "latency_ms": int((time.time() - t_start) * 1000),
        }

    try:
        data = resp.json()
    except Exception as e:
        return {
            "ok":         False,
            "provider":   "local_llm",
            "error":      f"json_decode: {e}",
            "text":       "",
            "model":      _model_name(),
            "latency_ms": int((time.time() - t_start) * 1000),
        }

    choice = (data.get("choices") or [{}])[0]
    text = (choice.get("message") or {}).get("content", "")
    usage = data.get("usage") or {}

    return {
        "ok":         True,
        "provider":   "local_llm",
        "text":       text,
        "model":      data.get("model", _model_name()),
        "tokens_in":  usage.get("prompt_tokens", 0),
        "tokens_out": usage.get("completion_tokens", 0),
        "latency_ms": int((time.time() - t_start) * 1000),
        "finish_reason": choice.get("finish_reason"),
    }


async def stream(
    prompt: str,
    *,
    system: str = "",
    max_tokens: int = 1024,
    temperature: float = 0.7,
    **_kw: Any,
) -> AsyncIterator[str]:
    """Streaming generator yielding text deltas. Used by /chat/stream."""
    base = _base_url()
    if not base:
        yield ""
        return
    try:
        import httpx
    except ImportError:
        yield ""
        return

    messages: list[dict[str, str]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    body = {
        "model":       _model_name(),
        "messages":    messages,
        "max_tokens":  max_tokens,
        "temperature": temperature,
        "stream":      True,
    }

    try:
        async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT) as client:
            async with client.stream(
                "POST", f"{base}/v1/chat/completions", json=body,
            ) as resp:
                async for line in resp.aiter_lines():
                    if not line or not line.startswith("data: "):
                        continue
                    payload = line[6:].strip()
                    if payload == "[DONE]":
                        break
                    try:
                        import json as _json
                        chunk = _json.loads(payload)
                        delta = (chunk.get("choices") or [{}])[0].get("delta", {})
                        text = delta.get("content")
                        if text:
                            yield text
                    except Exception:
                        continue
    except Exception as e:
        logger.warning("local_llm stream error: %s", e)


def summary() -> dict[str, Any]:
    return {
        "module":     "local_llm",
        "configured": is_configured(),
        "url":        _base_url(),
        "model":      _model_name(),
        "purpose":    "Phase 1 — self-hosted LLM substitution (Ollama / vLLM / OpenAI-compatible)",
    }
