"""R-F2141 — Permanent kill switch for autonomous engine.

Unlike the auto-expiring pause (which self-heals within 24h), the safety_stop
flag is MANUALLY cleared and NEVER auto-resumes. Intended for operator-initiated
emergency stop that must persist until explicitly released.

Tests drive the REAL safety functions with a mocked Redis store.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock


def _mock_redis():
    """Create a mock Redis store with dict-backed storage."""
    store = MagicMock()
    store._data: dict[str, str] = {}

    async def _get(key):
        return store._data.get(key)

    async def _set(key, val):
        store._data[key] = val

    async def _delete(key):
        store._data.pop(key, None)

    store.get = AsyncMock(side_effect=_get)
    store.set = AsyncMock(side_effect=_set)
    store.delete = AsyncMock(side_effect=_delete)
    return store


def test_rf2141_safety_stop_prevents_engine_run(monkeypatch):
    """safety_stop set → is_engine_paused returns True (engine blocked)."""
    from aria_service.autonomous import safety as _safety
    mock_rs = _mock_redis()
    monkeypatch.setattr(_safety, "rs", mock_rs)

    # Set the safety_stop flag
    import asyncio
    asyncio.run(_safety.safety_stop_engine("test emergency"))

    # Engine should be paused
    paused = asyncio.run(_safety.is_engine_paused())
    assert paused is True, "Engine should be paused after safety_stop"


def test_rf2141_safety_stop_does_not_auto_expire(monkeypatch):
    """safety_stop persists — it does NOT auto-expire like a regular pause."""
    from aria_service.autonomous import safety as _safety
    mock_rs = _mock_redis()
    monkeypatch.setattr(_safety, "rs", mock_rs)

    import asyncio
    asyncio.run(_safety.safety_stop_engine("persistent stop"))

    # Even after "time passes", safety_stop should still be active
    # (unlike pause_engine which has a TTL-based expiry)
    paused = asyncio.run(_safety.is_engine_paused())
    assert paused is True, "Safety stop should persist indefinitely"


def test_rf2141_safety_release_clears_stop(monkeypatch):
    """safety_release_engine clears the safety_stop → engine can run."""
    from aria_service.autonomous import safety as _safety
    mock_rs = _mock_redis()
    monkeypatch.setattr(_safety, "rs", mock_rs)

    import asyncio

    # Stop the engine
    asyncio.run(_safety.safety_stop_engine("test"))
    assert asyncio.run(_safety.is_engine_paused()) is True

    # Release the stop
    asyncio.run(_safety.safety_release_engine())
    paused = asyncio.run(_safety.is_engine_paused())
    assert paused is False, "Engine should resume after safety_release"


def test_rf2141_safety_stop_takes_priority_over_pause(monkeypatch):
    """safety_stop is checked BEFORE pause — if safety_stop is set, pause state
    doesn't matter (engine stays stopped)."""
    from aria_service.autonomous import safety as _safety
    mock_rs = _mock_redis()
    monkeypatch.setattr(_safety, "rs", mock_rs)

    import asyncio

    # Set safety_stop
    asyncio.run(_safety.safety_stop_engine("priority test"))

    # Even if we try to resume (which clears pause but NOT safety_stop)
    asyncio.run(_safety.resume_engine())

    # Engine should still be stopped because safety_stop is still set
    paused = asyncio.run(_safety.is_engine_paused())
    assert paused is True, "Safety stop should take priority over resume"


def test_rf2141_can_task_run_blocked_by_safety_stop(monkeypatch):
    """can_task_run returns False when safety_stop is engaged."""
    from aria_service.autonomous import safety as _safety
    mock_rs = _mock_redis()
    monkeypatch.setattr(_safety, "rs", mock_rs)

    import asyncio

    # Set safety_stop
    asyncio.run(_safety.safety_stop_engine("block all tasks"))

    # can_task_run should be blocked
    allowed, reason = asyncio.run(_safety.can_task_run("test_task", "test_entity"))
    assert allowed is False, "can_task_run should return False when safety_stop is set"
    assert "paused" in reason, f"Reason should mention paused, got: {reason}"
