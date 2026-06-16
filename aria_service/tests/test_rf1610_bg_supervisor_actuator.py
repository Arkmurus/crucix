"""R-F1610 — self-healing actuator: the bg supervisor RE-SPAWNS dead loops.

Operator's core gap (2026-06-16): ARIA "detects but doesn't heal." self_healing
logged stale heartbeats but never restarted a dead loop. R-F1610 adds a
supervisor that re-spawns any registered bg loop whose task died (bounded by
_BG_MAX_RESPAWNS), turning detection into ACTION. Pairs with R-F1609 (which
fixed the import bug that killed the loops) as defence-in-depth for future
deaths.

These capability tests drive the real _bg_task factory registration + the real
_bg_supervisor_tick re-spawn decision.
"""
from __future__ import annotations

import asyncio

import pytest

import aria_service.main as m


def _reset():
    m._BG_TASKS.clear()
    m._BG_RESPAWN.clear()
    m._BG_RESPAWN_COUNT.clear()


@pytest.mark.asyncio
async def test_rf1610_bg_task_registers_factory():
    _reset()
    async def _f():
        await asyncio.sleep(3600)
    t = asyncio.create_task(_f(), name="probe_loop")
    m._bg_task(t, factory=_f)
    assert m._BG_RESPAWN.get("probe_loop") is _f
    t.cancel()


@pytest.mark.asyncio
async def test_rf1610_tick_respawns_dead_loop():
    """A registered loop whose task DIED must be re-spawned (factory re-called)."""
    _reset()
    calls = {"n": 0}
    async def _dead():
        calls["n"] += 1
        return  # exits immediately = a dead loop
    t = asyncio.create_task(_dead(), name="dead_loop")
    m._bg_task(t, factory=_dead)
    await asyncio.sleep(0.05)            # let it finish → done + discarded from _BG_TASKS
    assert calls["n"] == 1

    respawned = await m._bg_supervisor_tick()
    assert "dead_loop" in respawned, "supervisor did not re-spawn the dead loop"
    await asyncio.sleep(0.05)
    assert calls["n"] == 2, "factory was not re-invoked on re-spawn"
    for x in list(m._BG_TASKS):
        x.cancel()


@pytest.mark.asyncio
async def test_rf1610_tick_leaves_live_loop_alone():
    """A merely-sleeping (not done) loop is 'live' → must NOT be re-spawned."""
    _reset()
    async def _alive():
        await asyncio.sleep(3600)
    t = asyncio.create_task(_alive(), name="alive_loop")
    m._bg_task(t, factory=_alive)
    respawned = await m._bg_supervisor_tick()
    assert "alive_loop" not in respawned
    t.cancel()


@pytest.mark.asyncio
async def test_rf1610_respawn_is_bounded():
    """After _BG_MAX_RESPAWNS, the supervisor stops re-spawning (no crash-loop)
    and latches to alert once."""
    _reset()
    async def _dead():
        return
    m._BG_RESPAWN["capped_loop"] = _dead
    m._BG_RESPAWN_COUNT["capped_loop"] = m._BG_MAX_RESPAWNS  # already at the cap
    respawned = await m._bg_supervisor_tick()
    assert "capped_loop" not in respawned, "exceeded the respawn cap but still re-spawned"
