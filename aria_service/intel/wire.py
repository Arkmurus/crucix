"""R-F1776 — Failure-wire decorator for automatic brain wiring.

Wraps async functions so that ANY unhandled exception automatically emits a
wire_failure signal to the brain via capability_gaps.record_gap (already async,
deduped via R-F66 1h window, bounded via R-F1669 500 cap). This is the
STRUCTURAL fix for the 271 modules with dark failure paths.

Design constraints (per Claude §20 review):
  1. FAILURE-ONLY — never wire_success on every call (would wedge the loop).
     Success stays at path-level entry points only (§21a).
  2. REUSE existing non-blocking infra — route through capability_gaps.record_gap,
     NOT a new queue that could itself backlog/wedge.
  3. Non-blocking — fire-and-forget onto the existing bounded queue.
  4. MEASURE-GATE — capture wedge_stacks + p95 before/after applying to hot paths.
  5. Each module needs a §3c capability test: force failure, assert signal lands.

Usage:
    from .wire import fail_wire

    @fail_wire(module="my_module", gap_type="source_failure")
    async def my_function():
        ...

    # On exception: auto-calls capability_gaps.record_gap with the error.
    # On success: no signal (failure-only by design).
"""
from __future__ import annotations

import asyncio
import functools
import inspect
import logging
import time
from typing import Any, Callable, Optional

logger = logging.getLogger("aria.wire")

# R-F1776: strong references to background wire_failure tasks. Prevents GC
# from collecting the task before it records the gap (same bug class as
# R-F1363 — weak create_task refs are silently dropped under GC pressure).
_BG_TASKS: set = set()


async def _record_gap(gap_type: str, detail: str, source: str) -> None:
    """Record a capability gap. Wrapped so tests can patch it easily."""
    from ..intel import capability_gaps as _cg

    await _cg.record_gap(gap_type=gap_type, detail=detail, source=source)


# R-F1784: exceptions that are NORMAL control flow, not failures.
# fail_wire catches `except Exception`, which includes FastAPI/Starlette
# HTTPException (raised for every 4xx) and request-/response-validation errors.
# Recording a brain gap on those would flood the ledger with normal HTTP control
# flow (277 HTTPException raise-sites across routes/ alone). Matched by class
# NAME up the MRO so both fastapi.HTTPException and starlette.exceptions.
# HTTPException are covered WITHOUT importing either (keeps wire.py dependency-free).
# NOTE: asyncio.CancelledError derives from BaseException (not Exception) since
# Py3.8, so `except Exception` already never catches it — no entry needed.
_CONTROL_FLOW_EXC_NAMES: frozenset = frozenset({
    "HTTPException",
    "RequestValidationError",
    "ResponseValidationError",
})


def _is_control_flow(exc: BaseException, extra: tuple = ()) -> bool:
    """True if exc is normal control flow (by class name, up the MRO)."""
    names = _CONTROL_FLOW_EXC_NAMES.union(extra)
    return any(klass.__name__ in names for klass in type(exc).__mro__)


