"""R-F1351 — silent-drop class: write failures become DISTINGUISHABLE.

R-F1341 made _run_locked return a default on failure so a stalled write can't
blackout the loop — but that silently disabled every caller's except branch
(dropped writes reported as success). For data-integrity writes that's worse
than an exception. R-F1351 adds an opt-in `critical=True` that RAISES
StateWriteError on drop; non-critical callers keep the no-blackout default.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from aria_service.intel import state_store as ss


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setattr(ss, "_OP_TIMEOUT_S", 0.3)
    monkeypatch.setattr(ss, "_ACQUIRE_TIMEOUT_S", 0.3)
    ss._reset_lock()
    yield
    ss._reset_lock()


# ── the primitive ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_non_critical_returns_default_on_drop(monkeypatch, tmp_path):
    """Default behaviour preserved: a stalled op returns the default, no raise,
    no blackout (R-F1341 contract intact)."""
    await ss.connect(str(tmp_path / "s.db"))
    async def _hang(*a, **k):
        await asyncio.sleep(30)
    monkeypatch.setattr(ss, "_read_list", _hang)
    out = await asyncio.wait_for(ss.lpush("L", "x"), timeout=3)   # non-critical
    assert out is None                                            # default, no raise


@pytest.mark.asyncio
async def test_critical_raises_on_drop(monkeypatch, tmp_path):
    """critical=True surfaces the drop as StateWriteError so the caller's
    failure branch re-arms."""
    await ss.connect(str(tmp_path / "s.db"))
    async def _hang(*a, **k):
        await asyncio.sleep(30)
    monkeypatch.setattr(ss, "_read_list", _hang)
    with pytest.raises(ss.StateWriteError):
        await asyncio.wait_for(ss.lpush("L", "x", critical=True), timeout=3)


@pytest.mark.asyncio
async def test_critical_acquire_timeout_raises(monkeypatch, tmp_path):
    await ss.connect(str(tmp_path / "s.db"))
    lock = ss._get_lock()
    await lock.acquire()  # force acquire-timeout for the next caller
    try:
        with pytest.raises(ss.StateWriteError):
            await asyncio.wait_for(ss.incr("c", critical=True), timeout=3)
    finally:
        lock.release()


@pytest.mark.asyncio
async def test_critical_success_returns_normally(tmp_path):
    await ss.connect(str(tmp_path / "s.db"))
    await ss.lpush("L", "a", critical=True)          # healthy → no raise
    assert await ss.lrange("L", 0, -1) == ["a"]
    assert await ss.incr("c", critical=True) == 1
    await ss.close()


# ── capability_gaps adoption (coder no longer blinded by a silent drop) ──


@pytest.mark.asyncio
async def test_capability_gaps_surfaces_dropped_write(monkeypatch, caplog):
    import aria_service.intel.capability_gaps as cg

    async def _raise_lpush(*a, **k):
        raise ss.StateWriteError("lpush: lock-acquire timeout")
    async def _noop(*a, **k):
        return None
    # rs is redis_store in capability_gaps; patch its ops.
    monkeypatch.setattr(cg.rs, "lpush", _raise_lpush)
    monkeypatch.setattr(cg.rs, "ltrim", _noop)
    monkeypatch.setattr(cg.rs, "set", _noop)
    monkeypatch.setattr(cg.rs, "get", _noop)  # dedupe check → not deduped

    import logging
    with caplog.at_level(logging.ERROR, logger="aria.capability_gaps"):
        await cg.record_gap(gap_type="module_bug", detail="x", source="t")
    # The drop is surfaced at ERROR — NOT logged as "recorded".
    assert any("NOT persisted" in r.message for r in caplog.records)
    assert not any("gap recorded" in r.message.lower() for r in caplog.records)
