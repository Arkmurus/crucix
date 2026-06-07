"""R-F1397 — state_store connection-churn fix.

Live failure class (2026-06-07): self-heal #2 fired 80s after a fresh boot;
each heal (a) churned the connection even when it was merely backlogged,
(b) left a `_conn is None` window in which reads returned false not_founds
and _upsert silently DROPPED writes (a 202'd job that was never stored), and
(c) on reopen failure left `_conn = None` forever — reads then returned None
without raising, so nothing ever scheduled another reconnect.

Fixes under test:
  (a) probe-before-churn — SELECT 1 answers -> keep the connection
  (b) new-conn-first swap — no None window; _upsert raises on conn-None
  (c) reopen failure keeps the old conn so its errors keep self-healing
"""
import asyncio
from pathlib import Path

import pytest

from aria_service.intel import redis_store, state_store
from aria_service.routes import aria as aria_routes


async def _open_conn(db_path: Path):
    import aiosqlite
    conn = await aiosqlite.connect(str(db_path))
    await conn.execute(
        "CREATE TABLE IF NOT EXISTS state ("
        "key TEXT PRIMARY KEY, value TEXT NOT NULL, kind TEXT NOT NULL, "
        "expires_at REAL)")
    await conn.commit()
    return conn


@pytest.mark.asyncio
async def test_probe_healthy_conn_skips_reset(monkeypatch, tmp_path):
    """(a) a healthy-but-was-slow connection is NOT churned."""
    conn = await _open_conn(tmp_path / "s.db")
    monkeypatch.setattr(state_store, "_conn", conn)
    monkeypatch.setattr(state_store, "_DB_PATH", tmp_path / "s.db")
    before = state_store._op_timeout_counts["reconnect"]
    await state_store._reconnect()
    assert state_store._conn is conn          # same object — no churn
    assert state_store._op_timeout_counts["reconnect"] == before
    await conn.close()


@pytest.mark.asyncio
async def test_dead_conn_replaced_and_usable(monkeypatch, tmp_path):
    """(b) a dead connection is replaced; the replacement works."""
    db = tmp_path / "s.db"
    conn = await _open_conn(db)
    await conn.close()                         # probe will fail fast
    monkeypatch.setattr(state_store, "_conn", conn)
    monkeypatch.setattr(state_store, "_DB_PATH", db)
    before = state_store._op_timeout_counts["reconnect"]
    await state_store._reconnect()
    assert state_store._conn is not conn
    assert state_store._conn is not None
    cur = await state_store._conn.execute("SELECT 1")
    assert (await cur.fetchone())[0] == 1
    await cur.close()
    assert state_store._op_timeout_counts["reconnect"] == before + 1
    await state_store._conn.close()


@pytest.mark.asyncio
async def test_reopen_failure_keeps_old_conn(monkeypatch, tmp_path):
    """(c) when the replacement cannot open, the old conn STAYS so its
    errors keep scheduling reconnects (a None conn never self-healed)."""
    conn = await _open_conn(tmp_path / "s.db")
    await conn.close()                         # dead — probe fails
    monkeypatch.setattr(state_store, "_conn", conn)
    # a path whose parent does not exist -> aiosqlite.connect raises
    monkeypatch.setattr(state_store, "_DB_PATH",
                        tmp_path / "no_such_dir" / "x.db")
    await state_store._reconnect()
    assert state_store._conn is conn           # old conn kept, not None


@pytest.mark.asyncio
async def test_upsert_raises_when_conn_none(monkeypatch):
    """(b) a write during a no-connection window RAISES (was a silent drop
    that 202'd a job which was never stored). OperationalError, NOT
    StateWriteError — _run_locked re-raises StateWriteError even for
    non-critical compound ops, which would crash lpush/hset callers."""
    import sqlite3
    monkeypatch.setattr(state_store, "_conn", None)
    with pytest.raises(sqlite3.OperationalError):
        await state_store.set("crucix:test:rf1397", "v")


@pytest.mark.asyncio
async def test_compound_op_still_graceful_when_conn_none(monkeypatch):
    """(b) guard: a NON-critical compound op (lpush) during the window keeps
    its graceful return-default contract — the conn-None raise must not
    escape _run_locked for non-critical callers."""
    monkeypatch.setattr(state_store, "_conn", None)
    await state_store.lpush("crucix:test:rf1397:list", "v")  # must not raise


@pytest.mark.asyncio
async def test_capability_job_set_fails_closed_during_window(monkeypatch):
    """Capability: the REAL job-store write chain (_readdoc_job_set ->
    redis_store.set_json -> state_store.set -> _upsert) returns False during
    a no-connection window, so the endpoint answers 503 and the listener
    falls back to sync mode — instead of 202'ing a job that was never stored."""
    monkeypatch.setattr(redis_store, "_BACKEND", "sqlite")
    monkeypatch.setattr(state_store, "_conn", None)
    ok = await aria_routes._readdoc_job_set("rf1397job", {"status": "processing"})
    assert ok is False
