"""R-F2004 — autonomous pause must auto-expire (live ecosystem can't be killed forever).

Root cause of the 187h news/sweep staleness: `pause_engine` set the pause flag
with NO TTL, so a single forgotten "pause to verify" left the engine fire=0
indefinitely — starving news_monitor -> intel_ledger -> signal_correlator -> BD.

These drive the REAL safety functions:
  - a LEGACY indefinite pause (flag set, no expiry) auto-resumes -> the literal fix
  - an EXPIRED pause auto-resumes
  - an ACTIVE bounded pause still holds, and resume clears it
  - a pause with no `minutes` is bounded by the default ceiling (never infinite)
"""
import asyncio
import time

from aria_service.autonomous import safety
from aria_service.intel import redis_store as rs


def _run(coro):
    return asyncio.run(coro)


async def _clear():
    if hasattr(rs, "delete"):
        await rs.delete(safety._PAUSE_KEY)
        await rs.delete(safety._PAUSE_UNTIL_KEY)
    else:
        await rs.set(safety._PAUSE_KEY, "0")
        await rs.set(safety._PAUSE_UNTIL_KEY, "0")


def test_legacy_indefinite_pause_auto_resumes():
    """The exact 187h outage: flag '1' with NO expiry key -> must self-heal."""
    async def go():
        await _clear()
        await rs.set(safety._PAUSE_KEY, "1")          # legacy pause, no until key
        if hasattr(rs, "delete"):
            await rs.delete(safety._PAUSE_UNTIL_KEY)
        assert await safety.is_engine_paused() is False, "legacy pause must auto-resume"
        assert (await rs.get(safety._PAUSE_KEY) or "").strip() != "1", "flag must be cleared"
    _run(go())


def test_expired_pause_auto_resumes():
    async def go():
        await _clear()
        await rs.set(safety._PAUSE_KEY, "1")
        await rs.set(safety._PAUSE_UNTIL_KEY, str(int(time.time()) - 10))  # already past
        assert await safety.is_engine_paused() is False
    _run(go())


def test_active_bounded_pause_holds_then_resume_clears():
    async def go():
        await _clear()
        await safety.pause_engine(reason="test", minutes=60)
        assert await safety.is_engine_paused() is True, "a fresh 60-min pause must hold"
        await safety.resume_engine()
        assert await safety.is_engine_paused() is False, "resume must clear it"
    _run(go())


def test_pause_without_minutes_is_bounded_not_infinite():
    async def go():
        await _clear()
        await safety.pause_engine(reason="no-minutes-given")
        until = float(await rs.get(safety._PAUSE_UNTIL_KEY))
        now = time.time()
        assert now < until, "must set a future expiry"
        assert until <= now + safety._DEFAULT_MAX_PAUSE_S + 5, "must be bounded by the default ceiling"
        await safety.resume_engine()
    _run(go())


def test_minutes_capped_at_hard_max():
    async def go():
        await _clear()
        await safety.pause_engine(reason="abuse", minutes=99999)  # ~69 days requested
        until = float(await rs.get(safety._PAUSE_UNTIL_KEY))
        now = time.time()
        assert until <= now + safety._HARD_MAX_PAUSE_S + 5, "must cap at the 24h hard max"
        await safety.resume_engine()
    _run(go())
