"""R-F994 — Centralised brain-wiring helpers for intel engines.

Every intel module that produces analysis output should call one of these
helpers on BOTH success and failure paths, so ARIA's brain (gap_detector,
capability_gaps, mistake_ledger) sees every engine's output.

Usage:
    from .engine_wiring import wire_success, wire_failure

    # On success:
    wire_success("my_engine", "Summary of what happened", detail="...")

    # On failure:
    wire_failure("my_engine", "What went wrong", gap_type="engine_failure")

R-F1022 — these helpers are STRICTLY fire-and-forget and MUST NOT block the
caller. In a running event loop they schedule a task; in a sync/CLI context
(no loop) they run the brain absorb on a DAEMON THREAD instead of blocking on
`asyncio.run(...)`. Blocking here was the cause of the ~10-minute self-coder
stalls: `reserve_r_number.py` (a sync CLI) called `wire_success`, which ran a
full neural-memory brain absorb synchronously on every R-number reservation,
freezing ARIA's loop on every task. Wiring is best-effort (CLAUDE.md §21a):
if a sync caller exits before the daemon thread finishes, the signal is
dropped — never blocked.
"""
from __future__ import annotations

import asyncio
import logging
import threading

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
