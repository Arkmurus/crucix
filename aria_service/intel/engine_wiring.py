"""R-F994 — Centralised brain-wiring helpers for intel engines.

Every intel module that produces analysis output should call one of these
helpers on BOTH success and failure paths, so ARIA's brain (gap_detector,
capability_gaps, mistake_ledger) sees every engine's output.

Usage:
    
    # On success:
    wire_success("my_engine", "Summary of what happened", detail="...")

    # On failure:
    wire_failure("my_engine", "What went wrong", gap_type="engine_failure")

    # As a decorator (R-F1121):
    @wired(module="my_engine", summary="Analysis complete for {entity_name}")
    async def my_engine(entity_name: str) -> dict:
        ...
        return result

R-F1022 — these helpers are STRICTLY fire-and-forget and MUST NOT block the
caller. In a running event loop they schedule a task; in a sync/CLI context
(no loop) they run the brain absorb on a DAEMON THREAD instead of blocking on
`asyncio.run(...)`. Blocking here was the cause of the ~10-minute self-coder
stalls: `reserve_r_number.py` (a sync CLI) called `wire_success`, which ran a
full neural-memory brain absorb synchronously on every R-number reservation,
freezing ARIA's loop on every task. Wiring is best-effort (CLAUDE.md §21a):
if a sync caller exits before the daemon thread finishes, the signal is
dropped — never blocked.

R-F1539 — BOOT SIGNAL STAGGERING. At boot, ~124 modules fire wire_success()
simultaneously via module-level import-time wiring. This spike tripped the
brain_hook circuit breaker (p95=30,951ms). The fix is a rate-limited dispatch
that queues signals during the first 30s of uptime and releases them at a
controlled pace (20/s). After the boot window, signals pass through
unrestricted. The rate limiter is a simple token-bucket: it never blocks the
caller, it just re-schedules the signal with a short delay."""
from __future__ import annotations

import asyncio
import functools
import logging
import threading
import time
from typing import Any, Callable, Coroutine, Optional

logger = logging.getLogger("aria.engine_wiring")

# R-F1539: boot signal staggering — token-bucket rate limiter.
# During the first BOOT_WINDOW_S seconds of uptime, signals are dispatched
# at BOOT_RATE signals/second to avoid swamping brain_hook on startup.
# After the boot window, the limiter is disabled (unlimited throughput).
_BOOT_START = time.monotonic()
_BOOT_WINDOW_S = 30
_BOOT_RATE = 20  # signals per second during boot
_BOOT_TOKENS = _BOOT_RATE  # initial token bucket
_BOOT_LAST_REFILL = _BOOT_START
_BOOT_LOCK = threading.Lock()


def _in_boot_window() -> bool:
    """True if we're still in the boot signal-staggering window."""
    return time.monotonic() - _BOOT_START < _BOOT_WINDOW_S


def _acquire_boot_token() -> float:
    """Try to acquire a token from the boot rate limiter.

    Returns the delay in seconds before the signal should be dispatched.
    0.0 means dispatch immediately (token available or boot window over).
    """
    if not _in_boot_window():
        return 0.0

    with _BOOT_LOCK:
        global _BOOT_TOKENS, _BOOT_LAST_REFILL
        now = time.monotonic()
        elapsed = now - _BOOT_LAST_REFILL
        _BOOT_TOKENS = min(_BOOT_RATE, _BOOT_TOKENS + elapsed * _BOOT_RATE)
        _BOOT_LAST_REFILL = now

        if _BOOT_TOKENS >= 1.0:
            _BOOT_TOKENS -= 1.0
            return 0.0  # dispatch immediately

        # No tokens available — calculate delay until next token
        deficit = 1.0 - _BOOT_TOKENS
        delay = deficit / _BOOT_RATE
        # Reserve the token for when the delay elapses
        _BOOT_TOKENS = 0.0
        return delay


