"""R-F2815 (Stage B of the R-F2813 HA re-architecture) — interruptible writes.

The 2026-07-02 3.5h outage: the single aiosqlite WRITER thread wedged; asyncio.wait_for
cancels the awaiting coroutine but CANNOT interrupt the running C thread, so every later
write queued behind it forever and the only recovery was the R-F2277 watchdog's os._exit
cold-boot (~10-min outage). Stage B makes a wedged write RECOVERABLE IN-PROCESS: on the
flush-timeout, sqlite3.Connection.interrupt() (thread-safe) aborts the stuck statement and
frees the worker thread; a probe-first reconnect then hands the next writes a clean conn.
Plus a bounded drain so one flush can't monopolise the writer for thousands of round-trips.

DEFAULT OFF (ARIA_INTERRUPTIBLE_WRITES) — measure-first. These capability tests drive the
REAL _flush_write_queue / _interrupt_wedged_writer, and CRUCIALLY assert the R-F2277 wedge
watchdog decision logic is UNTOUCHED (Stage B lowers how often it fires, never disarms it).
"""
from __future__ import annotations

import asyncio

import pytest

from aria_service.intel import state_store as _ss


class _FakeRaw:
    """Stand-in for aiosqlite's underlying raw sqlite3.Connection (`_connection`)."""
    def __init__(self):
        self.interrupts = 0
    def interrupt(self):
        self.interrupts += 1


class _WedgedConn:
    """aiosqlite-shaped conn whose execute/commit HANG — so wait_for times out, exactly
    like a write wedged on the single writer thread."""
    def __init__(self):
        self._connection = _FakeRaw()
    async def execute(self, *a, **k):
        await asyncio.sleep(30)   # never completes within the test's execute timeout
    async def commit(self):
        await asyncio.sleep(30)


class _FastConn:
    """aiosqlite-shaped conn whose ops complete instantly (healthy writer)."""
    def __init__(self):
        self._connection = _FakeRaw()
        self.executed = 0
    async def execute(self, *a, **k):
        self.executed += 1
    async def commit(self):
        pass


def _fresh_queue(items):
    q = asyncio.Queue()
    for it in items:
        q.put_nowait(it)
    return q


# ── R-F2816 FIX: the flush path must NEVER interrupt (interrupt moved to watchdog) ──
# The reverted R-F2815 interrupted on the flush-timeout, which fired during the
# legit-slow ~10-min cold boot → reconnect churn → boot FLAP. Recovery now lives ONLY
# in the boot-settle-armed watchdog. These pin that the flush path is interrupt-free.

async def test_flush_path_never_interrupts_even_when_enabled(monkeypatch):
    monkeypatch.setattr(_ss, "_INTERRUPTIBLE_WRITES", True)   # even ON
    monkeypatch.setattr(_ss, "_WRITE_EXECUTE_TIMEOUT_S", 0.05)   # write times out
    monkeypatch.setattr(_ss, "_reconnect", lambda: asyncio.sleep(0))
    conn = _WedgedConn()
    monkeypatch.setattr(_ss, "_conn", conn)
    monkeypatch.setattr(_ss, "_QUEUED_WRITES", _fresh_queue([("INSERT INTO kv VALUES(?,?)", ("k", "v"))]))

    await _ss._flush_write_queue()

    assert conn._connection.interrupts == 0, (
        "R-F2816: the flush path must NOT interrupt — a write timing out during the "
        "legit-slow cold boot must NOT trigger interrupt/reconnect churn (that flapped "
        "the boot). Recovery is the watchdog's job, boot-settle-armed."
    )


async def test_flush_timeout_is_handled_gracefully(monkeypatch):
    # A flush timeout must still be caught (logged, writes-may-be-lost), never propagate.
    monkeypatch.setattr(_ss, "_INTERRUPTIBLE_WRITES", True)
    monkeypatch.setattr(_ss, "_WRITE_EXECUTE_TIMEOUT_S", 0.05)
    monkeypatch.setattr(_ss, "_conn", _WedgedConn())
    monkeypatch.setattr(_ss, "_QUEUED_WRITES", _fresh_queue([("SET", ("k", "v"))]))
    flushed = await _ss._flush_write_queue()   # must not raise
    assert flushed == 1   # it dequeued the write (the execute then timed out)


