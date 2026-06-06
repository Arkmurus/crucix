"""aria_llm_provider — provider adapter for ARIA-LLM served via vLLM
or Ollama (R-F93, 2026-05-09).

Why this module exists
──────────────────────
Phase 4 of the independence roadmap. Once ARIA-LLM v0.1 is fine-tuned
and deployed via vLLM on a rented GPU, the live ARIA service connects
to it via this adapter. The adapter speaks the same OpenAI-compatible
API as the existing chain — swap-in via env vars only, no code change
needed in callers.

Env vars:
  ARIA_LLM_URL    — base URL of the ARIA-LLM vLLM endpoint (e.g.
                     https://aria-llm.runpod.io/v1)
  ARIA_LLM_MODEL  — model name as registered in vLLM
                     (default: aria-llm-v0.1)
  ARIA_LLM_KEY    — optional bearer token for the vLLM endpoint

When ARIA_LLM_URL is set, this provider takes priority position in the
fallback chain (above Anthropic). When unset, the chain falls back to
the existing providers.

The router (R-F87a) already has a tier for ARIA-LLM — once weights
deploy and ARIA_LLM_URL is set, the tier_router automatically routes
chat / DD / audit-grade calls to ARIA-LLM instead of Anthropic.

Public API (matches the existing OpenAI-compat provider shape):
    is_configured() -> bool
    complete(prompt, system, max_tokens, temperature) -> dict
    stream(prompt, system, max_tokens, temperature) -> AsyncIterator[str]
    summary() -> dict
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any, AsyncIterator

logger = logging.getLogger("aria.llm.aria_llm")

# R-F1224: brain wiring for observability
try:
    from ..intel.engine_wiring import wire_success, wire_failure
except ImportError:
    wire_success = lambda **kw: None
    wire_failure = lambda **kw: None

_DEFAULT_TIMEOUT = 120.0   # vLLM 70B can take 5-15s for a long completion


def is_configured() -> bool:
    return bool((os.getenv("ARIA_LLM_URL") or "").strip())


def _base_url() -> str | None:
    raw = (os.getenv("ARIA_LLM_URL") or "").strip()
    if not raw:
        return None
    return raw.rstrip("/")


def _model_name() -> str:
    return (os.getenv("ARIA_LLM_MODEL") or "aria-llm-v0.1").strip()


def _api_key() -> str | None:
    return (os.getenv("ARIA_LLM_KEY") or "").strip() or None


def _max_model_len() -> int:
    """Context window of the served ARIA-LLM, in tokens. Must match the vLLM
    `--max-model-len`. R-F1363: used to clamp max_tokens so prompt+completion
    never overflows (vLLM returns HTTP 400 otherwise). Default 32768 = the
    Qwen2.5-14B window we serve; override via ARIA_LLM_MAX_MODEL_LEN."""
    try:
        v = int((os.getenv("ARIA_LLM_MAX_MODEL_LEN") or "32768").strip())
        return v if v >= 512 else 32768
    except (TypeError, ValueError):
        return 32768


async def complete(
    prompt: str,
    *,
    system: str = "",
    max_tokens: int = 2048,
    temperature: float = 0.3,
    **_kw: Any,
) -> dict[str, Any]:
    """Single completion against the ARIA-LLM vLLM endpoint."""
    base = _base_url()
    if not base:
        return {
            "ok":       False,
            "provider": "aria_llm",
            "error":    "ARIA_LLM_URL not set — Phase 4 deploy pending",
            "text":     "",
            "model":    _model_name(),
        }
    try:
        import httpx
    except ImportError as e:
        return {"ok": False, "provider": "aria_llm",
                "error": f"httpx unavailable: {e}", "text": ""}

    headers = {"Content-Type": "application/json"}
    key = _api_key()
    if key:
        headers["Authorization"] = f"Bearer {key}"

    messages: list[dict[str, str]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    # R-F1363 — clamp max_tokens so (prompt + completion) never exceeds the
    # served model's context window. The coder requests max_tokens up to 8192;
    # with an 8192-token vLLM window ANY prompt overflowed → "maximum context
    # length is 8192 … you requested 8231" HTTP 400, which soft-cooled the
    # provider and failed every self-coding fix at the plan step. We reserve the
    # estimated prompt tokens (+ a margin) out of the window so a completion
    # always fits. ARIA_LLM_MAX_MODEL_LEN must match the vLLM --max-model-len.
    max_model_len = _max_model_len()
    prompt_chars = len(system) + len(prompt)
    est_prompt_tokens = prompt_chars // 4 + 32   # ~4 chars/token + framing
    safe_max_tokens = max_model_len - est_prompt_tokens - 256  # 256 = margin
    if safe_max_tokens < max_tokens:
        if safe_max_tokens < 256:
            logger.warning(
                "[aria_llm] prompt (~%d tok) leaves only %d tok in the %d window; "
                "clamping completion to 256",
                est_prompt_tokens, safe_max_tokens, max_model_len,
            )
            safe_max_tokens = 256
        else:
            logger.info(
                "[aria_llm] clamped max_tokens %d→%d to fit %d-token window "
                "(prompt ~%d tok)",
                max_tokens, safe_max_tokens, max_model_len, est_prompt_tokens,
            )
        max_tokens = safe_max_tokens

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
            resp = await client.post(
                f"{base}/chat/completions", headers=headers, json=body,
            )
    except Exception as e:
        wire_failure(
            module="aria_llm",
            detail=f"ARIA-LLM connection failed: {type(e).__name__}: {e}",
            gap_type="llm_unreachable",
            source="aria_llm_provider:complete",
        )
        return {
            "ok":         False,
            "provider":   "aria_llm",
            "error":      f"{type(e).__name__}: {e}",
            "text":       "",
            "model":      _model_name(),
            "latency_ms": int((time.time() - t_start) * 1000),
        }

    if resp.status_code != 200:
        wire_failure(
            module="aria_llm",
            detail=f"ARIA-LLM HTTP {resp.status_code}: {resp.text[:200]}",
            gap_type="llm_error",
            source="aria_llm_provider:complete",
        )
        return {
            "ok":         False,
            "provider":   "aria_llm",
            "error":      f"http_{resp.status_code}: {resp.text[:200]}",
            "text":       "",
            "model":      _model_name(),
            "latency_ms": int((time.time() - t_start) * 1000),
        }

    try:
        data = resp.json()
    except Exception as e:
        wire_failure(
            module="aria_llm",
            detail=f"ARIA-LLM JSON decode failed: {e}",
            gap_type="llm_error",
            source="aria_llm_provider:complete",
        )
        return {
            "ok":         False,
            "provider":   "aria_llm",
            "error":      f"json_decode: {e}",
            "text":       "",
            "model":      _model_name(),
            "latency_ms": int((time.time() - t_start) * 1000),
        }

    choice = (data.get("choices") or [{}])[0]
    text = (choice.get("message") or {}).get("content", "")
    usage = data.get("usage") or {}
    latency_ms = int((time.time() - t_start) * 1000)

    wire_success(
        module="aria_llm",
        summary=f"ARIA-LLM completion: {usage.get('prompt_tokens', 0)} in, "
                f"{usage.get('completion_tokens', 0)} out, {latency_ms}ms",
        source_id="aria_llm_provider:complete",
    )
    return {
        "ok":          True,
        "provider":    "aria_llm",
        "text":        text,
        "model":       data.get("model", _model_name()),
        "tokens_in":   usage.get("prompt_tokens", 0),
        "tokens_out":  usage.get("completion_tokens", 0),
        "latency_ms":  latency_ms,
        "finish_reason": choice.get("finish_reason"),
    }


async def stream(
    prompt: str,
    *,
    system: str = "",
    max_tokens: int = 2048,
    temperature: float = 0.3,
    **_kw: Any,
) -> AsyncIterator[str]:
    """Streaming generator. Used for /chat/stream end-to-end."""
    base = _base_url()
    if not base:
        yield ""
        return
    try:
        import httpx
    except ImportError:
        yield ""
        return

    headers = {"Content-Type": "application/json"}
    key = _api_key()
    if key:
        headers["Authorization"] = f"Bearer {key}"

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

    t_start = time.time()
    tokens_out = 0
    try:
        async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT) as client:
            async with client.stream(
                "POST", f"{base}/chat/completions",
                headers=headers, json=body,
            ) as resp:
                if resp.status_code != 200:
                    wire_failure(
                        module="aria_llm",
                        detail=f"ARIA-LLM stream HTTP {resp.status_code}",
                        gap_type="llm_error",
                        source="aria_llm_provider:stream",
                    )
                    yield ""
                    return
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
                            tokens_out += 1
                            yield text
                    except Exception:
                        continue
        latency_ms = int((time.time() - t_start) * 1000)
        wire_success(
            module="aria_llm",
            summary=f"ARIA-LLM stream: {tokens_out} tokens, {latency_ms}ms",
            source_id="aria_llm_provider:stream",
        )
    except Exception as e:
        logger.warning("aria_llm stream error: %s", e)
        wire_failure(
            module="aria_llm",
            detail=f"ARIA-LLM stream failed: {e}",
            gap_type="llm_unreachable",
            source="aria_llm_provider:stream",
        )


def summary() -> dict[str, Any]:
    return {
        "module":     "aria_llm_provider",
        "configured": is_configured(),
        "url":        _base_url(),
        "model":      _model_name(),
        "purpose":    "Phase 4 — sovereign ARIA-LLM serving via vLLM",
    }
