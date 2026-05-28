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
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

logger = logging.getLogger("aria.engine_wiring")


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
    see the output. Never raises.
    """
    try:
        from . import brain_hook as _bh

        _t = asyncio.create_task(_bh.absorb_silent(
            module=module,
            summary=summary[:300],
            detail=detail[:600],
            entity_name=entity_name[:120],
            success=True,
            confidence=confidence,
            source_id=source_id or module,
        ))
        _t.add_done_callback(_noop_callback)
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
    and can attempt a fix. Never raises.
    """
    try:
        from . import capability_gaps as _cg

        _t = asyncio.create_task(_cg.record_gap(
            gap_type=gap_type,
            detail=detail[:600],
            source=source or module,
        ))
        _t.add_done_callback(_noop_callback)
    except Exception:
        logger.debug("[engine_wiring] wire_failure failed for %s", module, exc_info=True)


def _noop_callback(t: asyncio.Task) -> None:
    """Safely consume a fire-and-forget task result."""
    try:
        if not t.cancelled():
            t.exception()
    except (asyncio.CancelledError, Exception):
        pass
