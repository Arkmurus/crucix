"""R-F3363 — the one error that resets Phase-A gate #3 logged nothing you could diagnose.

THE SITUATION. Gate #3 requires 7 consecutive days with no app ERROR. Measured
live 2026-07-28, the single event holding it open was:

    state_store: flush failed (3 writes): database is locked
        (aria_service/intel/state_store.py::_flush_write_queue)

It had occurred ONCE, 21-23h earlier, and did not recur while the streak climbed
back past 23h. So it is rare — and rare is exactly what makes it expensive: each
occurrence restarts a 7-day clock, and by the time anyone looks, the state that
caused it is gone.

WHY NOT JUST RAISE THE TIMEOUT. `busy_timeout` is already 120_000ms on both the
hot and cold connections (state_store.py:899, :612), and the hot and cold stores
are SEPARATE FILES (aria_state.db vs aria_knowledge_store.db), so they do not
contend with each other. A lock that survives 120s is not a tuning problem, and
CLAUDE.md §1 forbids reaching for the timeout knob anyway.

WHAT THIS FIX DOES *NOT* DO. It does not claim a root cause. One unreproduced
event is not evidence for a mechanism, and naming one would be exactly the
fabricated diagnosis §22 exists to prevent.

WHAT IT DOES. `state_store.get_lock_diagnostics()` (R-F1334) already reports the
RMW lock holder, its acquire stack and wait counts, and is safe to call from any
thread — the blackout wedge dumper already uses it. The flush failure handler
never did, so the most diagnostically valuable moment in the system threw its
evidence away. Now a flush failure captures that snapshot alongside the error,
so the NEXT occurrence arrives with the holder identity attached instead of
being another mystery.

Instrument what you cannot yet reproduce; do not guess it.
"""
from __future__ import annotations

import asyncio
import logging
from unittest.mock import patch

import pytest

from aria_service.intel import state_store as ss


def _run(coro):
    return asyncio.run(coro)


def test_lock_diagnostics_helper_exists_and_is_safe_to_call():
    """§3b — verify the callee before calling it, and it must never raise."""
    assert callable(ss.get_lock_diagnostics)
    out = ss.get_lock_diagnostics()
    assert isinstance(out, dict)


def test_flush_failure_captures_lock_diagnostics(caplog):
    """The capability: a failing flush must leave the lock state behind."""
    caplog.set_level(logging.ERROR)
    marker = {"initialised": True, "locked": True, "holder": "task-XYZ-holder",
              "waiters": 4}

    async def _boom(*a, **k):
        raise sqlite_locked()

    def sqlite_locked():
        return Exception("database is locked")

    with patch.object(ss, "get_lock_diagnostics", return_value=marker), \
         patch.object(ss, "_schedule_reconnect_if_dead", lambda e: None):
        _run(_force_flush_failure())

    blob = " ".join(r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR)
    assert "database is locked" in blob, blob
    assert "task-XYZ-holder" in blob, (
        "flush failure logged no lock diagnostics — the event that resets gate #3 "
        f"is still undiagnosable. got: {blob[:300]}"
    )


async def _force_flush_failure() -> None:
    """Drive the REAL _flush_write_queue with a queued write and a conn that
    raises 'database is locked', which is the production shape of the failure."""
    class _Conn:
        async def execute(self, *a, **k):
            raise Exception("database is locked")

        async def executemany(self, *a, **k):
            raise Exception("database is locked")

        async def commit(self):
            raise Exception("database is locked")

    # `_QUEUED_WRITES` is None until the store initialises; give the real
    # flusher a real queue rather than booting the whole store.
    # queue items are (sql, params) 2-tuples — see _flush_write_queue's unpack
    q: asyncio.Queue = asyncio.Queue()
    q.put_nowait(("INSERT INTO kv(k,v) VALUES(?,?)", ("crucix:test:rf3363", "v")))
    with patch.object(ss, "_QUEUED_WRITES", q), patch.object(ss, "_conn", _Conn()):
        await ss._flush_write_queue()


def test_diagnostics_failure_never_masks_the_original_error(caplog):
    """If the diagnostics probe itself throws, the flush error must still be
    logged — instrumentation must never eat the signal it exists to explain."""
    caplog.set_level(logging.ERROR)
    with patch.object(ss, "get_lock_diagnostics", side_effect=RuntimeError("probe died")), \
         patch.object(ss, "_schedule_reconnect_if_dead", lambda e: None):
        _run(_force_flush_failure())
    blob = " ".join(r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR)
    assert "database is locked" in blob, (
        f"a failing diagnostics probe swallowed the original error: {blob[:300]}"
    )