def fail_wire(
    module: str,
    gap_type: str = "agent_cycle_failure",
    source: str = "",
    max_detail_len: int = 500,
    control_flow_exempt: tuple = (),
) -> Callable:
    """Decorator: auto-wire_failure on any unhandled exception.

    Args:
        module: Module name for the gap record (e.g. "deep_researcher").
        gap_type: Gap type for record_gap (default "agent_cycle_failure").
        source: Source string (default "fail_wire:{module}").
        max_detail_len: Max chars for the error detail (default 500).
        control_flow_exempt: extra exception class names (beyond HTTPException /
            validation errors) this module treats as control flow, not failures.

    Returns:
        Decorated async function that auto-records failures to the brain.

    The decorator is NON-BLOCKING — it creates an asyncio.Task for the
    record_gap call so the caller is never blocked by brain signal delivery.
    The underlying record_gap is already async, deduped (R-F66 1h window),
    and bounded (R-F1669 500 cap).
    """
    if not source:
        source = f"fail_wire:{module}"

    def decorator(func: Callable) -> Callable:
        # R-F1782: GENERATOR GUARD — fail_wire can NEVER wrap a generator.
        # Wrapping lifespan (async generator) = boot outage (F28 class, §9).
        # Wrapping chat_stream_ep (async generator) = broken streaming chat.
        # Detect at DECORATION TIME so misapplication is impossible.
        if inspect.isasyncgenfunction(func):
            raise TypeError(
                f"fail_wire cannot wrap async generator '{func.__name__}'. "
                f"Async generators (async def ... yield) must be added to the "
                f"HARD EXEMPT registry with a reason. This prevents boot outages."
            )
        if inspect.isgeneratorfunction(func):
            raise TypeError(
                f"fail_wire cannot wrap sync generator '{func.__name__}'. "
                f"Generators (def ... yield) must be added to the HARD EXEMPT "
                f"registry with a reason."
            )

        if inspect.iscoroutinefunction(func):
            @functools.wraps(func)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                try:
                    return await func(*args, **kwargs)
                except Exception as e:
                    if not _is_control_flow(e, control_flow_exempt):
                        _wire_failure(
                            module=module,
                            gap_type=gap_type,
                            source=source,
                            detail=str(e)[:max_detail_len],
                            func_name=func.__name__,
                        )
                    raise

            return async_wrapper
        else:
            @functools.wraps(func)
            def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    if not _is_control_flow(e, control_flow_exempt):
                        _wire_failure(
                            module=module,
                            gap_type=gap_type,
                            source=source,
                            detail=str(e)[:max_detail_len],
                            func_name=func.__name__,
                        )
                    raise

            return sync_wrapper

    return decorator


# R-F2149: recursion guard for fail_wire. When record_gap itself raises (e.g.
# because a caller passed an unexpected kwarg), fail_wire catches it and calls
# _wire_failure again, which calls record_gap again — creating an infinite loop.
# This set tracks in-flight _wire_failure calls so we can detect and break the
# recursion. Keyed by (module, func_name) to allow independent failures through.
_WIRE_FAILURE_IN_FLIGHT: set = set()


def _wire_failure(
    module: str,
    gap_type: str,
    source: str,
    detail: str,
    func_name: str,
) -> None:
    """Fire-and-forget wire_failure via capability_gaps.record_gap.

    Runs in a background asyncio task so it NEVER blocks the caller.
    The underlying record_gap is already:
      - Async (non-blocking I/O)
      - Deduped (R-F66: same gap_type+detail within 1h window is skipped)
      - Bounded (R-F1669: MAX_GAPS=500 cap)

    R-F2149: recursion guard. If this module+func_name is already in-flight
    (i.e., we're being called from within record_gap's own fail_wire handler),
    we skip the wire_failure to prevent an infinite loop. The original error
    is still logged at DEBUG level.
    """
    import asyncio

    # R-F2149: recursion guard — break the loop if we're already recording
    # a failure for this module+func_name (means record_gap itself failed).
    _key = f"{module}:{func_name}"
    if _key in _WIRE_FAILURE_IN_FLIGHT:
        logger.debug(
            "[fail_wire] recursion guard: skipping wire_failure for %s "
            "(already in-flight — record_gap itself may have failed)",
            _key,
        )
        return
    _WIRE_FAILURE_IN_FLIGHT.add(_key)

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        logger.debug("[fail_wire] no running loop — skipping wire_failure for %s", module)
        _WIRE_FAILURE_IN_FLIGHT.discard(_key)
        return

    full_detail = f"[{func_name}] {detail}"[:500]

    async def _record():
        try:
            await _record_gap(
                gap_type=gap_type,
                detail=full_detail,
                source=source,
            )
            logger.debug(
                "[fail_wire] recorded gap for %s.%s: %s",
                module, func_name, detail[:100],
            )
        except Exception as _e:
            # The wire itself must NEVER raise — would mask the original error
            logger.debug("[fail_wire] record_gap failed (non-fatal): %s", _e)
        finally:
            _WIRE_FAILURE_IN_FLIGHT.discard(_key)

    # R-F1776: hold a STRONG reference to prevent GC from collecting the
    # task before it records the gap (same bug class as R-F1363 — weak
    # create_task refs are collected under GC pressure, silently losing
    # the exact failure signals this system exists to capture).
    _t = loop.create_task(_record())
    _BG_TASKS.add(_t)
    _t.add_done_callback(_BG_TASKS.discard)
