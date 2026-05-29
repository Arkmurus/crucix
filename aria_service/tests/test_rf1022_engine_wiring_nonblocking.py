"""R-F1022 — engine_wiring helpers must be fire-and-forget and NEVER block.

Root cause of the ~10-min self-coder stalls: `reserve_r_number.py` (a sync CLI)
called `wire_success`, which in a no-event-loop context ran a full neural-memory
brain absorb synchronously via `asyncio.run(...)`, blocking the caller on every
R-number reservation. These tests prove the helpers return immediately in a sync
context even when the underlying brain absorb is slow.
"""
from __future__ import annotations

import time

from aria_service.intel import engine_wiring


def test_wire_success_does_not_block_in_sync_context(monkeypatch):
    """Even if the brain absorb would take seconds, wire_success returns instantly."""
    async def _slow_absorb(**kwargs):
        import asyncio
        await asyncio.sleep(5)  # simulate a slow neural-memory absorb

    # Patch brain_hook.absorb_silent to the slow coroutine.
    import aria_service.intel.brain_hook as bh
    monkeypatch.setattr(bh, "absorb_silent", _slow_absorb, raising=False)

    start = time.monotonic()
    engine_wiring.wire_success("test_engine", "summary", detail="d")
    elapsed = time.monotonic() - start
    assert elapsed < 1.0, f"wire_success blocked for {elapsed:.2f}s (must be fire-and-forget)"


def test_wire_failure_does_not_block_in_sync_context(monkeypatch):
    async def _slow_gap(**kwargs):
        import asyncio
        await asyncio.sleep(5)

    import aria_service.intel.capability_gaps as cg
    monkeypatch.setattr(cg, "record_gap", _slow_gap, raising=False)

    start = time.monotonic()
    engine_wiring.wire_failure("test_engine", "boom", gap_type="engine_failure")
    elapsed = time.monotonic() - start
    assert elapsed < 1.0, f"wire_failure blocked for {elapsed:.2f}s (must be fire-and-forget)"


def test_wire_helpers_never_raise(monkeypatch):
    """A broken brain absorb must not propagate to the caller."""
    async def _boom(**kwargs):
        raise RuntimeError("brain down")

    import aria_service.intel.brain_hook as bh
    import aria_service.intel.capability_gaps as cg
    monkeypatch.setattr(bh, "absorb_silent", _boom, raising=False)
    monkeypatch.setattr(cg, "record_gap", _boom, raising=False)

    # Must not raise even though the underlying coroutine raises.
    engine_wiring.wire_success("m", "s")
    engine_wiring.wire_failure("m", "d")


def test_dispatch_runs_without_event_loop():
    """Sanity: the dispatcher uses the no-loop (thread) path here and returns."""
    ran = {"v": False}

    async def _mark():
        ran["v"] = True

    start = time.monotonic()
    engine_wiring._dispatch_fire_and_forget(_mark)
    assert time.monotonic() - start < 1.0
    # give the daemon thread a moment to complete (best-effort)
    time.sleep(0.3)
    assert ran["v"] is True
