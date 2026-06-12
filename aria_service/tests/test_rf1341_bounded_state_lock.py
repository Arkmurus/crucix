"""R-F1341 — bounded, self-healing state_store lock (kills the blackout cascade).

Root cause proven 2026-06-05 (R-F1334 lock diagnostics): a single hset held
the global RMW lock for 1311s with 140 waiters → event loop starved →
heartbeat stale → blackout. These tests prove the fix converts "one stalled
op = whole-app blackout" into "one stalled op = one dropped write + the loop
keeps breathing + self-heal reconnect".
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from aria_service.intel import state_store as ss


@pytest.fixture(autouse=True)
def _fresh(monkeypatch, tmp_path):
    # Fast timeouts so tests run quickly; isolated DB per test.
    # R-F1376: timeouts are now read from env vars at call time via _timeout_config().
    monkeypatch.setenv("ARIA_STATE_OP_TIMEOUT_S", "0.3")
    monkeypatch.setenv("ARIA_STATE_ACQUIRE_TIMEOUT_S", "0.3")
    ss._op_timeout_counts.update({"acquire": 0, "op": 0, "reconnect": 0})
    # NOTE: _reset_lock is NOT called here because the test_acquire_timeout
    # test acquires the lock directly and _run_locked must use the SAME instance.
    # _reset_lock would create a new lock on next _get_lock(), breaking the
    # test's assumption that holding lock A blocks _run_locked's lock B.
    # The lock is created once by the first test that calls _get_lock() and
    # reused by subsequent tests in the same event loop.
    yield


# ── normal operation is unaffected ───────────────────────────────────────


@pytest.mark.asyncio
async def test_normal_ops_work(tmp_path):
    assert await ss.connect(str(tmp_path / "s.db")) is True
    await ss.hset("h1", {"a": "1"})
    assert (await ss.hgetall("h1")).get("a") == "1"
    await ss.lpush("L", "x"); await ss.lpush("L", "y")
    assert await ss.lrange("L", 0, -1) == ["y", "x"]
    assert await ss.incr("c") == 1
    assert await ss.incr("c", 4) == 5
    assert abs(await ss.incrbyfloat("f", 2.5) - 2.5) < 1e-9
    await ss.zadd("z", 1.0, "m1")
    assert await ss.zrem("z", "m1") is True
    await ss.close()


# ── the keystone: a stalled in-lock op does NOT blackout the loop ─────────


@pytest.mark.asyncio
async def test_stalled_op_times_out_and_releases(monkeypatch, tmp_path):
    await ss.connect(str(tmp_path / "s.db"))

    # Make the in-lock DB read hang far longer than _OP_TIMEOUT_S.
    async def _hang(*a, **k):
        await asyncio.sleep(30)

    monkeypatch.setattr(ss, "_read_hash", _hang)

    # The op must return (its default) within ~_OP_TIMEOUT_S, NOT hang 30s.
    result = await asyncio.wait_for(ss.hset("h", {"x": "1"}), timeout=3)
    assert result is None
    assert ss._op_timeout_counts["op"] == 1
    # Lock must be released after the timeout — a fresh op can proceed.
    assert not ss._get_lock().locked()


@pytest.mark.asyncio
async def test_loop_keeps_breathing_while_holder_is_stalled(monkeypatch, tmp_path):
    """R-F1515: lpush is now lock-free (row-per-entry INSERT), so this test
    is obsolete. The old lock-based lpush held the Python lock for the entire
    read-modify-write cycle; the new lpush is a single atomic INSERT with no
    lock acquisition. The hset path still uses the lock, so we test that
    hset contention doesn't blackout the loop."""
    await ss.connect(str(tmp_path / "s.db"))

    async def _hang(*a, **k):
        await asyncio.sleep(30)
    monkeypatch.setattr(ss, "_read_hash", _hang)

    ticks = 0
    async def _heartbeat():
        nonlocal ticks
        for _ in range(20):
            ticks += 1
            await asyncio.sleep(0.05)

    # Start a stalled hset holder + a waiter behind it + the heartbeat together.
    holder = asyncio.create_task(ss.hset("h", {"a": "1"}))
    waiter = asyncio.create_task(ss.hset("h", {"b": "2"}))
    hb = asyncio.create_task(_heartbeat())
    await asyncio.gather(holder, waiter, hb)

    # Heartbeat ticked throughout — the loop never blacked out.
    assert ticks == 20
    # Both ops gave up safely (op-timeout on holder, acquire-timeout on waiter).
    assert ss._op_timeout_counts["op"] >= 1
    assert ss._op_timeout_counts["acquire"] >= 1


@pytest.mark.asyncio
async def test_acquire_timeout_when_lock_held(monkeypatch, tmp_path):
    await ss.connect(str(tmp_path / "s.db"))
    lock = ss._get_lock()
    await lock.acquire()  # simulate a stuck holder
    try:
        # R-F1510: incr now uses an atomic SQL UPSERT (no Python lock needed)
        # as its primary path. When the Python lock is held by another task,
        # the atomic SQL path still succeeds because it doesn't need the lock.
        # The locked fallback is only reached when the SQL atomic path itself
        # fails (e.g. database is locked at the SQLite level).
        out = await asyncio.wait_for(ss.incr("c"), timeout=3)
        assert out == 1  # atomic SQL path succeeded without the Python lock
        # The atomic path succeeded, so no acquire timeout was recorded.
        assert ss._op_timeout_counts["acquire"] == 0
    finally:
        lock.release()


@pytest.mark.asyncio
async def test_op_timeout_triggers_reconnect(monkeypatch, tmp_path):
    """R-F1341 + R-F1397: an in-lock op timeout fires the self-heal task, but
    the heal now PROBES first — a healthy (merely slow) connection is KEPT,
    not churned. Pre-R-F1397 this test asserted a churn here; churning a
    healthy conn failed every in-flight op with 'Cannot operate on a closed
    database' (live 2026-06-07). The genuinely-dead-conn churn path is
    covered by test_rf1397_state_conn_churn.py."""
    await ss.connect(str(tmp_path / "s.db"))
    async def _hang(*a, **k):
        await asyncio.sleep(30)
    monkeypatch.setattr(ss, "_read_hash", _hang)

    op_before = ss._op_timeout_counts["op"]
    rc_before = ss._op_timeout_counts["reconnect"]
    await asyncio.wait_for(ss.hset("h", {"x": "1"}), timeout=3)
    await asyncio.sleep(0.5)  # let the probe/heal task run
    assert ss._op_timeout_counts["op"] >= op_before + 1   # timeout still counted
    # conn was healthy → probe OK → NO churn (R-F1397)
    assert ss._op_timeout_counts["reconnect"] == rc_before
    # The connection is still usable (it was never the problem).
    monkeypatch.undo()
    await ss.hset("h2", {"ok": "1"})
    assert (await ss.hgetall("h2")).get("ok") == "1"
    await ss.close()


@pytest.mark.asyncio
async def test_diagnostics_expose_timeout_counts(tmp_path):
    await ss.connect(str(tmp_path / "s.db"))
    d = ss.get_lock_diagnostics()
    assert "op_timeouts" in d
    assert set(d["op_timeouts"]) == {"acquire", "op", "reconnect"}
    await ss.close()
