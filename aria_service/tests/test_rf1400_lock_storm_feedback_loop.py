"""R-F1400 — break the state_store lock-storm feedback loop.

The 2026-06-07 ~13:12Z death spiral (live, ~2887 waiters): state_store
logged every lock-acquire timeout at WARNING; error_log_handler turned
EVERY such warning into a new lock-guarded rs.incr task; each of those
timed out 20s later and logged another warning → self-amplifying loop the
queue never drained from. Three breakers shipped:

  1. error_log_handler._SKIP_SUBSTRINGS now skips state_store contention/
     self-heal telemetry (no record_error task, no incr per storm line).
  2. _increment_error_count batches: at most one rs.incr flush per 5s
     window instead of one task per warning.
  3. state_store._run_locked sheds new entrants when the waiter queue is
     over cap, and rate-limits contention warnings to one per 10s.

Capability tests drive the REAL paths: the actual logging handler with the
actual storm message, and the real _run_locked entry.
"""
from __future__ import annotations

import asyncio
import logging

import pytest

from aria_service.intel import error_log_handler as elh
from aria_service.intel import state_store


STORM_WARNING = (
    "[R-F1376] state_store incr: lock-acquire timed out after 20s "
    "(attempt 1/2, holder=None held_for=Nones waiters=2887) — retrying with backoff"
)
SELF_HEAL_ERROR = (
    "[R-F1341] state_store connection reset (self-heal) after in-lock op timeout"
)
CLOSED_DB_WARNING = "write failed: Cannot operate on a closed database"


# ── 1. The feedback loop is severed at the handler ─────────────────────────

def _emit_through_handler(monkeypatch, message: str, level=logging.WARNING) -> dict:
    """Drive the REAL ErrorLedgerHandler.emit with a storm record and
    capture whether it would have spawned record_error/incr tasks."""
    spawned = {"record_error": 0, "incr": 0}

    async def _noop():
        return None

    def _fake_create_task(coro):
        name = getattr(coro, "__qualname__", "") or str(coro)
        if "record_error" in name:
            spawned["record_error"] += 1
        else:
            spawned["incr"] += 1
        coro.close()
        return None

    class _FakeLoop:
        create_task = staticmethod(_fake_create_task)

    monkeypatch.setattr(asyncio, "get_running_loop", lambda: _FakeLoop())

    handler = elh.ErrorLedgerHandler()
    record = logging.LogRecord(
        name="aria.state_store", level=level, pathname=__file__,
        lineno=1, msg=message, args=(), exc_info=None,
    )
    handler.emit(record)
    return spawned


def test_storm_warning_spawns_no_tasks(monkeypatch):
    """THE capability assertion: the exact live storm line must not create
    record_error or incr tasks (pre-fix it created BOTH per line)."""
    spawned = _emit_through_handler(monkeypatch, STORM_WARNING)
    assert spawned == {"record_error": 0, "incr": 0}, spawned


def test_self_heal_and_closed_db_lines_skipped(monkeypatch):
    assert _emit_through_handler(monkeypatch, SELF_HEAL_ERROR, logging.ERROR) == {
        "record_error": 0, "incr": 0,
    }
    assert _emit_through_handler(monkeypatch, CLOSED_DB_WARNING) == {
        "record_error": 0, "incr": 0,
    }


def test_genuine_warning_still_reaches_ledger(monkeypatch):
    """The skip additions must not silence real defects (§21a)."""
    spawned = _emit_through_handler(
        monkeypatch, "document extraction failed: KeyError 'pages'"
    )
    assert spawned["record_error"] == 1


# ── 2. Error-count incr is batched, not per-warning ────────────────────────

def test_increment_error_count_batches(monkeypatch):
    created = []

    def _fake_create_task(coro):
        created.append(coro)
        coro.close()

    class _FakeLoop:
        create_task = staticmethod(_fake_create_task)

    # Reset module batch state
    monkeypatch.setattr(elh, "_pending_count", 0)
    monkeypatch.setattr(elh, "_last_flush_at", 0.0)

    import time as _time
    base = _time.monotonic()

    # 100 warnings inside one flush window → at most 1 incr task
    for _ in range(100):
        elh._increment_error_count(_FakeLoop())
    assert len(created) == 1, (
        f"expected 1 batched flush, got {len(created)} — per-warning incr is "
        "the storm fuel"
    )


# ── 3. Waiter shed + rate-limited warnings in _run_locked ──────────────────

class _FakeLockManyWaiters:
    """Lock whose waiter queue is already over the shed cap."""
    _waiters = [object()] * 600

    async def acquire(self):  # pragma: no cover — shed must fire first
        raise AssertionError("acquire must not be called when shedding")

    def release(self):  # pragma: no cover
        pass

    def locked(self):
        return True


def test_run_locked_sheds_over_cap(monkeypatch):
    monkeypatch.setattr(state_store, "_get_lock", lambda: _FakeLockManyWaiters())

    async def _factory():  # pragma: no cover — never reached
        return 42

    result = asyncio.run(
        state_store._run_locked("incr", _factory, default=None)
    )
    assert result is None  # shed → default, immediately, no 20s wait


def test_run_locked_shed_critical_raises(monkeypatch):
    monkeypatch.setattr(state_store, "_get_lock", lambda: _FakeLockManyWaiters())

    async def _factory():  # pragma: no cover
        return 42

    with pytest.raises(state_store.StateWriteError):
        asyncio.run(
            state_store._run_locked("hset", _factory, default=None, critical=True)
        )


def test_warn_rate_limited_one_warning_per_window(monkeypatch, caplog):
    monkeypatch.setattr(state_store, "_last_log_at", {})
    with caplog.at_level(logging.DEBUG, logger="aria.state_store"):
        for i in range(50):
            state_store._warn_rate_limited("storm line %d", i)
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    debugs = [r for r in caplog.records if r.levelno == logging.DEBUG]
    assert len(warnings) == 1, f"expected 1 WARNING per window, got {len(warnings)}"
    assert len(debugs) == 49


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
