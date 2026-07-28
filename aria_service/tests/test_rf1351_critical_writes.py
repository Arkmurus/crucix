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


# R-F3337 — these tests simulated a dropped write by monkeypatching two private
# internals, and both were removed by later rewrites of the write path:
#
#   * `_read_list` — lpush used to read-modify-write the list. R-F1515 made it a
#     single INSERT and R-F2470 replaced the seq counter with an authoritative
#     `SELECT MAX(seq)`. Nothing reads the list on the write path any more, so
#     `monkeypatch.setattr(ss, "_read_list", ...)` raised AttributeError before a
#     single assertion ran.
#   * the global `_get_lock()` — writes no longer serialise on it (R-F1518 moved
#     to a per-list lock held for microseconds), so holding it no longer starves
#     anything and `incr(critical=True)` simply succeeded: "DID NOT RAISE".
#
# The PROPERTY is untouched and still worth pinning: a write that fails must
# raise StateWriteError when critical=True and be swallowed when it is not. So
# the drop is now induced at the layer that actually performs it — the DB
# connection — rather than at a helper whose name is an implementation detail.
# `except Exception -> if critical: raise` (state_store.py:2747, 2965) is the
# real branch under test, and a connection that fails its execute is the honest
# way to reach it.


class _DroppingConn:
    """A connection whose every write fails, as a wedged/broken store does."""

    def __init__(self):
        self.attempts = 0

    async def execute(self, *a, **k):
        self.attempts += 1
        raise RuntimeError("simulated write drop (wedged store)")

    async def commit(self):
        raise RuntimeError("simulated write drop (wedged store)")

    async def close(self):
        return None


# ── the primitive ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_non_critical_returns_default_on_drop(monkeypatch, tmp_path):
    """Default behaviour preserved: a stalled op returns the default, no raise,
    no blackout (R-F1341 contract intact)."""
    await ss.connect(str(tmp_path / "s.db"))
    conn = _DroppingConn()
    monkeypatch.setattr(ss, "_conn", conn)
    out = await asyncio.wait_for(ss.lpush("L", "x"), timeout=3)   # non-critical
    assert out is None                                            # default, no raise
    assert conn.attempts > 0, "the write must have been ATTEMPTED, not skipped"


@pytest.mark.asyncio
async def test_critical_raises_on_drop(monkeypatch, tmp_path):
    """critical=True surfaces the drop as StateWriteError so the caller's
    failure branch re-arms."""
    await ss.connect(str(tmp_path / "s.db"))
    monkeypatch.setattr(ss, "_conn", _DroppingConn())
    with pytest.raises(ss.StateWriteError):
        await asyncio.wait_for(ss.lpush("L", "x", critical=True), timeout=3)


@pytest.mark.asyncio
async def test_critical_incr_raises_on_drop(monkeypatch, tmp_path):
    """incr carries the same contract as lpush.

    R-F3337: this used to hold the global ss._get_lock() to force an
    acquire-timeout. R-F1518 moved writes off that lock, so holding it starved
    nothing and incr simply succeeded — "DID NOT RAISE StateWriteError", a test
    that had stopped exercising its own subject.
    """
    await ss.connect(str(tmp_path / "s.db"))
    # NOT _DroppingConn here, and the reason is worth recording: incr's atomic
    # UPSERT uses the module-global _conn, but on failure it falls back to
    # _run_locked -> _row/_upsert, which go through the ROUTED reader/writer and
    # never touch _conn. Breaking _conn therefore does not break incr — it
    # succeeded via the fallback and, worse, wrote to a store outside tmp_path,
    # leaking a counter into the next test. `_conn is None` is the drop incr
    # itself documents and checks FIRST (state_store.py:2960), so it reaches the
    # critical branch without writing anything anywhere.
    monkeypatch.setattr(ss, "_conn", None)
    with pytest.raises(ss.StateWriteError):
        await asyncio.wait_for(ss.incr("rf3337:critical", critical=True), timeout=3)


@pytest.mark.asyncio
async def test_rf3337_non_critical_incr_swallows_the_same_drop(monkeypatch, tmp_path):
    """The other half of the contract, on the SAME induced fault.

    Both directions on one fault is what makes this a contract rather than two
    unrelated observations: identical broken store, opposite outcomes, decided
    only by `critical`.
    """
    await ss.connect(str(tmp_path / "s.db"))
    monkeypatch.setattr(ss, "_conn", None)
    assert await asyncio.wait_for(ss.incr("rf3337:non-critical"), timeout=3) == 0


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