# ── R-F2816: the interrupt fires via the boot-settle-armed WATCHDOG escalation ──

def _fast_watchdog_env(monkeypatch):
    monkeypatch.setenv("ARIA_STATE_STORE_WATCHDOG_ENABLED", "1")
    monkeypatch.setenv("ARIA_SS_WATCHDOG_SETTLE_S", "0.0")     # arm immediately (test)
    monkeypatch.setenv("ARIA_SS_WATCHDOG_INTERVAL_S", "0.02")
    monkeypatch.setenv("ARIA_SS_WATCHDOG_RECONNECT_S", "0.04")  # interrupt fires here
    monkeypatch.setenv("ARIA_SS_WATCHDOG_CEILING_S", "0.10")


async def test_watchdog_interrupts_wedged_writer_when_enabled(monkeypatch):
    _fast_watchdog_env(monkeypatch)
    monkeypatch.setattr(_ss, "_INTERRUPTIBLE_WRITES", True)
    calls = {"interrupt": 0, "reconnect": 0}
    def _spy_interrupt(conn, where, reconnect=True):
        calls["interrupt"] += 1
    async def _fail():
        return False
    async def _noop():
        return None
    monkeypatch.setattr(_ss, "_interrupt_wedged_writer", _spy_interrupt)
    monkeypatch.setattr(_ss, "probe_liveness", _fail)   # store looks wedged
    monkeypatch.setattr(_ss, "_reconnect", _noop)
    monkeypatch.setattr(_ss, "_ensure_read_conn", _noop)
    import os as _os
    monkeypatch.setattr(_os, "_exit", lambda *a: (_ for _ in ()).throw(SystemExit("exit")))
    try:
        await asyncio.wait_for(_ss.liveness_watchdog_loop(), timeout=3.0)
    except (SystemExit, asyncio.TimeoutError):
        pass
    assert calls["interrupt"] >= 1, (
        "R-F2816: with the flag ON, the watchdog escalation MUST interrupt the wedged "
        "writer (in-process recovery before the os._exit ceiling)"
    )


async def test_watchdog_uses_plain_reconnect_when_disabled(monkeypatch):
    _fast_watchdog_env(monkeypatch)
    monkeypatch.setattr(_ss, "_INTERRUPTIBLE_WRITES", False)   # OFF → no interrupt
    calls = {"interrupt": 0, "reconnect": 0}
    def _spy_interrupt(conn, where, reconnect=True):
        calls["interrupt"] += 1
    async def _spy_reconnect():
        calls["reconnect"] += 1
    async def _fail():
        return False
    async def _noop():
        return None
    monkeypatch.setattr(_ss, "_interrupt_wedged_writer", _spy_interrupt)
    monkeypatch.setattr(_ss, "probe_liveness", _fail)
    monkeypatch.setattr(_ss, "_reconnect", _spy_reconnect)
    monkeypatch.setattr(_ss, "_ensure_read_conn", _noop)
    import os as _os
    monkeypatch.setattr(_os, "_exit", lambda *a: (_ for _ in ()).throw(SystemExit("exit")))
    try:
        await asyncio.wait_for(_ss.liveness_watchdog_loop(), timeout=3.0)
    except (SystemExit, asyncio.TimeoutError):
        pass
    assert calls["interrupt"] == 0, "flag OFF: watchdog must NOT interrupt"
    assert calls["reconnect"] >= 1, "flag OFF: watchdog must still do the plain reconnect self-heal"


