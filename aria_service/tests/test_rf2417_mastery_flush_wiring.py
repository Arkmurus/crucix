"""R-F2417 — make R-F2408 mastery-save coalescing SAFE TO ENABLE by wiring
student.flush_mastery() into (a) the lifespan graceful-shutdown branch and (b) a
per-process periodic tick (the 20s _health_precompute_loop).

The gap R-F2408 left open: with ARIA_MASTERY_COALESCE_SAVE=1, a QUIET period (no
further update_mastery, no report read) can leave the last coalesced whole-cache
update unpersisted, and a graceful restart would strand it. These tests drive the
SAME public entry point the wiring calls — student.flush_mastery() — and prove:

  1. flag ON  -> a coalesced (deferred) update is persisted by flush_mastery(),
                 exactly what the shutdown branch + periodic tick invoke.
  2. flag OFF -> flush_mastery() is a NO-OP over the inline path: N updates == N
                 saves and flush adds ZERO extra writes (byte-identical to today).
  3. flush_mastery() on a clean cache returns False and never raises (the periodic
     tick fires this every 20s even when nothing learned).
  4. the wiring is actually present in main.py (shutdown branch + periodic loop),
     so a future edit that drops the wire fails CI, not silently in production.
"""
from __future__ import annotations

import pathlib

import pytest

from aria_service.intel import student as _st


async def _reset_mastery_state(monkeypatch, coalesce: bool):
    """Clean in-memory cache, _save_mastery replaced by a counter (no sqlite),
    mirroring test_rf2408's harness so the two suites agree on semantics."""
    monkeypatch.setattr(_st, "_mastery_cache", {})
    monkeypatch.setattr(_st, "_mastery_dirty", False)
    monkeypatch.setattr(_st, "_mastery_last_save", 0.0)
    monkeypatch.setattr(_st, "_mastery_save_lock", None)
    monkeypatch.setattr(_st, "_MASTERY_SAVE_COALESCE", coalesce)
    monkeypatch.setattr(_st, "_MASTERY_FLUSH_INTERVAL_S", 1000.0)  # never elapses mid-test

    saves = {"n": 0}

    async def _fake_save():
        if not _st._mastery_dirty:
            return
        saves["n"] += 1
        _st._mastery_dirty = False

    monkeypatch.setattr(_st, "_save_mastery", _fake_save)

    async def _fake_load():
        return _st._mastery_cache

    monkeypatch.setattr(_st, "_load_mastery", _fake_load)
    return saves


@pytest.mark.asyncio
async def test_shutdown_or_tick_flush_persists_coalesced_update(monkeypatch):
    """flag ON: a burst coalesces to 1 write with a pending (dirty) tail; the
    flush_mastery() that the lifespan shutdown branch AND the periodic tick call
    persists that deferred tail — proving a graceful restart / quiet period no
    longer strands the last learning signal."""
    saves = await _reset_mastery_state(monkeypatch, coalesce=True)
    await _st.update_mastery(["compliance"], correct=True)   # 1st write
    await _st.update_mastery(["compliance"], correct=True)   # coalesced -> dirty
    assert saves["n"] == 1 and _st._mastery_dirty is True, "precondition: 1 write + pending tail"

    # This is EXACTLY what main.lifespan shutdown + _health_precompute_loop invoke.
    wrote = await _st.flush_mastery()
    assert wrote is True, "flush must persist the deferred coalesced write"
    assert saves["n"] == 2, "the pending tail is now durable"
    assert _st._mastery_dirty is False, "nothing left pending after flush"


@pytest.mark.asyncio
async def test_flag_off_flush_is_byte_identical_noop(monkeypatch):
    """flag OFF (default): the inline path already saved every update, so
    flush_mastery() adds ZERO extra writes — wiring it in is byte-identical to
    today until ARIA_MASTERY_COALESCE_SAVE=1."""
    saves = await _reset_mastery_state(monkeypatch, coalesce=False)
    for _ in range(4):
        await _st.update_mastery(["procurement"], correct=True)
    assert saves["n"] == 4, "OFF inline path saves every update"
    assert _st._mastery_dirty is False, "OFF path leaves nothing dirty"

    # The shutdown/periodic wiring calls this — it must not write again.
    wrote = await _st.flush_mastery()
    assert wrote is True, "OFF path returns True (inline no-op save attempted)"
    assert saves["n"] == 4, "flush adds NO extra write when flag OFF (byte-identical)"
    assert _st._mastery_dirty is False


@pytest.mark.asyncio
async def test_periodic_flush_on_clean_cache_no_write_no_raise(monkeypatch):
    """The periodic tick fires flush_mastery() every 20s even when ARIA learned
    nothing since the last flush — it must be a cheap no-op that never raises."""
    saves = await _reset_mastery_state(monkeypatch, coalesce=True)
    wrote = await _st.flush_mastery()           # nothing dirty
    assert wrote is False, "clean cache -> no write"
    assert saves["n"] == 0
    # idempotent second tick
    assert await _st.flush_mastery() is False
    assert saves["n"] == 0


def test_wiring_present_in_main_shutdown_and_periodic_loop():
    """Guard the wire itself: main.py must call student.flush_mastery() in BOTH
    the lifespan shutdown branch and the periodic _health_precompute_loop. A future
    edit that removes either wire re-opens the R-F2408 quiet-period strand gap, so
    fail CI here rather than silently in production."""
    main_src = (
        pathlib.Path(__file__).resolve().parents[1] / "main.py"
    ).read_text(encoding="utf-8")
    n = main_src.count("student.flush_mastery()")
    assert n >= 2, (
        f"expected student.flush_mastery() wired in >=2 sites "
        f"(shutdown + periodic tick), found {n}"
    )
    # shutdown branch is after the `yield`; periodic tick is inside the health loop.
    assert "yield" in main_src
    tail = main_src.split("\n    yield\n", 1)[-1]
    assert "student.flush_mastery()" in tail, "no flush_mastery() in the shutdown branch"
    assert "_health_precompute_loop" in main_src