def _dispatch_fire_and_forget(coro_factory) -> None:
    """Run an async coroutine without blocking the caller.

    - Running loop  -> schedule a task (server/async context).
    - No loop (sync/CLI) -> run in a daemon thread so the caller returns
      immediately. Never raises.

    R-F1539: during the boot window, signals are rate-limited to BOOT_RATE/s
    to prevent the 124-module signal spike from tripping brain_hook's circuit
    breaker. The delay is applied via loop.call_later so the caller is never
    blocked.
    """
    delay = _acquire_boot_token()

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop is not None:
        try:
            if delay > 0:
                loop.call_later(delay, lambda: loop.create_task(coro_factory()).add_done_callback(_noop_callback))
            else:
                task = loop.create_task(coro_factory())
                task.add_done_callback(_noop_callback)
        except Exception:
            logger.debug("[engine_wiring] task dispatch failed", exc_info=True)
        return

    def _worker() -> None:
        if delay > 0:
            time.sleep(delay)
        try:
            asyncio.run(coro_factory())
        except Exception:
            logger.debug("[engine_wiring] background wiring failed", exc_info=True)

    try:
        threading.Thread(target=_worker, name="engine_wiring", daemon=True).start()
    except Exception:
        logger.debug("[engine_wiring] thread dispatch failed", exc_info=True)


# R-F4052 (C-108) — last emission per module, for `wire_success_throttled`.
# Process-local and unbounded only in the number of MODULES, which is finite.
_SUCCESS_LAST: dict[str, float] = {}
_SUCCESS_MIN_INTERVAL_S = 300.0


def wire_success_throttled(
    module: str,
    summary: str,
    *,
    min_interval_s: float = _SUCCESS_MIN_INTERVAL_S,
    **kwargs: Any,
) -> bool:
    """Rate-limited success signal. Returns True if a signal was emitted.

    R-F4052 — WHY THIS EXISTS. §21a requires a success branch, but a module
    whose work function runs per ITEM (`record_bookmark`, `mark_processed`,
    `review_topic`) would emit thousands of identical "it worked" signals into
    a 500-slot ledger. That flood is not hypothetical: `cost_tracker` and
    `grounding_reward` are exempt from §21a for exactly this reason,
    `loop_monitor` (R-F3557) rate-limits both its breach and healthy signals,
    and C-102's audit wiring had to report on CHANGE for the same cause.

    Putting the cooldown HERE — next to the primitive it throttles — means the
    next per-item module gets it for free instead of copying the pattern and
    getting the reset condition subtly wrong.

    Failures are deliberately NOT throttled: they are rare, and `wire_failure`
    already routes through `capability_gaps.record_gap`, which dedupes 1h
    (R-F66). Throttling them too would hide a newly-broken module.

    Never raises — telemetry must not break the caller's work.
    """
    try:
        now = time.monotonic()
        last = _SUCCESS_LAST.get(module)
        if last is not None and (now - last) < min_interval_s:
            return False
        _SUCCESS_LAST[module] = now
        wire_success(module=module, summary=summary, **kwargs)
        return True
    except Exception as exc:      # pragma: no cover - telemetry never blocks
        logger.debug("[R-F4052] throttled success wiring failed (%s): %s",
                     module, exc)
        return False


def wire_success(
    module: str,
    summary: str,
    detail: str = "",
    entity_name: str = "",
    confidence: str = "ASSESSED",
    source_id: str = "",
) -> None:
    """Fire-and-forget brain signal for a successful engine run.

    R-F1664 (wedge cure 1): routes to brain_hook.record_signal — the
    lightweight §21a metric path — instead of the heavy absorb_silent.
    wire_success is per-tick / per-module telemetry ("engine X ran"); routing
    it through the expensive mastery+knowledge+neural tiers flooded the absorb
    pipeline (live probe 2026-06-18: 258 heavy absorb() vs 21 metric calls) and
    was a dominant driver of the absorb-p95 wedge (autonomous_scheduler tick +
    124-module boot burst). Genuine new knowledge is absorbed by the engines
    via direct absorb() calls; this success-telemetry belongs in the metric
    counter. The module stays §21a-wired (success branch -> metric); failures
    still flow through wire_failure -> capability_gaps.record_gap. Never raises.
    """
    try:
        from . import brain_hook as _bh

        _dispatch_fire_and_forget(lambda: _bh.record_signal(
            module=module,
            success=True,
            summary=summary[:300],
        ))
    except Exception:
        logger.debug("[engine_wiring] wire_success failed for %s", module, exc_info=True)


