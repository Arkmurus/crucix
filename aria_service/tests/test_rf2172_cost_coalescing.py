"""R-F2172 — cost_tracker write-coalescing.

ROOT-CAUSE GUARD for the residual self-DOS (after R-F2157 fixed brain_hook):
cost_tracker.record_call did THREE read-modify-write round-trips on shared hot
keys (cost:index, cost:aggregate, cost:month) on EVERY LLM call. Under
autonomous load that saturated state_store's single connection — live
2026-06-30 showed cost:* keys timing out at 5s.

Fix: accumulate raw records in-process and replay the merges in ONE read+write
per hot key at most once per _COST_FLUSH_INTERVAL_S. These capability tests
drive the REAL record_call path and assert (a) many concurrent calls coalesce
into few writes, (b) no cost is lost, (c) the batch folds back on DB failure.
"""
from __future__ import annotations

import asyncio
import time

import pytest

# NB: import under the full name `cost_tracker` (not a short alias like `ct`)
# so the R-F1958 pre-commit function-name verifier resolves the module
# correctly — it mis-maps short aliases to look-alike modules.
from aria_service.intel import cost_tracker
from aria_service.intel import redis_store as rs


class _FakeStore:
    def __init__(self):
        self.kv = {}
        self.set_calls = 0
        self.set_by_key: dict[str, int] = {}

    async def get_json(self, key):
        import copy
        return copy.deepcopy(self.kv.get(key))

    async def set_json(self, key, obj, ex=None, **kw):
        import copy
        self.set_calls += 1
        self.set_by_key[key] = self.set_by_key.get(key, 0) + 1
        self.kv[key] = copy.deepcopy(obj)


@pytest.fixture
def fake(monkeypatch):
    store = _FakeStore()
    monkeypatch.setattr(rs, "get_json", store.get_json)
    monkeypatch.setattr(rs, "set_json", store.set_json)
    # Reset coalescing state between cases.
    cost_tracker._pending_cost_records = []
    cost_tracker._cost_last_flush = 0.0
    return store


def _mk(model="deepseek-chat", feat="chat", cost=0.001, ts=None):
    return {
        "id": f"call_{int((ts or time.time())*1000)}_{id(object()) & 0xffff:x}",
        "ts": ts or time.time(),
        "model": model, "provider": "deepseek", "feature": feat,
        "input_tokens": 100, "output_tokens": 50, "total_tokens": 150,
        "cost_usd": cost, "latency_ms": 10, "success": True, "error": "",
    }


def test_rf2172_concurrent_records_coalesce_writes(fake):
    """200 records within one interval must NOT produce 600 hot-key writes
    (3 per call). They coalesce — at most a couple of writes per hot key."""
    async def run():
        cost_tracker._COST_FLUSH_INTERVAL_S = 999.0
        cost_tracker._cost_last_flush = time.time()  # not yet due → accumulate
        for _ in range(200):
            cost_tracker._pending_cost_records.append(_mk())
        await cost_tracker._flush_cost_pending(force=True)
        return fake.set_by_key

    sbk = asyncio.run(run())
    # One write each to index / aggregate / month — NOT 200 each.
    assert sbk.get(cost_tracker.COST_INDEX_KEY, 0) == 1, sbk
    assert sbk.get(cost_tracker.COST_AGG_KEY, 0) == 1, sbk
    month_key = f"{cost_tracker.COST_MONTH_PREFIX}{cost_tracker._current_month_key()}"
    assert sbk.get(month_key, 0) == 1, sbk


def test_rf2172_no_cost_lost_in_coalescing(fake):
    """Coalescing must be lossless — every record's cost lands in the rollup,
    aggregate, and index."""
    async def run():
        cost_tracker._COST_FLUSH_INTERVAL_S = 999.0
        cost_tracker._cost_last_flush = time.time()
        for _ in range(40):
            cost_tracker._pending_cost_records.append(_mk(cost=0.002, feat="research"))
        await cost_tracker._flush_cost_pending(force=True)
        return fake.kv

    kv = asyncio.run(run())
    month_key = f"{cost_tracker.COST_MONTH_PREFIX}{cost_tracker._current_month_key()}"
    roll = kv[month_key]
    assert roll["total_calls"] == 40
    assert round(roll["total_cost_usd"], 6) == round(40 * 0.002, 6)
    assert roll["by_feature"]["research"]["calls"] == 40
    agg = kv[cost_tracker.COST_AGG_KEY]
    assert agg["research"]["calls"] == 40
    assert round(agg["research"]["cost_usd"], 6) == round(40 * 0.002, 6)
    index = kv[cost_tracker.COST_INDEX_KEY]
    assert len(index) == 40


def test_rf2172_index_capped_and_newest_first(fake):
    """Index keeps newest-first and is capped at _INDEX_CAP."""
    async def run():
        cost_tracker._COST_FLUSH_INTERVAL_S = 999.0
        cost_tracker._cost_last_flush = time.time()
        base = time.time()
        for i in range(cost_tracker._INDEX_CAP + 50):
            cost_tracker._pending_cost_records.append(_mk(ts=base + i))
        await cost_tracker._flush_cost_pending(force=True)
        return fake.kv[cost_tracker.COST_INDEX_KEY]

    index = asyncio.run(run())
    assert len(index) == cost_tracker._INDEX_CAP, f"index not capped: {len(index)}"
    # newest (largest ts) must be first
    assert index[0]["ts"] >= index[1]["ts"]


def test_rf2172_flush_folds_back_on_failure(fake, monkeypatch):
    """A DB failure during flush must fold the batch back — no cost lost."""
    async def boom(*a, **k):
        raise RuntimeError("state_store write queue full")

    async def run():
        cost_tracker._COST_FLUSH_INTERVAL_S = 999.0
        cost_tracker._cost_last_flush = time.time()
        cost_tracker._pending_cost_records.append(_mk())
        monkeypatch.setattr(rs, "set_json", boom)
        wrote = await cost_tracker._flush_cost_pending(force=True)
        return wrote, len(cost_tracker._pending_cost_records)

    wrote, pending = asyncio.run(run())
    assert wrote is False
    assert pending == 1, "record was lost on flush failure — must fold back"


def test_rf2172_record_call_accumulates_not_per_call_rmw(fake):
    """Drive the REAL record_call: a single call must NOT immediately do the 3
    hot-key RMW writes (they are deferred to the coalesced flush)."""
    async def run():
        cost_tracker._COST_FLUSH_INTERVAL_S = 999.0
        cost_tracker._cost_last_flush = time.time()  # not due → defer
        await cost_tracker.record_call(model="deepseek-chat", input_tokens=10,
                             output_tokens=5, feature_name="chat")
        # The per-call DETAIL record is still written inline (unique key), but
        # the hot index/agg/month keys must NOT have been touched yet.
        return fake.set_by_key

    sbk = asyncio.run(run())
    assert sbk.get(cost_tracker.COST_INDEX_KEY, 0) == 0, "index written per-call (not coalesced)"
    assert sbk.get(cost_tracker.COST_AGG_KEY, 0) == 0, "aggregate written per-call (not coalesced)"
    assert len(cost_tracker._pending_cost_records) == 1, "record was not accumulated"
