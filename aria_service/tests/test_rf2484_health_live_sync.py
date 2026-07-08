"""R-F2484 — the top-level /health/live alias must be a SYNC def so FastAPI runs
it in starlette's threadpool (immune to asyncio event-loop stalls from the
aiosqlite writer under state_store saturation). As async def it ran on the event
loop and timed out at 25s during a loop stall (live probe 2026-07-08).
"""
import inspect

from aria_service import main


def test_rf2484_health_live_is_sync_threadpool():
    fn = main.health_live_top_level
    # Sync def -> starlette threadpool -> not blocked by an event-loop stall.
    assert not inspect.iscoroutinefunction(fn), \
        "health_live_top_level must be a sync def (threadpool), not async def"


def test_rf2484_health_live_returns_alive_payload():
    result = main.health_live_top_level()   # sync call works only if it's def
    assert isinstance(result, dict)
    assert result.get("status") == "alive"
    assert "build_rev" in result
