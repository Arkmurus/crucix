"""R-F1933 (M4) — state_store write ordering: a queued set() must not reorder
against an immediate delete/incr/expire/hset/lpush.

set/set_json enqueue (R-F1541, drained on the next read) while delete/incr/expire/
hset/lpush executed immediately on the connection. So `set(k,v)` then `delete(k)`
ran the DELETE first and the queued INSERT flushed on the next read — resurrecting
the key. Fix: each immediate op flushes the write queue FIRST, so prior queued
writes land in program order before it runs.

These drive the REAL state_store against a temp sqlite DB and assert the race is gone.
"""
from __future__ import annotations

import tempfile

import pytest

from aria_service.intel import state_store as ss


@pytest.fixture
async def store(monkeypatch):
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    monkeypatch.setenv("ARIA_STATE_DB_PATH", tmp.name)
    if ss._conn is not None:
        try:
            await ss.close()
        except Exception:
            pass
    await ss.connect(tmp.name)
    yield ss
    try:
        await ss.close()
    except Exception:
        pass


async def test_set_then_delete_does_not_resurrect(store):
    await store.set("m4:k", "v1")          # enqueued
    await store.delete("m4:k")             # must flush the set, THEN delete
    assert await store.get("m4:k") is None, "a queued set() must not resurrect after delete()"


async def test_set_then_incr_operates_on_the_queued_value(store):
    await store.set("m4:c", "10")          # enqueued
    await store.incr("m4:c", 5)            # must flush set (10) first, then +5
    assert await store.get("m4:c") == "15", "incr must see the queued set value, not a stale row"


async def test_set_then_expire_then_delete_consistent(store):
    await store.set("m4:e", "x")           # enqueued
    await store.expire("m4:e", 1000)       # flush-first: applies to the real row
    assert await store.get("m4:e") == "x"
    await store.delete("m4:e")
    assert await store.get("m4:e") is None


def test_all_immediate_ops_flush_first_sourcepin():
    import pathlib
    src = (pathlib.Path(__file__).resolve().parents[2]
           / "aria_service" / "intel" / "state_store.py").read_text(encoding="utf-8", errors="ignore")
    # each immediate write op must call _flush_write_queue() (the R-F1933 guard)
    assert src.count("R-F1933 (M4)") >= 5, "delete/expire/incr/hset/lpush must each flush-first"
