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
from . import aria_llm_url as _aria_llm_url  # R-F2645: the one URL join
from ..intel.wire import fail_wire  # R-F1789 §21 brain-wiring

logger = logging.getLogger("aria.llm.aria_llm")

# R-F1224: brain wiring for observability
try:
    from ..intel.engine_wiring import wire_success, wire_failure
except ImportError:
    wire_success = lambda **kw: None
    wire_failure = lambda **kw: None

_DEFAULT_TIMEOUT = 120.0   # vLLM 70B can take 5-15s for a long completion


def _effective_timeout(timeout: float | None) -> float:
    """R-F3614 (2026-08-01) — honour the caller's per-call budget.

    THE DEFECT. `complete()` and `stream()` ended in `**_kw`, with no `timeout`
    parameter. model_router._sovereign_complete has always passed
    `timeout=_sovereign_timeout(timeout)` — and it landed in `_kw` and was
    DISCARDED, so every sovereign call ran on the 120s client default.

    R-F1365's stated purpose was "cap the per-provider budget at
    ARIA_LLM_TIMEOUT (default 40s) so a slow/stuck 14B fails over to the funded
    DeepSeek fallback fast instead of burning 120s". It never did that. Worse,
    because the cap was applied at the CALLER (aria_engine) instead, the only
    provider it ever actually bound was DeepSeek — the opposite of the intent
    (R-F3606 removed that half).

    So a stuck sovereign burned the full 120s before failover, silently, for as
    long as the two-track router has existed. This closes the loop: the value
    the router already computes is now the value the socket uses.

    Guards a nonsense timeout rather than trusting the caller: <=0 or a
    non-number falls back to the default instead of creating a client that
    times out instantly (or never).
    """
    if timeout is None:
        return _DEFAULT_TIMEOUT
    try:
        t = float(timeout)
    except (TypeError, ValueError):
        return _DEFAULT_TIMEOUT
    return t if t > 0 else _DEFAULT_TIMEOUT


@fail_wire(module="aria_llm_provider", gap_type="engine_failure")
def is_configured() -> bool:
    return bool((os.getenv("ARIA_LLM_URL") or "").strip())


def _base_url() -> str | None:
    """The configured ARIA-LLM base, or None when unset.

    R-F2645: normalisation delegates to aria_llm_url so this module, the health
    probe (resilience.py) and self_healing all derive URLs from one definition.
    """
    raw = (os.getenv("ARIA_LLM_URL") or "").strip()
    if not raw:
        return None
    return _aria_llm_url.normalise_base(raw)


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


def window_overflow(system: str, prompt: str) -> str:
    """Return a reason string if the prompt CANNOT fit the served window, else "".

    R-F4317 (C-265). R-F1363 clamps `max_tokens` so prompt+completion fit, and
    its arithmetic is right for a large-but-fitting prompt. It was wrong for the
    case that matters once every turn routes here: when the PROMPT ALONE exceeds
    the window, `safe_max_tokens` goes NEGATIVE, the old code floored it at 256
    and POSTED anyway, and vLLM answered HTTP 400. R-F1363's own comment records
    the cost - it "soft-cooled the provider and failed every self-coding fix at
    the plan step".

    Measured demand is a mean 5,671 tokens/call against a 16,384 window, with
    research_extraction at ~9,861 calls/month, so this fires in production the
    moment the sovereign takes general traffic.

    ELIGIBILITY, NOT TRUNCATION. Dropping the tail of a research extraction or a
    DD prompt returns a confident answer computed from part of the evidence, and
    nothing downstream can tell it happened. Failing over to a larger-window
    provider is honest; truncating is not (readiness note item 4: "fail CLOSED
    ... never truncate to fit").

    Reserves the same 256-token answer margin the clamp uses: a "fit" with no
    room to answer is not a fit.
    """
    max_model_len = _max_model_len()
    est_prompt_tokens = (len(system) + len(prompt)) // 4 + 32
    if est_prompt_tokens + 256 >= max_model_len:
        return (
            f"prompt does not fit the sovereign context window: "
            f"~{est_prompt_tokens} prompt tokens + 256 answer margin exceeds "
            f"{max_model_len}. Not truncating; a larger-window provider must "
            f"take this call."
        )
    return ""