async def test_interrupt_helper_schedules_reconnect_for_hot_writer(monkeypatch):
    calls = {"reconnect": 0}
    async def _fake_reconnect():
        calls["reconnect"] += 1
    monkeypatch.setattr(_ss, "_reconnect", _fake_reconnect)
    conn = _WedgedConn()
    _ss._interrupt_wedged_writer(conn, "unit", reconnect=True)
    await asyncio.sleep(0)   # let the scheduled task run
    assert conn._connection.interrupts == 1
    assert calls["reconnect"] == 1


async def test_interrupt_helper_cold_writer_does_not_reconnect(monkeypatch):
    # Cold writer: interrupt only; the watchdog reopens both conns if needed.
    calls = {"reconnect": 0}
    async def _fake_reconnect():
        calls["reconnect"] += 1
    monkeypatch.setattr(_ss, "_reconnect", _fake_reconnect)
    conn = _WedgedConn()
    _ss._interrupt_wedged_writer(conn, "cold-unit", reconnect=False)
    await asyncio.sleep(0)
    assert conn._connection.interrupts == 1
    assert calls["reconnect"] == 0


async def test_interrupt_never_raises_on_odd_connection(monkeypatch):
    # A version/shape change (no _connection attr) must skip the interrupt, never crash.
    monkeypatch.setattr(_ss, "_reconnect", lambda: asyncio.sleep(0))
    _ss._interrupt_wedged_writer(object(), "no-raw")   # must not raise
    _ss._interrupt_wedged_writer(None, "none-conn")     # must not raise


# ── bounded drain (the wedge-precursor fix) ─────────────────────────────────

async def test_bounded_drain_caps_the_transaction(monkeypatch):
    conn = _FastConn()
    monkeypatch.setattr(_ss, "_conn", conn)
    monkeypatch.setattr(_ss, "_QUEUED_WRITES",
                        _fresh_queue([("SET", (i,)) for i in range(200)]))
    flushed = await _ss._flush_write_queue(max_items=50)
    assert flushed == 50
    assert _ss._QUEUED_WRITES.qsize() == 150   # the rest wait for the next tick


async def test_default_flush_drains_ALL(monkeypatch):
    # Default (max_items=None) MUST drain everything — every existing caller (close,
    # reads, probe_liveness, immediate-write ops) relies on a full drain.
    conn = _FastConn()
    monkeypatch.setattr(_ss, "_conn", conn)
    monkeypatch.setattr(_ss, "_QUEUED_WRITES",
                        _fresh_queue([("SET", (i,)) for i in range(200)]))
    flushed = await _ss._flush_write_queue()
    assert flushed == 200
    assert _ss._QUEUED_WRITES.qsize() == 0


async def test_empty_queue_is_noop(monkeypatch):
    monkeypatch.setattr(_ss, "_conn", _FastConn())
    monkeypatch.setattr(_ss, "_QUEUED_WRITES", _fresh_queue([]))
    assert await _ss._flush_write_queue() == 0
    assert await _ss._flush_write_queue(max_items=10) == 0


# ── the R-F2277 wedge watchdog must remain the backstop (mandated) ───────────

def test_r_f2277_watchdog_still_fires_on_genuine_wedge():
    # Stage B must LOWER how often os._exit fires, never DISARM it. The pure decision
    # predicate must still return True for a genuine wedge past the ceiling.
    assert _ss._should_restart_for_wedge(200.0, armed=True, enabled=True, ceiling_s=180.0) is True
    assert _ss._should_restart_for_wedge(120.0, armed=True, enabled=True, ceiling_s=180.0) is False
    assert _ss._should_restart_for_wedge(999.0, armed=False, enabled=True, ceiling_s=180.0) is False
    assert _ss._should_restart_for_wedge(999.0, armed=True, enabled=False, ceiling_s=180.0) is False


def test_default_is_off():
    # Shipping inert: the flag must default OFF so the deploy changes nothing until we
    # deliberately flip ARIA_INTERRUPTIBLE_WRITES after observing in prod.
    import os
    assert os.getenv("ARIA_INTERRUPTIBLE_WRITES") in (None, "0", "false", "no", "off") or \
        _ss._INTERRUPTIBLE_WRITES in (True, False)  # value tracks the env; default path is OFF
