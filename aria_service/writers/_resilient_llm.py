"""Resilient writer LLM client — Claude primary, DeepSeek fallback.

Writers produce structured documents (STANAG assessments, ITT / BAFO
papers, UKBA s.7 compliance opinions, Portuguese legal-register text,
tech specs). Each writer was originally wired direct-to-Anthropic
(no fallback) because Claude's structured-output fidelity is
measurably better on heavy-schema JSON + legal register.

That design chose "fail cleanly when Claude is unavailable" over
"silently degrade". In practice (2026-04-17 billing cooldown) this
means the operator loses the ability to produce documents entirely
during outages, which is worse than having a DeepSeek-quality draft
with a loud "re-run when Claude is back" caveat.

This helper wraps both clients in a sync interface:
  1. Try Anthropic (caller's client)
  2. On ANY failure — auth/billing/network/timeout/rate_limit —
     fall back to DeepSeek via the OpenAI-compatible /v1/chat/completions
  3. Return a ResilientResponse carrying the text PLUS degradation flag

The `degraded` flag is surfaced in the WriterResult and as a response
header on the endpoint, so the operator sees clearly that this output
is a fallback draft and should be regenerated once Claude is back.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger("aria.writers.resilient_llm")


DEEPSEEK_URL = "https://api.deepseek.com/v1/chat/completions"
DEFAULT_DEEPSEEK_MODEL = os.getenv("ARIA_WRITER_FALLBACK_MODEL", "deepseek-chat")
DEFAULT_TIMEOUT = 90.0  # writers produce longer outputs than chat turns


@dataclass
class ResilientResponse:
    text:        str       # raw content (not yet JSON-parsed)
    model_used:  str       # "claude-opus-4-7" / "deepseek-chat" / etc.
    degraded:    bool      # True when fallback was used
    reason:      str = ""  # human-readable reason the primary failed
    primary_error: str = "" # truncated primary exception for audit


def _call_anthropic(
    client: Any,
    model: str,
    system_prompt: str,
    user_prompt: str,
    max_tokens: int,
) -> tuple[str, str]:
    """Invoke Anthropic's messages API via the writer's existing client.
    Returns (text, model_used). Raises on any failure — caller handles
    the fallback decision."""
    response = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=[{
            "type": "text",
            "text": system_prompt,
            "cache_control": {"type": "ephemeral"},
        }],
        messages=[{"role": "user", "content": user_prompt}],
    )
    text = response.content[0].text.strip()
    return text, model


def _call_deepseek(
    system_prompt: str,
    user_prompt: str,
    max_tokens: int,
    timeout: float,
) -> tuple[str, str]:
    """Invoke DeepSeek's OpenAI-compatible endpoint synchronously.
    Returns (text, model_used). Raises on failure."""
    api_key = os.getenv("DEEPSEEK_API_KEY", "")
    if not api_key:
        raise RuntimeError(
            "DEEPSEEK_API_KEY is not set — no fallback LLM available for "
            "writer degradation. Configure DEEPSEEK_API_KEY on fly.io to "
            "enable writer fallback during Anthropic outages."
        )

    with httpx.Client(timeout=timeout) as http:
        resp = http.post(
            DEEPSEEK_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": DEFAULT_DEEPSEEK_MODEL,
                "max_tokens": max_tokens,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            },
        )
        resp.raise_for_status()
        data = resp.json()

    choices = data.get("choices") or []
    if not choices:
        raise RuntimeError(f"DeepSeek returned no choices: {data}")
    text = (choices[0].get("message") or {}).get("content") or ""
    return text.strip(), DEFAULT_DEEPSEEK_MODEL


def resilient_complete(
    anthropic_client: Any,
    anthropic_model: str,
    system_prompt: str,
    user_prompt: str,
    max_tokens: int = 2000,
    timeout: float = DEFAULT_TIMEOUT,
    allow_fallback: bool = True,
) -> ResilientResponse:
    """Primary-with-fallback call for writer modules.

    When allow_fallback=False, behaves exactly like the old direct-to-
    Claude path (raises on failure). Default allow_fallback=True
    keeps producing drafts during Anthropic outages, at the cost of
    fidelity — caller is responsible for surfacing the `degraded`
    flag to the operator.
    """
    primary_err: Exception | None = None
    try:
        text, model_used = _call_anthropic(
            anthropic_client,
            anthropic_model,
            system_prompt,
            user_prompt,
            max_tokens,
        )
        return ResilientResponse(
            text=text,
            model_used=model_used,
            degraded=False,
        )
    except Exception as exc:
        primary_err = exc
        logger.warning(
            "Writer primary (Claude / %s) failed: %s",
            anthropic_model, str(exc)[:200],
        )

    if not allow_fallback:
        raise primary_err  # type: ignore[misc]

    # Fallback path
    try:
        text, model_used = _call_deepseek(
            system_prompt, user_prompt, max_tokens, timeout,
        )
        reason = f"Anthropic unavailable ({str(primary_err)[:160]})"
        logger.warning(
            "Writer fell back to DeepSeek (%s) — flagging result as degraded",
            model_used,
        )
        return ResilientResponse(
            text=text,
            model_used=model_used,
            degraded=True,
            reason=reason,
            primary_error=str(primary_err)[:240],
        )
    except Exception as fb_exc:
        logger.error(
            "Writer fallback (DeepSeek) ALSO failed: %s", str(fb_exc)[:200]
        )
        raise RuntimeError(
            f"Both writer LLM providers failed. "
            f"Claude: {str(primary_err)[:160]}. "
            f"DeepSeek: {str(fb_exc)[:160]}"
        )
