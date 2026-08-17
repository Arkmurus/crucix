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
from ..intel.wire import fail_wire  # R-F1789 §21 brain-wiring
from ..intel.engine_wiring import wire_success, wire_failure  # R-F2489 §21a success+failure

logger = logging.getLogger("aria.llm.metered")

# R-F1568 — how long a paused-flag read is cached in-process. Keeps the kill
# switch from costing a Redis read per autonomous call/token while still
# reacting within a couple of seconds of the operator hitting pause.
_PAUSE_CACHE_TTL = 2.0


def _cost_attribution() -> str:
    """R-F4087 (C-134) — delegate to `cost_tracker.attribute_unscoped_caller()`.

    Must be called from `complete`/`stream` themselves, on the CALLER's stack:
    the actual record is written inside an `asyncio.create_task`, where the
    caller's frames no longer exist. Lazy import for the same reason every
    other cost_tracker use here is lazy — metered.py must stay free of cost
    import cycles — and fail-open, because attribution is bookkeeping and must
    never be the reason an LLM call fails.
    """
    try:
        from ..intel.cost_tracker import attribute_unscoped_caller
        return attribute_unscoped_caller()
    except Exception:
        return ""


class EnginePausedError(RuntimeError):
    """R-F1568 — raised when an AUTONOMOUS LLM call is issued while the engine
    is paused (the emergency kill switch). A RuntimeError subclass so callers
    that already handle the monthly-cap RuntimeError surface it the same way.
    Interactive operator/user chat is never raised against this."""