def wire_failure(
    module: str,
    detail: str,
    gap_type: str = "engine_failure",
    source: str = "",
) -> None:
    """Fire-and-forget brain signal for an engine failure.

    Writes to BOTH sinks, because they answer different questions:
      - capability_gaps.record_gap → the coder loop ("something to fix"),
        deduped 1h (R-F66) and capped at 500 (R-F1669)
      - brain_hook.record_signal(success=False) → the HEALTH metric
        ("this module is failing"), which is what /api/aria/brain/stats reads

    R-F3036 (2026-07-25) — the second sink was MISSING, and the asymmetry was
    structural: wire_success routed to brain_hook.record_signal while
    wire_failure routed only to record_gap. A module's `fail` counter could
    therefore never be incremented by the wiring layer, so EVERY module on
    /api/aria/brain/stats read `fail=0, success_rate=1.0` — a health surface
    that certifies health by construction, in the same family as the Phase A
    gates that "could not fail" (CLAUDE.md §1).

    Measured 2026-07-25: the primary LLM was returning HTTP 400 on 100% of
    calls (retired `deepseek-chat` model id). 258/258 calls failed over
    ~2h40m. Across all 106 modules on /brain/stats: `fail=0` everywhere,
    every success_rate 1.0. The gap was filed and deduped to roughly one an
    hour; nothing the operator watches ever moved. Recording the failure
    metric as well is what makes a dead limb visible (§25a proprioception).

    Never raises, never blocks the caller.
    """
    try:
        from . import capability_gaps as _cg

        _dispatch_fire_and_forget(lambda: _cg.record_gap(
            gap_type=gap_type,
            detail=detail[:600],
            source=source or module,
        ))
    except Exception:
        logger.debug("[engine_wiring] wire_failure failed for %s", module, exc_info=True)

    # R-F3036 — the health metric. Dispatched separately so a failure in the
    # gap sink can never suppress the health signal (and vice versa): before
    # this, one shared try/except meant a single broken sink silenced both.
    try:
        from . import brain_hook as _bh

        _dispatch_fire_and_forget(lambda: _bh.record_signal(
            module=module,
            success=False,
            summary=detail[:300],
        ))
    except Exception:
        logger.debug("[engine_wiring] wire_failure metric failed for %s", module, exc_info=True)


def _noop_callback(t: "asyncio.Task") -> None:
    """Safely consume a fire-and-forget task result."""
    try:
        if not t.cancelled():
            t.exception()
    except (asyncio.CancelledError, Exception):
        pass


# ── @wired decorator (R-F1121) ──────────────────────────────────────────────

