"""R-F1878 — the DD completion brain-signal must be fire-and-forget.

Awaiting brain_hook.absorb in the DD critical path let a ~29s GIL-bound embed
block DD completion and trip the brain_hook circuit. _fire_signal must dispatch
the absorb as a tracked background task and return immediately.
"""
from __future__ import annotations

import asyncio
import time

import aria_service.intel.dd_orchestrator as dd
import aria_service.intel.brain_hook as bh


def test_fire_signal_returns_immediately_and_runs_in_background(monkeypatch):
    ran = {"done": False}

    async def _slow_absorb(**kwargs):
        await asyncio.sleep(0.25)   # simulate a slow absorb tier
        ran["done"] = True

    monkeypatch.setattr(bh, "absorb_silent", _slow_absorb)

    async def _drive():
        dd._DD_SIGNAL_TASKS.clear()
        t0 = time.monotonic()
        dd._fire_signal(module="dd_orchestrator", summary="x")
        elapsed = time.monotonic() - t0
        # caller must NOT have blocked on the 0.25s absorb
        assert elapsed < 0.05, f"_fire_signal blocked {elapsed:.3f}s — should be fire-and-forget"
        # task is tracked (strong ref, not GC'd)
        assert len(dd._DD_SIGNAL_TASKS) == 1
        assert not ran["done"]            # hasn't run yet
        await asyncio.gather(*list(dd._DD_SIGNAL_TASKS))
        assert ran["done"] is True        # ran in the background
        assert len(dd._DD_SIGNAL_TASKS) == 0  # done-callback discarded it

    asyncio.run(_drive())


def test_fire_signal_no_loop_is_safe():
    # Called with no running loop (e.g. sync context) — must not raise.
    dd._fire_signal(module="dd_orchestrator", summary="x")  # no asyncio.run wrapper
