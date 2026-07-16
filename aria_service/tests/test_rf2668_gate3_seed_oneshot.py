"""R-F2668 — the one-shot knowledge seed must NOT be respawn-supervised (gate #3).

After R-F2663 removed the FALSE per-boot warmup reset, the next gate-#3 reset was a
GENUINE one that the warmup had masked: `_seed_knowledge_bg` (main.py) is a ONE-SHOT
(runs run_knowledge_seed once, catches its own errors at WARNING, RETURNS), but
`_singleton_task` registered it with the bg supervisor's RESPAWN factory. The
supervisor only knows "not done() = live" (_bg_supervisor_tick), so a clean one-shot
completion looks like a death → re-spawned every time → hits _BG_MAX_RESPAWNS → the
supervisor emits `[R-F1610] … NEEDS OPERATOR` at ERROR → is_reset_type=True → RESET the
gate-#3 streak on every boot.

R-F2668: `_singleton_task(..., respawn=False)` for the one-shot — keep the R-F2073
singleton lock, but do NOT register it for re-spawn. Genuine while-True loops keep
respawn=True so real crashes still self-heal.
"""
from __future__ import annotations

import ast
import asyncio
import pathlib

import pytest

import aria_service.main as m


# ── §3c clean guard: the seed_knowledge call must use respawn=False ──────────
def test_seed_knowledge_singleton_uses_respawn_false():
    src = pathlib.Path(m.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    found = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "_singleton_task":
            # is this the seed_knowledge registration?
            if any(isinstance(a, ast.Constant) and a.value == "seed_knowledge" for a in node.args):
                found = node
                break
    assert found is not None, "_singleton_task(..., 'seed_knowledge') call not found in main.py"
    kw = {k.arg: k.value for k in found.keywords}
    assert "respawn" in kw and isinstance(kw["respawn"], ast.Constant) and kw["respawn"].value is False, (
        "the one-shot seed_knowledge must be registered respawn=False (R-F2668) — else its "
        "clean completion is re-spawned to the R-F1610 ERROR that resets gate #3")


# ── functional: respawn flag controls supervisor registration ────────────────
@pytest.mark.asyncio
async def test_respawn_false_is_not_registered(monkeypatch):
    monkeypatch.setattr(m, "_runs_singletons", lambda: True)
    monkeypatch.setattr(m, "_BG_RESPAWN", {})
    monkeypatch.setattr(m, "_BG_TASKS", set())

    async def _oneshot():
        return
    t = m._singleton_task(_oneshot, "seed_test", respawn=False)
    assert "seed_test" not in m._BG_RESPAWN, "a one-shot (respawn=False) must NOT be respawn-registered"
    if t:
        await t  # complete cleanly


@pytest.mark.asyncio
async def test_respawn_true_default_is_registered(monkeypatch):
    monkeypatch.setattr(m, "_runs_singletons", lambda: True)
    monkeypatch.setattr(m, "_BG_RESPAWN", {})
    monkeypatch.setattr(m, "_BG_TASKS", set())

    async def _loop():
        return
    t = m._singleton_task(_loop, "loop_test")  # default respawn=True
    assert "loop_test" in m._BG_RESPAWN, "genuine loops must stay respawn-registered (default unchanged)"
    if t:
        await t


# ── functional: supervisor no longer respawns the unregistered one-shot, but
#    still heals a registered crashed loop ───────────────────────────────────
@pytest.mark.asyncio
async def test_supervisor_ignores_unregistered_oneshot(monkeypatch):
    monkeypatch.setattr(m, "_BG_RESPAWN", {})            # seed_knowledge no longer here
    monkeypatch.setattr(m, "_BG_TASKS", set())
    monkeypatch.setattr(m, "_BG_RESPAWN_COUNT", {})
    monkeypatch.setattr(m, "_BG_RESPAWN_PENDING", set())

    async def _oneshot():
        return
    done = asyncio.create_task(_oneshot(), name="seed_knowledge")
    m._BG_TASKS.add(done)
    await done  # clean completion (done() = True)

    respawned = await m._bg_supervisor_tick()
    assert "seed_knowledge" not in respawned, (
        "a completed one-shot that is NOT respawn-registered must NOT be re-spawned (R-F2668)")


@pytest.mark.asyncio
async def test_supervisor_still_heals_registered_crashed_loop(monkeypatch):
    """Regression: the fix must NOT weaken self-healing for genuine loops — a
    registered loop whose task died is still re-spawned."""
    spawns = {"n": 0}

    async def _factory():
        spawns["n"] += 1
        return  # dies immediately (stand-in for a crashed while-True loop)

    monkeypatch.setattr(m, "_BG_RESPAWN", {"crashed_loop": _factory})
    monkeypatch.setattr(m, "_BG_TASKS", set())
    monkeypatch.setattr(m, "_BG_RESPAWN_COUNT", {})
    monkeypatch.setattr(m, "_BG_RESPAWN_PENDING", set())

    dead = asyncio.create_task(_factory(), name="crashed_loop")
    m._BG_TASKS.add(dead)
    await dead

    respawned = await m._bg_supervisor_tick()
    assert "crashed_loop" in respawned, "supervisor must still re-spawn a registered dead loop"


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