def wired(
    module: str = "",
    *,
    summary: str = "",
    detail: str = "",
    entity_arg: str = "",
    confidence: str = "ASSESSED",
    gap_type: str = "engine_failure",
    capture_result: bool = False,
    check_falsy_success: bool = False,
) -> Callable[[Callable[..., Coroutine[Any, Any, Any]]], Callable[..., Coroutine[Any, Any, Any]]]:
    """Decorator that auto-wires an async function's success and failure to the brain.

    Usage::

        @wired(module="my_engine", summary="Analysis complete")
        async def my_func(entity_name: str) -> dict:
            ...

    On success: calls ``wire_success(module, summary, ...)``.
    On exception: calls ``wire_failure(module, detail, gap_type=gap_type)``.

    This is the PREFERRED way to wire a module. Every module that calls
    ``wire_success`` directly should ALSO call ``wire_failure`` on its
    error paths. The ``@wired`` decorator guarantees both paths are covered.

    Args:
        module: Module name for brain signals. Defaults to the function's
            ``__module__`` if empty.
        summary: Summary template. Supports ``{arg_name}`` placeholders
            that are filled from the function's keyword arguments.
        detail: Detail template (same placeholder support).
        entity_arg: Name of the keyword argument to use as ``entity_name``
            in the success signal. If empty, no entity name is sent.
        confidence: Confidence level for success signals.
        gap_type: Gap type for failure signals.
        capture_result: If True, the return value's ``__str__`` (first 300
            chars) is appended to the success detail. Use sparingly — most
            engines should write their own summary.
        check_falsy_success: If True, checks if the return value is a dict
            with ``success=False`` and fires ``wire_failure`` instead of
            ``wire_success``. Kills the false-success class of bugs where
            a function returns a falsy-success dict without raising.
    """
    def _decorator(
        func: Callable[..., Coroutine[Any, Any, Any]],
    ) -> Callable[..., Coroutine[Any, Any, Any]]:
        _module = module or func.__module__
        _name = func.__name__

        def _prep(kwargs: dict) -> tuple:
            _summary = summary
            _detail = detail
            if kwargs:
                try:
                    _summary = summary.format(**kwargs)
                except (KeyError, ValueError):
                    _summary = summary
                try:
                    _detail = detail.format(**kwargs)
                except (KeyError, ValueError):
                    _detail = detail
            _entity = kwargs.get(entity_arg, "") if entity_arg else ""
            return _summary, _detail, _entity

        def _emit_failure(_detail: str, exc: Exception) -> None:
            _fail_detail = _detail or f"{_name}: {exc}"
            wire_failure(module=_module, detail=_fail_detail[:600], gap_type=gap_type, source=_module)

        def _emit_result(result: Any, _summary: str, _detail: str, _entity: str) -> None:
            # Falsy-success check (R-F1122): a dict with success=False is a failure even
            # though the function returned without raising.
            if check_falsy_success and isinstance(result, dict) and result.get("success") is False:
                _fail_detail = _detail or f"{_name} returned falsy success: {str(result.get('error', ''))[:200]}"
                wire_failure(module=_module, detail=_fail_detail[:600], gap_type=gap_type, source=_module)
                return
            _success_detail = _detail
            if capture_result and result is not None:
                _result_str = str(result)[:300]
                _success_detail = f"{_success_detail} | result: {_result_str}" if _success_detail else _result_str
            wire_success(
                module=_module, summary=_summary or f"{_name} completed",
                detail=_success_detail, entity_name=_entity, confidence=confidence, source_id=_module,
            )

        # R-F2274 — @wired MUST handle SYNC functions too. The old async-only wrapper turned a
        # decorated `def` into an un-awaitable coroutine, so callers got "'coroutine' object has
        # no attribute ..." — silently breaking get_country_risk + financial_findings in EVERY
        # DD (all country-risk/CPI/FATF/Basel substance was lost + the raw error shown to users).
        # Detect the function kind and wrap accordingly; async behaviour is unchanged.
        if asyncio.iscoroutinefunction(func):
            @functools.wraps(func)
            async def _wrapper(*args: Any, **kwargs: Any) -> Any:
                _summary, _detail, _entity = _prep(kwargs)
                try:
                    result = await func(*args, **kwargs)
                except Exception as exc:
                    _emit_failure(_detail, exc)
                    raise
                _emit_result(result, _summary, _detail, _entity)
                return result
            return _wrapper

        @functools.wraps(func)
        def _sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            _summary, _detail, _entity = _prep(kwargs)
            try:
                result = func(*args, **kwargs)
            except Exception as exc:
                _emit_failure(_detail, exc)
                raise
            _emit_result(result, _summary, _detail, _entity)
            return result

        return _sync_wrapper

    return _decorator