# R-F3606 (2026-08-01) — R-F1360's latency ceiling, moved to the boundary that
# OWNS it.
#
# The 7B/14B sovereign serves at ~10 tok/s on the bf16 shim, so a 4000-token
# completion takes 150-250s and blows the chat timeout. R-F1360 enforced that by
# making the CALLER (aria_engine._completion_max_tokens) return 800 whenever a
# sovereign URL was merely CONFIGURED — which capped DeepSeek instead, because
# DeepSeek is what actually serves under SHADOW/TWO-TRACK. That produced a total
# chat outage on 2026-08-01 (see R-F3606 in aria_engine.py).
#
# SCOPE — CHAT ONLY. APPLIED BY model_router, NOT HERE. (Do not "tidy" this by
# calling it inside complete()/stream(); that was tried and it is wrong.)
#
# R-F1360's constraint is a CHAT constraint — its own words are "past the 120s
# chat timeout". The self-coder also calls this module, and it legitimately asks
# for max_tokens=8192 to generate a whole file (see R-F1363 and its test). An
# unconditional 800-token ceiling here TRUNCATES generated code — precisely the
# truncating-fix class that R-F904 exists to block and that CLAUDE.md §21c warns
# must be fixed before self-deploy is safe.
#
# So the ceiling is applied at the chat call sites in model_router
# (_sovereign_complete + the stream_synthesis sovereign branch), which are the
# §13 chat pair. Batch/coder callers reaching this module directly are untouched.
SOVEREIGN_COMPLETION_CEILING = 800


def clamp_for_sovereign(max_tokens: int) -> int:
    """Cap a CHAT completion budget to what the sovereign can generate inside
    the chat timeout. Only ever lowers — a caller asking for less than the
    ceiling is honoured as-is.

    Call this from chat paths only. Batch callers (self-coder, training export)
    need large budgets and must not be clamped."""
    try:
        n = int(max_tokens)
    except (TypeError, ValueError):
        return SOVEREIGN_COMPLETION_CEILING
    if n > SOVEREIGN_COMPLETION_CEILING:
        logger.info(
            "[R-F3606] clamped sovereign completion %d→%d tokens (~10 tok/s "
            "would exceed the chat timeout)", n, SOVEREIGN_COMPLETION_CEILING,
        )
        return SOVEREIGN_COMPLETION_CEILING
    return n


@fail_wire(module="aria_llm_provider", gap_type="engine_failure")
async def complete(
    prompt: str,
    *,
    system: str = "",
    max_tokens: int = 2048,
    temperature: float = 0.3,
    timeout: float | None = None,   # R-F3614 — was swallowed by **_kw
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
    # R-F4317 (C-265) — ELIGIBILITY FIRST. If the prompt cannot fit, the
    # sovereign is not a candidate for this call: return the ordinary not-ok
    # result so the caller fails over, exactly as it does for an unreachable
    # endpoint. Sending it anyway produces an HTTP 400 that cools the provider
    # and takes the whole sovereign path down with it.
    _overflow = window_overflow(system, prompt)
    if _overflow:
        logger.info("[aria_llm] not eligible: %s", _overflow)
        return {
            "ok":       False,
            "provider": "aria_llm",
            "error":    _overflow,
            "text":     "",
            "model":    _model_name(),
        }

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
        async with httpx.AsyncClient(timeout=_effective_timeout(timeout)) as client:
            resp = await client.post(
                _aria_llm_url.chat_completions_url(base), headers=headers, json=body,
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
    timeout: float | None = None,   # R-F3614 — was swallowed by **_kw
    **_kw: Any,
) -> AsyncIterator[str]:
    """Streaming generator. Used for /chat/stream end-to-end."""
    # R-F4317 (C-265) — the streaming path shares the window and must
    # share the rule. CLAUDE.md §13: a guard added to one path and not
    # the other is how the two forks drift. Yielding nothing lets the
    # caller fall through to a larger-window provider.
    _overflow = window_overflow(system, prompt)
    if _overflow:
        logger.info("[aria_llm] stream not eligible: %s", _overflow)
        return
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
        async with httpx.AsyncClient(timeout=_effective_timeout(timeout)) as client:
            async with client.stream(
                "POST", _aria_llm_url.chat_completions_url(base),
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


@fail_wire(module="aria_llm_provider", gap_type="engine_failure")
def summary() -> dict[str, Any]:
    return {
        "module":     "aria_llm_provider",
        "configured": is_configured(),
        "url":        _base_url(),
        "model":      _model_name(),
        "purpose":    "Phase 4 — sovereign ARIA-LLM serving via vLLM",
    }
