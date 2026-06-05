"""R-F1352 — the state_store READ path self-heals on a dead connection.

R-F1341 gave the WRITE path (_run_locked) a self-heal reconnect, but plain
reads (_row → get/get_json/lrange/hgetall, plus scan_keys/stats) bypass
_run_locked. So once the single shared aiosqlite connection died, every read
failed 'Connection closed' forever — the live symptom was health/perf and
autonomous/status hanging while health/live (no state read) stayed fast.

These tests drive the real read entry point (get) against a killed connection
and assert it (a) degrades gracefully, (b) schedules a reconnect, (c) recovers.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from aria_service.intel import state_store as ss


@pytest.fixture(autouse=True)
def _fresh():
    ss._reset_lock()
    yield
    ss._reset_lock()


# ── classification: only churn the connection when it can help ──────────────


def test_is_conn_dead_classifies():
    assert ss._is_conn_dead(ValueError("Connection closed"))
    assert ss._is_conn_dead(Exception("no active connection"))
    assert ss._is_conn_dead(Exception("Cannot operate on a closed database"))
    # A genuine query/programming error must NOT trigger a reconnect churn.
    assert not ss._is_conn_dead(Exception("near \"SELCT\": syntax error"))


# ── capability: the real read path recovers from a dead connection ──────────


@pytest.mark.asyncio
async def test_read_path_self_heals_on_dead_connection(tmp_path):
    await ss.connect(str(tmp_path / "s.db"))
    await ss.set("k", "v")
    assert await ss.get("k") == "v"

    # Kill the shared connection out from under the reader (the live wedge).
    await ss._conn.close()

    # A read now fails 'Connection closed' but degrades gracefully (no raise)…
    assert await ss.get("k") is None
    # …and a single-flight reconnect was scheduled — let it run.
    for _ in range(50):
        await asyncio.sleep(0.05)
        if not ss._reconnect_in_progress and ss._conn is not None:
            break
    # The connection is healed → the next read recovers the persisted value.
    assert await ss.get("k") == "v"
    await ss.close()


@pytest.mark.asyncio
async def test_non_connection_error_does_not_churn(monkeypatch, tmp_path):
    """A query error (not a dead connection) must NOT schedule a reconnect."""
    await ss.connect(str(tmp_path / "s.db"))
    calls = {"n": 0}

    async def _boom(*a, **k):
        raise Exception("near \"SELCT\": syntax error")

    async def _track():
        calls["n"] += 1

    monkeypatch.setattr(ss._conn, "execute", _boom)
    monkeypatch.setattr(ss, "_reconnect", _track)
    assert await ss.get("k") is None        # graceful default
    await asyncio.sleep(0.1)
    assert calls["n"] == 0                   # no reconnect churn on a query bug
    await ss.close()
