"""R-F1334 — wedge diagnostics: state_store lock holder tracking + dump refactor.

Why this exists (2026-06-04 incident): every /api/aria/* endpoint hung 170s+
while /health/live answered in 0.5s. The R-F1333 blackout dump could show
asyncio task stacks and that state_store._lock was held — but asyncio.Lock
cannot name its HOLDER, so the culprit stayed invisible. R-F1334 adds:
  1. _DiagLock — records holder task name + acquire call stack + hold start
  2. state_store.get_lock_diagnostics() — thread-safe snapshot
  3. release()-time WARNING when held > ARIA_STATE_LOCK_HOLD_WARN_S
  4. self_restart._write_wedge_dump() — extracted, testable, prints holder

Unit tests prove each contract; the capability test drives the REAL dump
path with a genuinely held lock + genuinely stuck task and asserts the dump
names both — unconditionally (the R-F1333 test was vacuously green).
"""
import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from aria_service.intel import state_store
from aria_service.intel.self_restart import _write_wedge_dump


@pytest.fixture(autouse=True)
def _fresh_lock():
    """Each test gets a fresh _DiagLock bound to its own loop."""
    state_store._reset_lock()
    yield
    state_store._reset_lock()


# ── Unit: _DiagLock holder tracking ──────────────────────────────────────


@pytest.mark.asyncio
async def test_diaglock_records_holder_while_held():
    lock = state_store._get_lock()
    assert isinstance(lock, state_store._DiagLock)

    async with lock:
        diag = state_store.get_lock_diagnostics()
        assert diag["initialised"] is True
        assert diag["locked"] is True
        # Holder is THIS test's task
        current = asyncio.current_task()
        assert diag["holder_task"] == current.get_name()
        assert diag["held_for_s"] is not None and diag["held_for_s"] >= 0
        # Acquire stack points at this test file
        assert any("test_rf1334_wedge_diagnostics.py" in line for line in diag["holder_stack"])


@pytest.mark.asyncio
async def test_diaglock_clears_holder_on_release():
    lock = state_store._get_lock()
    async with lock:
        pass
    diag = state_store.get_lock_diagnostics()
    assert diag["locked"] is False
    assert "holder_task" not in diag
    assert lock.holder_task is None
    assert lock.acquired_at is None


@pytest.mark.asyncio
async def test_diaglock_reports_waiters():
    lock = state_store._get_lock()

    async def _contender():
        async with lock:
            pass

    async with lock:
        waiter = asyncio.create_task(_contender())
        await asyncio.sleep(0)  # let the contender block on acquire
        diag = state_store.get_lock_diagnostics()
        assert diag["waiters"] == 1
    await waiter
    assert state_store.get_lock_diagnostics()["waiters"] == 0


@pytest.mark.asyncio
async def test_diaglock_warns_on_long_hold(monkeypatch, caplog):
    monkeypatch.setattr(state_store, "_LOCK_HOLD_WARN_S", 0.0)
    lock = state_store._get_lock()
    with caplog.at_level(logging.WARNING, logger="aria.state_store"):
        async with lock:
            await asyncio.sleep(0.01)
    assert any("R-F1334" in r.message and "state_store lock held" in r.message
               for r in caplog.records)


@pytest.mark.asyncio
async def test_diaglock_no_warn_on_short_hold(monkeypatch, caplog):
    monkeypatch.setattr(state_store, "_LOCK_HOLD_WARN_S", 60.0)
    lock = state_store._get_lock()
    with caplog.at_level(logging.WARNING, logger="aria.state_store"):
        async with lock:
            pass
    assert not any("state_store lock held" in r.message for r in caplog.records)


def test_get_lock_diagnostics_uninitialised():
    state_store._reset_lock()
    diag = state_store.get_lock_diagnostics()
    assert diag == {"initialised": False, "locked": False}


# ── Capability: the real wedge dump names the culprit ────────────────────


@pytest.mark.asyncio
async def test_wedge_dump_names_lock_holder_and_stuck_task(tmp_path):
    """Drive the REAL _write_wedge_dump with (a) the state_store lock held
    by a named task and (b) a coroutine stuck on a never-resolved future —
    the exact starvation shape of the 2026-06-04 incident. The dump must
    name both, so the next production wedge identifies its culprit."""
    lock = state_store._get_lock()
    loop = asyncio.get_running_loop()
    stuck_fut: asyncio.Future = loop.create_future()
    holder_acquired = asyncio.Event()

    async def _hold_lock_forever():
        async with lock:
            holder_acquired.set()
            await stuck_fut  # holds the lock while stuck — worst case

    async def _stuck_waiter():
        await stuck_fut

    holder = asyncio.create_task(_hold_lock_forever(), name="rf1334_lock_holder")
    waiter = asyncio.create_task(_stuck_waiter(), name="rf1334_stuck_waiter")
    await asyncio.wait_for(holder_acquired.wait(), timeout=5)
    await asyncio.sleep(0)  # ensure _stuck_waiter reached its await

    try:
        wedge_path = _write_wedge_dump("cap_test_agent", 300.5, wedge_dir=str(tmp_path))
        assert wedge_path is not None, "dump must be written"
        with open(wedge_path, "r", encoding="utf-8") as f:
            content = f.read()

        # 1. Lock section names the HOLDER task and its acquire stack
        assert "holder_task=rf1334_lock_holder" in content
        assert "_hold_lock_forever" in content  # acquire-stack frame
        assert "held_for_s=" in content

        # 2. Task section names the stuck coroutine + its suspension point
        assert "rf1334_stuck_waiter" in content
        assert "_stuck_waiter" in content

        # 3. Sections are intact
        assert "=== asyncio task stacks ===" in content
        assert "=== state_store lock status ===" in content
        assert "=== end blackout stack dump ===" in content
    finally:
        stuck_fut.set_result(None)
        await asyncio.gather(holder, waiter, return_exceptions=True)
