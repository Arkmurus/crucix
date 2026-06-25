"""R-F1911 — rag_store.get_stats must memoise the expensive ChromaDB count so
/api/aria/health stops paying an O(collection-size) native scan on every poll.

ROOT CAUSE (live aria-intel, §22): /health was a flat ~30s on every call; the
entire cost was rag_store.get_stats -> _documents_collection.count() +
_facts_collection.count() (~215K chunks), uncached. Fix: single-flight TTL cache.

Capability test drives the REAL get_stats() and asserts:
  1. a second call within TTL does NOT re-count (cache hit),
  2. a burst of concurrent first-calls counts ONCE (single-flight, no herd),
  3. the honest staleness field is present,
  4. a transient failure is NOT cached.
"""
from __future__ import annotations

import asyncio

from aria_service.intel import rag_store


class _FakeCollection:
    def __init__(self, n, counter, key):
        self._n = n
        self._counter = counter
        self._key = key

    def count(self):
        # record how many times the expensive native count ran
        self._counter[self._key] = self._counter.get(self._key, 0) + 1
        return self._n


def _setup(monkeypatch, *, doc_n=63000, fact_n=152000, ttl=120.0):
    counter: dict = {}
    monkeypatch.setattr(rag_store, "_documents_collection", _FakeCollection(doc_n, counter, "doc"))
    monkeypatch.setattr(rag_store, "_facts_collection", _FakeCollection(fact_n, counter, "fact"))

    async def _ok():
        return True
    monkeypatch.setattr(rag_store, "_ensure_async", _ok)
    monkeypatch.setattr(rag_store, "_STATS_TTL_S", ttl)
    # fresh cache for each test
    monkeypatch.setattr(rag_store, "_stats_cache", {"value": None, "ts": 0.0})
    monkeypatch.setattr(rag_store, "_stats_lock", asyncio.Lock())
    return counter


def test_second_call_within_ttl_is_cached(monkeypatch):
    counter = _setup(monkeypatch)

    async def run():
        a = await rag_store.get_stats()
        b = await rag_store.get_stats()
        return a, b

    a, b = asyncio.run(run())
    assert a["documents_indexed"] == 63000 and a["facts_indexed"] == 152000
    assert a["total_chunks"] == 215000
    # the expensive count ran exactly once per collection across both calls
    assert counter == {"doc": 1, "fact": 1}, counter
    # honest staleness surfaced; second read is non-zero age (served from cache)
    assert a["stats_cache_age_s"] == 0.0
    assert b["stats_cache_age_s"] >= 0.0
    assert b["documents_indexed"] == 63000  # same value


def test_concurrent_burst_is_single_flight(monkeypatch):
    counter = _setup(monkeypatch)

    async def run():
        # 8 simultaneous first-calls (cold cache) — the /health poll burst
        return await asyncio.gather(*[rag_store.get_stats() for _ in range(8)])

    results = asyncio.run(run())
    # single-flight: the native count ran ONCE, not 8x
    assert counter == {"doc": 1, "fact": 1}, counter
    assert all(r["total_chunks"] == 215000 for r in results)


def test_ttl_expiry_recounts(monkeypatch):
    counter = _setup(monkeypatch, ttl=0.0)  # everything is immediately stale

    async def run():
        await rag_store.get_stats()
        await rag_store.get_stats()

    asyncio.run(run())
    # TTL=0 -> every call is a miss -> two counts
    assert counter == {"doc": 2, "fact": 2}, counter


def test_failure_is_not_cached(monkeypatch):
    counter = {"calls": 0}

    class _Boom:
        def count(self):
            counter["calls"] += 1
            raise RuntimeError("chroma down")

    monkeypatch.setattr(rag_store, "_documents_collection", _Boom())
    monkeypatch.setattr(rag_store, "_facts_collection", _Boom())

    async def _ok():
        return True
    monkeypatch.setattr(rag_store, "_ensure_async", _ok)
    monkeypatch.setattr(rag_store, "_STATS_TTL_S", 120.0)
    monkeypatch.setattr(rag_store, "_stats_cache", {"value": None, "ts": 0.0})
    monkeypatch.setattr(rag_store, "_stats_lock", asyncio.Lock())

    async def run():
        a = await rag_store.get_stats()
        b = await rag_store.get_stats()
        return a, b

    a, b = asyncio.run(run())
    assert a["available"] is False
    # failure not cached -> retried on the next call (recovery isn't blocked for the TTL)
    assert counter["calls"] >= 2, counter
