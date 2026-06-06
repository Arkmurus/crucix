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
        """Fire-and-forget cost recording. Never blocks the caller.

        R-F104 (2026-05-09): instrumented to surface silent failures.
        Previously the create_task'd record_call could raise inside the
        coroutine and disappear without trace — leaving the operator
        dashboard showing 'NO DATA' while LLM calls actually were
        producing cost. Now wraps the task with a done-callback that
        logs at WARNING when the task raised, so fly logs show:
            cost_tracker record_call failed: ...
        """
        latency_ms = int((time.time() - started) * 1000)
        try:
            from ..intel import cost_tracker
            model = ""
            in_tk = 0
            out_tk = 0
            routed_via = ""
            if result is not None:
                model = getattr(result, "model", "") or getattr(self._inner, "model", "")
                in_tk = int(getattr(result, "input_tokens", 0) or 0)
                out_tk = int(getattr(result, "output_tokens", 0) or 0)
                routed_via = getattr(result, "routed_via", "") or ""
            else:
                model = getattr(self._inner, "model", "") or getattr(self._inner, "name", "")
            # R-F131 (2026-05-10): the dashboard panel was showing
            # 100% of spend attributed to "fallback" because the
            # MeteredProvider wraps a FallbackProvider whose name
            # is the literal string "fallback". The underlying
            # provider that actually served the call is exposed via
            # result.routed_via = "fallback:<name>". Extract the real
            # provider so cost-by-provider reflects anthropic vs
            # deepseek vs groq vs aria_llm spend distribution.
            inner_name = getattr(self._inner, "name", "")
            if routed_via.startswith("fallback:"):
                provider_for_metric = routed_via.split(":", 1)[1].strip() or inner_name
            else:
                provider_for_metric = inner_name
            import asyncio as _aio
            task = _aio.create_task(cost_tracker.record_call(
                model=model or "",
                input_tokens=in_tk,
                output_tokens=out_tk,
                latency_ms=latency_ms,
                provider_name=provider_for_metric,
                success=success,
                error=error,
            ))
            # R-F104: surface silent failures
            def _on_done(t):
                # R-F1029: a cancelled cost-record task raises CancelledError
                # (a BaseException, NOT caught by `except Exception`) from
                # t.exception(), which made this very callback error loudly during
                # restart churn. Guard it so cancellation is silent.
                if t.cancelled():
                    return
                try:
                    exc = t.exception()
                except BaseException:  # noqa: BLE001 — never let the callback raise
                    return
                if exc is not None:
                    logger.warning("cost_tracker.record_call task failed: %s: %s",
                                   type(exc).__name__, exc)
            task.add_done_callback(_on_done)
        except Exception as e:
            logger.warning("MeteredProvider record dispatch failed: %s", e)

    async def _enforce_monthly_cap(self) -> None:
        """Hard stop if month-to-date LLM spend has hit ARIA_MONTHLY_CAP_USD.
        Lazy import keeps metered.py free of any cost_tracker import cycles."""
        try:
            from ..intel import cost_tracker
        except Exception:
            return
        # Let MonthlyCostCapExceeded propagate — callers expect a RuntimeError
        # subclass so chat/streaming endpoints can surface a useful message.
        await cost_tracker.assert_monthly_cap()

    async def complete(
        self,
        system_prompt: str,
        user_message: str,
        *,
        max_tokens: int = 4096,
        timeout: float = 60.0,
        prefer_provider: str = "",
    ) -> LLMResult:
        await self._enforce_monthly_cap()
        started = time.time()
        success = True
        error = ""
        result: LLMResult | None = None
        # R-F1366 — forward the per-call provider preference to the fallback
        # chain. Only when non-empty: a single (non-chain) inner provider
        # doesn't accept the kwarg, and the default path must stay identical.
        extra = {"prefer_provider": prefer_provider} if prefer_provider else {}
        try:
            result = await self._inner.complete(
                system_prompt,
                user_message,
                max_tokens=max_tokens,
                timeout=timeout,
                **extra,
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
        await self._enforce_monthly_cap()
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