class MeteredProvider(LLMProvider):
    """Decorator that delegates to an inner provider and records cost
    after each call. The inner provider's identity (name, is_configured)
    is exposed transparently so callers can't tell the difference."""

    # R-F1568 — process-wide cache of the last paused read: (paused, read_at).
    # Class-level so all wrapped providers share one short-TTL view.
    _paused_cache: tuple[bool, float] | None = None

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

    def _record_cost(self, started: float, result, success: bool, error: str,
                     feature_name: str = "") -> None:
        """Fire-and-forget cost recording. Never blocks the caller.

        R-F4087 (C-134): `feature_name` carries the caller label captured at
        `complete`/`stream` entry. It must be captured THERE and threaded
        through, because `record_call` below runs inside a `create_task` — the
        contextvar survives (create_task copies the context) but the caller's
        stack frames do not, so attribution is impossible by that point.
        Empty means "a real scope is active, or the caller could not be named",
        and `record_call` then falls back to the contextvar exactly as before.

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
            # R-F4101 (C-145) — UNKNOWN USAGE IS NOT ZERO COST.
            #
            # The `else` below used to fall back to the provider's NAME as the
            # model. FallbackProvider.name is the literal string "fallback", so
            # 142 of 1,000 calls in 24 h (14.2%) landed on the cost panel as a
            # plausible-looking $0 "model" — one of them 23 seconds of
            # autonomous_engine work, priced at nothing.
            #
            # The tokens were spent; only our reading of them failed. Estimating
            # a count here would be a guess wearing a measurement's clothes, so
            # the record is FLAGGED instead: a call we could not read must stay
            # distinguishable from one that genuinely cost nothing, and the
            # count must be visible — §17, where a meter reading low is
            # otherwise indistinguishable from a quiet month.
            usage_unknown = False
            if result is not None:
                model = getattr(result, "model", "") or getattr(self._inner, "model", "")
                in_tk = int(getattr(result, "input_tokens", 0) or 0)
                out_tk = int(getattr(result, "output_tokens", 0) or 0)
                routed_via = getattr(result, "routed_via", "") or ""
            else:
                # Deliberately NOT the provider name — see above.
                model = getattr(self._inner, "model", "") or "unknown"
                usage_unknown = True
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
                feature_name=feature_name,
                usage_unknown=usage_unknown,   # R-F4101 (C-145)
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

    async def _enforce_spend_caps(self) -> None:
        """Hard stop if spend has hit either ceiling: ARIA_DAILY_CAP_USD
        (R-F2888) or ARIA_MONTHLY_CAP_USD. Lazy import keeps metered.py free of
        any cost_tracker import cycles.

        Daily is checked FIRST so a runaway is caught by the tighter, cheaper
        brake before it can eat into the month. Both raise RuntimeError
        subclasses, which chat/streaming endpoints already surface usefully.
        """
        try:
            from ..intel import cost_tracker
        except Exception:
            return
        await cost_tracker.assert_daily_cap()
        await cost_tracker.assert_monthly_cap()

    async def _enforce_kill_switch(self, autonomous: bool) -> None:
        """R-F1568 — hard money brake on the emergency pause / kill switch.

        Before R-F1568 the engine pause (POST /api/aria/autonomous/pause →
        autonomous.safety.pause_engine sets crucix:autonomous:paused) was only
        honoured BETWEEN cycles by the loops. The LLM provider layer enforced
        the $300 monthly cap on every call but had NO pause check — so when the
        operator hit the kill switch, in-flight and newly-issued autonomous LLM
        calls still completed and billed. This adds the missing brake.

        Scope is deliberately narrow:
          • Only AUTONOMOUS-origin calls are gated. Interactive operator/user
            chat (autonomous=False, the default) is NEVER blocked — the kill
            switch stops ARIA's own spend, not the human's.
          • Callers opt IN by passing autonomous=True (see complete/stream).
            The safe default means a caller that doesn't know about origin is
            treated as interactive and never refused.

        Cheap by design: the paused read is cached for a short TTL so a paused
        flag is not a Redis hit per token / per call under load.
        """
        # R-F1568 enablement: auto-detect autonomous origin via the LLM priority
        # contextvar. Autonomous loops set Priority.BACKGROUND (rate_limiter
        # .set_priority / .priority()); interactive user/operator chat stays at
        # the default INTERACTIVE. So a BACKGROUND-priority call IS autonomous
        # even when the caller didn't pass autonomous=True — this ENABLES the
        # kill switch for every existing autonomous loop with zero per-caller
        # changes, while interactive chat is never gated.
        if not autonomous:
            try:
                from .rate_limiter import get_priority, Priority
                if get_priority() >= Priority.BACKGROUND:
                    autonomous = True
            except Exception:
                pass
        if not autonomous:
            return  # interactive path — never gated
        if not await self._is_paused_cached():
            return
        raise EnginePausedError(
            "Autonomous LLM call refused: engine is paused (kill switch active)."
        )

    async def _is_paused_cached(self) -> bool:
        """Read the global engine pause flag with a brief in-process cache.

        Pause reader verified (§3b): aria_service/autonomous/safety.py
        `async def is_engine_paused() -> bool` reads crucix:autonomous:paused
        and returns True when set to "1" (fail-open on Redis error).
        """
        now = time.time()
        cached = MeteredProvider._paused_cache
        if cached is not None and (now - cached[1]) < _PAUSE_CACHE_TTL:
            return cached[0]
        try:
            from ..autonomous import safety
            paused = await safety.is_engine_paused()
        except Exception:
            # Fail OPEN for the gate: if we cannot read the pause state we do
            # NOT block the call (mirrors is_engine_paused's own fail-open).
            paused = False
        MeteredProvider._paused_cache = (paused, now)
        return paused

    @fail_wire(module="metered", gap_type="engine_failure")
    async def complete(
        self,
        system_prompt: str,
        user_message: str,
        *,
        max_tokens: int = 4096,
        timeout: float = 60.0,
        prefer_provider: str = "",
        autonomous: bool = False,
        model: str = "",   # R-F2769 — per-call Claude model override
    ) -> LLMResult:
        # R-F1568 — kill switch first: refuse autonomous spend BEFORE the
        # provider is invoked. autonomous defaults False so interactive
        # operator/user chat is never gated (callers opt in).
        await self._enforce_kill_switch(autonomous)
        await self._enforce_spend_caps()
        # R-F4087 (C-134) — capture WHO is spending, on the caller's own stack.
        # Empty when a real `feature()` scope is active, so correctly-scoped
        # callers are untouched.
        _feature_name = _cost_attribution()
        started = time.time()
        success = True
        error = ""
        result: LLMResult | None = None
        # R-F1366 — forward the per-call provider preference to the fallback
        # chain. Only when non-empty: a single (non-chain) inner provider
        # doesn't accept the kwarg, and the default path must stay identical.
        extra = {}
        if prefer_provider:
            extra["prefer_provider"] = prefer_provider
        if model:
            extra["model"] = model   # R-F2769 — forward the routed model to the chain
        try:
            result = await self._inner.complete(
                system_prompt,
                user_message,
                max_tokens=max_tokens,
                timeout=timeout,
                **extra,
            )
            # R-F2489 §21a — success branch reaches the brain (failures already
            # flow via the @fail_wire decorator on this method).
            wire_success(module="metered", summary=f"metered completion ({self.name})")
            return result
        except Exception as e:
            success = False
            error = str(e)
            raise
        finally:
            self._record_cost(started, result, success, error, _feature_name)

    async def stream(
        self,
        system_prompt: str,
        user_message: str,
        *,
        max_tokens: int = 4096,
        timeout: float = 120.0,
        on_done=None,
        autonomous: bool = False,
        model: str = "",   # R-F2769 — per-call Claude model override
    ):
        """Metered streaming — yields chunks, records cost after stream ends."""
        # R-F1568 — kill switch first (see complete()). Default False keeps
        # interactive streaming chat unaffected; only autonomous callers gate.
        await self._enforce_kill_switch(autonomous)
        await self._enforce_spend_caps()
        # R-F4087 (C-134) — §13: `stream` is a subset-fork of `complete`, so the
        # attribution hook is mirrored here. Streaming is the USER-FACING path;
        # leaving it out would keep exactly the spend a reader most wants named
        # sitting in `uncategorized`.
        _feature_name = _cost_attribution()
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
                **({"model": model} if model else {}),   # R-F2769
            ):
                yield chunk
            self._record_cost(started, final_result, True, "", _feature_name)
            # R-F2489 §21a — stream is an async generator (fail_wire cannot wrap
            # it), so wire BOTH branches explicitly here.
            wire_success(module="metered", summary=f"metered stream ({self.name})")
        except Exception as e:
            self._record_cost(started, final_result, False, str(e), _feature_name)
            wire_failure(
                module="metered", detail=f"metered stream failed ({self.name}): {e}",
                gap_type="engine_failure", source="metered",
            )
            raise
