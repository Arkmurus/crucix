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
dropped — never blocked."""
from __future__ import annotations

import asyncio
import functools
import logging
import threading
from typing import Any, Callable, Coroutine, Optional

logger = logging.getLogger("aria.engine_wiring")


def _dispatch_fire_and_forget(coro_factory) -> None:
    """Run an async coroutine without blocking the caller.

    - Running loop  -> schedule a task (server/async context).
    - No loop (sync/CLI) -> run in a daemon thread so the caller returns
      immediately. Never raises.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop is not None:
        try:
            task = loop.create_task(coro_factory())
            task.add_done_callback(_noop_callback)
        except Exception:
            logger.debug("[engine_wiring] task dispatch failed", exc_info=True)
        return

    def _worker() -> None:
        try:
            asyncio.run(coro_factory())
        except Exception:
            logger.debug("[engine_wiring] background wiring failed", exc_info=True)

    try:
        threading.Thread(target=_worker, name="engine_wiring", daemon=True).start()
    except Exception:
        logger.debug("[engine_wiring] thread dispatch failed", exc_info=True)


def wire_success(
    module: str,
    summary: str,
    detail: str = "",
    entity_name: str = "",
    confidence: str = "ASSESSED",
    source_id: str = "",
) -> None:
    """Fire-and-forget brain signal for a successful engine run.

    Writes to brain_hook.absorb_silent so the neural memory + gap_detector
    see the output. Never raises, never blocks the caller.
    """
    try:
        from . import brain_hook as _bh

        _dispatch_fire_and_forget(lambda: _bh.absorb_silent(
            module=module,
            summary=summary[:300],
            detail=detail[:600],
            entity_name=entity_name[:120],
            success=True,
            confidence=confidence,
            source_id=source_id or module,
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

    Writes to capability_gaps.record_gap so the coder sees the failure
    and can attempt a fix. Never raises, never blocks the caller.
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

        @functools.wraps(func)
        async def _wrapper(*args: Any, **kwargs: Any) -> Any:
            # Build summary/detail from kwargs placeholders
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

            try:
                result = await func(*args, **kwargs)
            except Exception as exc:
                _fail_detail = _detail or f"{_name}: {exc}"
                wire_failure(
                    module=_module,
                    detail=_fail_detail[:600],
                    gap_type=gap_type,
                    source=_module,
                )
                raise

            # Falsy-success check (R-F1122): if the return is a dict with
            # success=False, treat it as a failure — kills the false-success
            # class of bugs where a function returns without raising.
            if check_falsy_success and isinstance(result, dict) and result.get("success") is False:
                _fail_detail = _detail or f"{_name} returned falsy success: {str(result.get('error', ''))[:200]}"
                wire_failure(
                    module=_module,
                    detail=_fail_detail[:600],
                    gap_type=gap_type,
                    source=_module,
                )
                return result

            # Success path
            _success_detail = _detail
            if capture_result and result is not None:
                _result_str = str(result)[:300]
                if _success_detail:
                    _success_detail = f"{_success_detail} | result: {_result_str}"
                else:
                    _success_detail = _result_str

            wire_success(
                module=_module,
                summary=_summary or f"{_name} completed",
                detail=_success_detail,
                entity_name=_entity,
                confidence=confidence,
                source_id=_module,
            )
            return result

        return _wrapper

    return _decorator
