"""R-F1769 — capability test for self-heal act+VERIFY in the bg supervisor.

Before: the supervisor wired "re-spawned successfully" the instant it created the
task — proving nothing (a loop that crashes on line 1 is `done()` immediately).
Now: it claims a TRUTHFUL "VERIFIED alive" success only once a re-spawned loop is
still live a full supervisor interval later. This test drives the real
_bg_supervisor_tick across two ticks and asserts attempt→verify.
"""
from __future__ import annotations

import asyncio

import aria_service.main as m


def _reset_state():
    m._BG_TASKS.clear()
    m._BG_RESPAWN.clear()
    m._BG_RESPAWN_COUNT.clear()
    m._BG_RESPAWN_PENDING.clear()


def test_supervisor_verifies_respawn_survival(monkeypatch):
    _reset_state()
    summaries: list[str] = []

    from aria_service.intel import brain_hook
    async def _spy_absorb(**k):
        summaries.append(k.get("summary", ""))
    monkeypatch.setattr(brain_hook, "absorb", _spy_absorb)

    async def _live_loop():
        await asyncio.sleep(100)  # survives → stays alive

    # Registered for respawn but NO live task present → supervisor sees it dead.
    m._BG_RESPAWN["healme"] = _live_loop

    async def body():
        # Tick 1: re-spawn — ATTEMPTED, not yet verified.
        r1 = await m._bg_supervisor_tick()
        assert "healme" in r1
        assert "healme" in m._BG_RESPAWN_PENDING, "must await survival verification"
        await asyncio.sleep(0.05)  # let the re-spawned loop + absorb tasks start

        # Tick 2: the re-spawned loop is sleeping → alive → VERIFIED.
        await m._bg_supervisor_tick()
        assert "healme" not in m._BG_RESPAWN_PENDING, "survival must be verified"
        await asyncio.sleep(0.05)

        # cleanup
        for t in list(m._BG_TASKS):
            t.cancel()

    asyncio.run(body())
    _reset_state()

    assert any("ATTEMPTED" in s for s in summaries), \
        "first tick must wire honest 'attempted', not premature success"
    assert any("VERIFIED" in s for s in summaries), \
        "second tick must wire the truthful 'verified alive' only after survival"


def test_respawn_that_dies_again_is_not_verified(monkeypatch):
    """A re-spawn that immediately dies must NOT be marked verified — it stays
    pending and the bounded counter escalates (honest: heal didn't work)."""
    _reset_state()
    from aria_service.intel import brain_hook
    async def _noop_absorb(**k):
        return None
    monkeypatch.setattr(brain_hook, "absorb", _noop_absorb)

    async def _instant_die():
        return  # exits immediately → done() → still "dead"

    m._BG_RESPAWN["flaky"] = _instant_die

    async def body():
        await m._bg_supervisor_tick()          # respawn 1
        assert "flaky" in m._BG_RESPAWN_PENDING
        await asyncio.sleep(0.02)              # the loop exits immediately
        await m._bg_supervisor_tick()          # still dead → NOT verified, respawn 2
        # It must NOT have been silently verified; counter advanced past 1.
        assert m._BG_RESPAWN_COUNT.get("flaky", 0) >= 2, "must keep re-acting, not claim success"
        for t in list(m._BG_TASKS):
            t.cancel()

    asyncio.run(body())
    _reset_state()
