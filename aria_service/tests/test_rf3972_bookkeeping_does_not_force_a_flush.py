"""R-F3972 / C-61 — a duplicate fact that learned NOTHING triggered a full
dataset rewrite, twice.

`store_fact` detects a content-hash duplicate, bumps a counter, and calls
`_save()` — which marks the cache dirty and makes the next debounced tick write
the ENTIRE knowledge graph:

    knowledge.py:1451
        f["accessCount"] = f.get("accessCount", 0) + 1
        f["last_seen_at"] = now
        await _save()                      # -> full flush
        return {"action": "duplicate_skipped", ...}

`_write_to_disk_atomic` serialises the whole graph (~150-171 MB at ~223k facts),
fsyncs it, renames, fsyncs the directory — and then unconditionally calls
`_write_facts_sidecar(data)` (knowledge.py:677), writing the SAME data again with
its own fsync. At `FLUSH_DEBOUNCE_S = 2.0` that is roughly **1.7-2 GB/min** to
the same volume that also holds `aria_state.db`, its WAL, chromadb and the
neural shards.

Measured live 2026-08-13 while this was in place: `/health` reported
`loop: {"status": "starved", "p95_ms": 2058.1, "max_ms": 5620.1}`.

A duplicate is the single most common outcome of a crawl-and-absorb loop that
re-encounters the same pages. Nothing was learned; only `accessCount` and
`last_seen_at` moved. That is BOOKKEEPING, and it does not justify rewriting the
graph.

**Why losing a bump is acceptable and losing a fact is not.** `accessCount`
feeds ranking (`knowledge.py:1880`, capped at `min(count, 5)`) and a dedup
preference — it is a derived usage statistic, not knowledge. §7's infinite-memory
rule is about never deleting facts; it says nothing about the durability of a
counter. Every MATERIAL mutation — new fact, superseded content, conflict logged
— still flushes exactly as before. Verified: the other two `accessCount` bump
sites (`:1481`, `:1497`) also assign `f["content"]`, so they are material and
are untouched.

Bookkeeping is not dropped, only deferred: it rides the next material flush, and
if none arrives it is written on its own after `BOOKKEEPING_MAX_AGE_S`. An
explicit `flush()` (shutdown hooks, tests) always writes.
"""
from __future__ import annotations

import asyncio

import pytest

from aria_service.intel import knowledge as K


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch):
    monkeypatch.setattr(K, "_cache", {"facts": [{"id": "f1", "content": "x"}]})
    monkeypatch.setattr(K, "_dirty", False)
    monkeypatch.setattr(K, "_dirty_bookkeeping_since", None)
    # never start the real background flusher in a unit test
    monkeypatch.setattr(K, "_ensure_flusher", lambda: None)
    yield


def _writes(monkeypatch) -> list:
    """Capture actual disk writes."""
    seen = []

    # R-F3985 (C-72) added a `write_sidecar` argument to _write_to_disk_atomic,
    # so the throttle is now called with three. The fake mirrors the REAL
    # signature (`run_in_thread_throttled(fn, *args)`) rather than pinning an
    # arity the production call no longer has.
    async def _fake_throttled(fn, snapshot, *args):
        seen.append(snapshot)

    monkeypatch.setattr(
        "aria_service.intel._snapshot_throttle.run_in_thread_throttled",
        _fake_throttled,
    )
    return seen


# ── bookkeeping must not force a rewrite ─────────────────────────────────────

def test_a_bookkeeping_save_does_not_flush(monkeypatch):
    seen = _writes(monkeypatch)
    asyncio.run(K._save(material=False))
    asyncio.run(K._flush_to_disk())
    assert seen == [], (
        "a duplicate that only bumped a counter rewrote the whole graph — "
        "~150-171 MB canonical + the same again as sidecar, every 2 seconds"
    )


def test_a_material_save_still_flushes_immediately(monkeypatch):
    seen = _writes(monkeypatch)
    asyncio.run(K._save())
    asyncio.run(K._flush_to_disk())
    assert len(seen) == 1, "a real fact must still be persisted on the next tick"


def test_default_is_material_so_every_existing_caller_is_unchanged(monkeypatch):
    """`_save()` has many callers; the safe default is the old behaviour."""
    seen = _writes(monkeypatch)
    asyncio.run(K._save())          # no argument
    asyncio.run(K._flush_to_disk())
    assert len(seen) == 1


# ── bookkeeping is DEFERRED, never dropped ───────────────────────────────────

def test_bookkeeping_rides_the_next_material_flush(monkeypatch):
    seen = _writes(monkeypatch)
    asyncio.run(K._save(material=False))
    asyncio.run(K._save())                       # a real fact arrives
    asyncio.run(K._flush_to_disk())
    assert len(seen) == 1
    assert K._dirty_bookkeeping_since is None, (
        "the material write persisted the counter too; the marker must clear or "
        "it will force a redundant flush later"
    )


def test_stale_bookkeeping_is_eventually_written(monkeypatch):
    """If no material change ever arrives, counters must still reach disk."""
    seen = _writes(monkeypatch)
    asyncio.run(K._save(material=False))
    # age the marker past the ceiling
    monkeypatch.setattr(
        K, "_dirty_bookkeeping_since",
        K._dirty_bookkeeping_since - K.BOOKKEEPING_MAX_AGE_S - 1.0,
    )
    asyncio.run(K._flush_to_disk())
    assert len(seen) == 1, "bookkeeping was deferred forever, not deferred"
    assert K._dirty_bookkeeping_since is None


def test_an_explicit_flush_always_writes(monkeypatch):
    """Shutdown hooks and tests must be able to force the write."""
    seen = _writes(monkeypatch)
    asyncio.run(K._save(material=False))
    asyncio.run(K.flush())
    assert len(seen) == 1, "shutdown would lose the pending counter bumps"


def test_nothing_pending_writes_nothing(monkeypatch):
    seen = _writes(monkeypatch)
    asyncio.run(K._flush_to_disk())
    assert seen == []


# ── the duplicate path must actually use it ──────────────────────────────────

def test_the_duplicate_path_saves_as_bookkeeping():
    from ._source_probe import function_code
    src = function_code(K, "store_fact")
    assert "material=False" in src, (
        "the duplicate_skipped branch still forces a material flush — every "
        "re-encountered page rewrites the entire knowledge graph"
    )


def test_the_material_branches_were_not_downgraded():
    """superseded and content-update BOTH change f['content'] — they must
    still flush immediately, or a real fact could be lost on a crash."""
    from ._source_probe import function_code
    src = function_code(K, "store_fact")
    assert src.count("material=False") == 1, (
        f"expected exactly one bookkeeping save, found "
        f"{src.count('material=False')} — a material mutation was downgraded"
    )
