"""R-F1693 — the autonomous engine kill-switch must FAIL CLOSED.

Before R-F1693, `is_engine_paused()` returned False on any Redis error
("fail open"). That meant the operator's emergency stop evaporated during a
Redis outage — every autonomous loop (coder, gap_detector, self_improve, …)
would resume FULL autonomy precisely when state was unreliable. A safety stop
is the one control that must never fail open.

These capability tests drive the REAL pause/resume/is_paused path and assert:
  * a deliberate pause HOLDS through a Redis outage (the core fix),
  * a pause whose Redis write itself failed still takes effect,
  * when nobody paused, a transient Redis blip does NOT spuriously halt,
  * a successful read refreshes the in-process mirror,
  * per-task pause also fails closed to last-known.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aria_service.autonomous import safety


def _reset(paused: bool = False) -> None:
    safety._paused_inproc = paused
    safety._task_paused_inproc = {}


@pytest.mark.asyncio
async def test_pause_holds_through_redis_outage():
    """CORE: operator pauses, then Redis goes down → engine STAYS paused."""
    _reset(False)
    with patch.object(safety, "wire_success", MagicMock()), \
         patch.object(safety.rs, "set", AsyncMock()):
        await safety.pause_engine("emergency")
    assert safety._paused_inproc is True
    # Redis read now fails — pre-fix this returned False (resumed everything).
    with patch.object(safety.rs, "get", AsyncMock(side_effect=RuntimeError("redis down"))):
        assert await safety.is_engine_paused() is True


@pytest.mark.asyncio
async def test_pause_holds_even_when_redis_write_failed():
    """A pause whose Redis WRITE failed must still take effect (intent set first)."""
    _reset(False)
    with patch.object(safety, "wire_success", MagicMock()), \
         patch.object(safety, "wire_failure", MagicMock()), \
         patch.object(safety.rs, "set", AsyncMock(side_effect=RuntimeError("redis down"))):
        await safety.pause_engine("emergency")
    with patch.object(safety.rs, "get", AsyncMock(side_effect=RuntimeError("redis down"))):
        assert await safety.is_engine_paused() is True


@pytest.mark.asyncio
async def test_not_paused_blip_does_not_spuriously_halt():
    """When nobody paused, a transient Redis read error must NOT halt autonomy."""
    _reset(False)
    with patch.object(safety.rs, "get", AsyncMock(side_effect=RuntimeError("blip"))):
        assert await safety.is_engine_paused() is False


@pytest.mark.asyncio
async def test_successful_read_refreshes_mirror_and_resume_clears():
    _reset(True)
    with patch.object(safety.rs, "get", AsyncMock(return_value="")):
        assert await safety.is_engine_paused() is False
    assert safety._paused_inproc is False
    with patch.object(safety.rs, "get", AsyncMock(return_value="1")):
        assert await safety.is_engine_paused() is True
    assert safety._paused_inproc is True
    # resume clears intent first, holds through a subsequent outage
    with patch.object(safety, "wire_success", MagicMock()), \
         patch.object(safety.rs, "delete", AsyncMock()):
        await safety.resume_engine()
    with patch.object(safety.rs, "get", AsyncMock(side_effect=RuntimeError("down"))):
        assert await safety.is_engine_paused() is False


@pytest.mark.asyncio
async def test_task_pause_fails_closed_to_last_known():
    _reset(False)
    with patch.object(safety.rs, "get", AsyncMock(return_value="1")):
        assert await safety.is_task_paused("coder") is True
    with patch.object(safety.rs, "get", AsyncMock(side_effect=RuntimeError("down"))):
        assert await safety.is_task_paused("coder") is True       # held last-known
        assert await safety.is_task_paused("never_seen") is False  # default
