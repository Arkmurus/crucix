"""
MeteredProvider — transparent cost-tracking wrapper around any LLMProvider.

Sits in front of the real provider so EVERY llm.complete() call is metered
without each feature having to remember to instrument itself. Token counts
come straight from the LLMResult the inner provider already produces;
USD cost is computed from cost_tracker's pricing table; the active feature
is read from the contextvar set by the calling code.

Why a wrapper instead of editing each provider class
════════════════════════════════════════════════════
Two reasons:
  1. New providers don't have to remember to add metering — wrap once
     in the factory and you're done.
  2. The token-extraction logic lives in one place, not five.

Failure modes
═════════════
The recording side is fully wrapped — if Redis is down, the LLM call
still completes and returns to the caller, and we just lose that one
metric. NEVER let cost tracking be the reason a chat reply fails.
"""
from __future__ import annotations

import logging
import time

from .provider import LLMProvider, LLMResult

logger = logging.getLogger("aria.llm.metered")


class MeteredProvider(LLMProvider):
    """Decorator that delegates to an inner provider and records cost
    after each call. The inner provider's identity (name, is_configured)
    is exposed transparently so callers can't tell the difference."""

    def __init__(self, inner: LLMProvider):
        self._inner = inner

    @property
    def name(self) -> str:  # type: ignore[override]
        return getattr(self._inner, "name", "metered")

    @property
    def is_configured(self) -> bool:
        return self._inner.is_configured

    def __getattr__(self, item):
        # Forward any attribute the inner provider has but we don't —
        # keeps things like model selection / extra config working
        # without us listing every field.
        return getattr(self._inner, item)

    def _record_cost(self, started: float, result, success: bool, error: str) -> None:
        """Fire-and-forget cost recording. Never blocks the caller."""
        latency_ms = int((time.time() - started) * 1000)
        try:
            from ..intel import cost_tracker
            model = ""
            in_tk = 0
            out_tk = 0
            if result is not None:
                model = getattr(result, "model", "") or getattr(self._inner, "model", "")
                in_tk = int(getattr(result, "input_tokens", 0) or 0)
                out_tk = int(getattr(result, "output_tokens", 0) or 0)
            else:
                model = getattr(self._inner, "model", "") or getattr(self._inner, "name", "")
            import asyncio as _aio
            _aio.create_task(cost_tracker.record_call(
                model=model or "",
                input_tokens=in_tk,
                output_tokens=out_tk,
                latency_ms=latency_ms,
                provider_name=getattr(self._inner, "name", ""),
                success=success,
                error=error,
            ))
        except Exception as e:
            logger.debug("MeteredProvider record dispatch failed: %s", e)

    async def complete(
        self,
        system_prompt: str,
        user_message: str,
        *,
        max_tokens: int = 4096,
        timeout: float = 60.0,
    ) -> LLMResult:
        started = time.time()
        success = True
        error = ""
        result: LLMResult | None = None
        try:
            result = await self._inner.complete(
                system_prompt,
                user_message,
                max_tokens=max_tokens,
                timeout=timeout,
            )
            return result
        except Exception as e:
            success = False
            error = str(e)
            raise
        finally:
            self._record_cost(started, result, success, error)

    async def stream(
        self,
        system_prompt: str,
        user_message: str,
        *,
        max_tokens: int = 4096,
        timeout: float = 120.0,
        on_done=None,
    ):
        """Metered streaming — yields chunks, records cost after stream ends."""
        started = time.time()
        final_result = None

        def _capture_done(result):
            nonlocal final_result
            final_result = result
            if on_done:
                on_done(result)

        try:
            async for chunk in self._inner.stream(
                system_prompt, user_message,
                max_tokens=max_tokens, timeout=timeout,
                on_done=_capture_done,
            ):
                yield chunk
            self._record_cost(started, final_result, True, "")
        except Exception as e:
            self._record_cost(started, final_result, False, str(e))
            raise
