"""R-F1422 — memory_wal lost-append race ("never forget" must mean it).

Pre-R-F1422: drain() snapshot-read all lines, retried, then BLIND-OVERWROTE
the WAL with only still_failing. A record_pending_fact() append landing between
the read and the rewrite was silently lost. R-F1422: append+drain serialized by
a lock; drain ROTATES (atomic) instead of overwriting, so concurrent appends
survive; crash-orphaned .draining files are recovered.

These tests drive the REAL record_pending_fact + drain against a temp WAL.
"""
from __future__ import annotations

import asyncio

import pytest

from aria_service.intel import memory_wal


@pytest.fixture
def wal(tmp_path, monkeypatch):
    p = tmp_path / "wal.jsonl"
    monkeypatch.setattr(memory_wal, "_WAL_PATH", p)
    # reset single-flight + stats
    monkeypatch.setattr(memory_wal, "_drain_in_progress", False)
    return p


def _count(p):
    if not p.exists():
        return 0
    return sum(1 for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip())


def test_append_during_drain_is_not_lost(wal):
    """THE race: a fact appended WHILE the drain runs must survive."""
    # seed 2 facts that will FAIL to store (so they stay in the WAL)
    memory_wal.record_pending_fact("t1", "fail one", "s", "ASSESSED")
    memory_wal.record_pending_fact("t2", "fail two", "s", "ASSESSED")

    appended_mid = {"done": False}

    async def _store_fact(**rec):
        # simulate the race: on the FIRST retry, a new fact gets appended
        # concurrently (as record_pending_fact would from another path)
        if not appended_mid["done"]:
            appended_mid["done"] = True
            memory_wal.record_pending_fact("t3", "appended during drain", "s", "ASSESSED")
        raise RuntimeError("store still failing")  # keep all in the WAL

    asyncio.run(memory_wal.drain(_store_fact, max_items=500))

    # all THREE must still be in the WAL — the mid-drain append (t3) must NOT
    # have been lost by the rewrite. Pre-R-F1422 this was 2 (t3 lost).
    contents = wal.read_text(encoding="utf-8")
    assert "fail one" in contents
    assert "fail two" in contents
    assert "appended during drain" in contents, "mid-drain append was LOST (race)"
    assert _count(wal) == 3


def test_successful_facts_removed_failures_kept(wal):
    memory_wal.record_pending_fact("ok", "store me", "s", "ASSESSED")
    memory_wal.record_pending_fact("bad", "keep me", "s", "ASSESSED")

    async def _store_fact(**rec):
        if rec.get("content") == "store me":
            return True            # success → removed
        raise RuntimeError("nope")  # failure → kept

    asyncio.run(memory_wal.drain(_store_fact))
    contents = wal.read_text(encoding="utf-8") if wal.exists() else ""
    assert "store me" not in contents   # drained
    assert "keep me" in contents        # retained for retry


def test_orphan_draining_file_recovered(wal, tmp_path):
    """A .draining orphan from a crashed prior drain must be processed, not
    overwritten/lost."""
    orphan = wal.with_suffix(".jsonl.draining")
    import json
    orphan.write_text(
        json.dumps({"topic": "orphan", "content": "survived a crash",
                    "source": "s", "confidence": "ASSESSED"}) + "\n",
        encoding="utf-8",
    )
    # also a live append after the crash
    memory_wal.record_pending_fact("live", "after crash", "s", "ASSESSED")

    stored = []

    async def _store_fact(**rec):
        stored.append(rec.get("content"))
        return True  # succeed → drained out

    asyncio.run(memory_wal.drain(_store_fact))
    # the orphan's fact was recovered + stored (not lost)
    assert "survived a crash" in stored


def test_empty_wal_noop(wal):
    async def _store_fact(**rec):
        return True
    res = asyncio.run(memory_wal.drain(_store_fact))
    assert res == {"pending": 0}


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
