"""R-F2157 — brain_hook stats write-coalescing accumulator.

ROOT-CAUSE GUARD for the live 2026-06-30 self-DOS: _record_signal() and
_record_gate_skip() used to do a FULL read-modify-write of the single hot
key `crucix:aria:brain_hook:stats` on EVERY absorb. Under ARIA's own
concurrency that produced a thundering herd of reads (14 simultaneous
"state_store.get(...:stats) timed out after 5s" in the logs) plus write
amplification that saturated state_store's 2000-deep write queue
("write queue full — accept data loss") and tripped the brain_hook
circuit (p95=31s) — starving the user-facing read path so web proxied
timeouts and WhatsApp chat returned 503.

These capability tests drive the ACTUAL broken path (_record_signal /
_record_gate_skip / get_stats) and assert the user-visible structural
property: many concurrent signals collapse into AT MOST ONE coalesced DB
write per flush interval, and no counts are lost.
"""
from __future__ import annotations

import asyncio
import time

import pytest

from aria_service.intel import brain_hook
from aria_service.intel import redis_store as rs


class _FakeStore:
    """In-memory stand-in for redis_store with call counters so the test
    can prove how many DB round-trips the coalescing path actually makes."""

    def __init__(self):
        self.kv: dict = {}
        self.get_calls = 0
        self.set_calls = 0

    async def get_json(self, key):
        self.get_calls += 1
        # return a deep-ish copy so callers mutating it don't corrupt store
        import copy
        return copy.deepcopy(self.kv.get(key))

    async def set_json(self, key, obj, ex=None, **kw):
        self.set_calls += 1
        import copy
        self.kv[key] = copy.deepcopy(obj)


@pytest.fixture
def fake_store(monkeypatch):
    store = _FakeStore()
    monkeypatch.setattr(rs, "get_json", store.get_json)
    monkeypatch.setattr(rs, "set_json", store.set_json)
    # Reset the module-level accumulator + flush clock between cases.
    brain_hook._pending_modules = {}
    brain_hook._pending_global_total = 0
    brain_hook._pending_sectors = {}
    brain_hook._pending_gate_skips = {}
    brain_hook._stats_last_flush = 0.0
    brain_hook._stats_cache = {}
    brain_hook._stats_cache_at = 0
    return store


def test_concurrent_signals_coalesce_into_at_most_one_write(fake_store):
    """The structural fix: 200 concurrent _record_signal calls within one
    flush interval must NOT produce 200 DB writes (the old thundering-herd
    behaviour). They accumulate in-process and ride a single coalesced
    flush — at most ONE set_json for the whole burst."""
    async def run():
        # Long interval so the whole burst lands in one window.
        brain_hook._STATS_FLUSH_INTERVAL_S = 999.0
        brain_hook._stats_last_flush = 0.0  # first call is "due" → may flush once
        await asyncio.gather(*[
            brain_hook._record_signal("modA", success=True, sector="broker")
            for _ in range(200)
        ])
        # Force the final drain.
        await brain_hook._flush_stats_pending(force=True)
        return fake_store.set_calls

    set_calls = asyncio.run(run())
    # Old code: ~200 writes. New code: the burst coalesces — at most a
    # couple of flushes (one opportunistic + one forced), never per-call.
    assert set_calls <= 2, (
        f"expected coalesced writes (<=2), got {set_calls} — the per-absorb "
        "hot-key write storm is back"
    )


def test_no_counts_are_lost_under_coalescing(fake_store):
    """Coalescing must be lossless: every signal's increment lands in the
    persisted totals after the flush."""
    async def run():
        brain_hook._STATS_FLUSH_INTERVAL_S = 999.0
        brain_hook._stats_last_flush = 0.0
        for _ in range(50):
            await brain_hook._record_signal("modB", success=True)
        for _ in range(10):
            await brain_hook._record_signal("modB", success=False)
        await brain_hook._flush_stats_pending(force=True)
        return fake_store.kv.get(brain_hook._STATS_KEY)

    stats = asyncio.run(run())
    assert stats is not None, "stats key was never written"
    m = stats["modB"]
    assert m["total"] == 60, f"expected 60 total, got {m['total']}"
    assert m["success"] == 50, f"expected 50 success, got {m['success']}"
    assert m["fail"] == 10, f"expected 10 fail, got {m['fail']}"
    assert stats["_global"]["total"] == 60


def test_gate_skip_coalesces_and_persists(fake_store):
    async def run():
        brain_hook._STATS_FLUSH_INTERVAL_S = 999.0
        brain_hook._stats_last_flush = 0.0
        for _ in range(30):
            await brain_hook._record_gate_skip("fabricated_token", "modC")
        await brain_hook._flush_stats_pending(force=True)
        return fake_store.kv.get(brain_hook._STATS_KEY), fake_store.set_calls

    stats, set_calls = asyncio.run(run())
    assert set_calls <= 2, f"gate-skip write storm not coalesced: {set_calls} writes"
    bucket = stats["_gate_skips"]["fabricated_token"]
    assert bucket["total"] == 30
    assert bucket["by_module"]["modC"] == 30


def test_flush_folds_back_on_db_failure(fake_store, monkeypatch):
    """If the coalesced write fails, the snapshot must fold BACK into pending
    so the next flush retries it — no silent count loss."""
    async def boom(*a, **k):
        raise RuntimeError("simulated state_store write queue full")

    async def run():
        # Long interval + a recent last-flush so the record's own
        # opportunistic flush is NOT due — the delta stays pending until we
        # force-flush it against the failing write.
        brain_hook._STATS_FLUSH_INTERVAL_S = 999.0
        brain_hook._stats_last_flush = time.time()
        await brain_hook._record_signal("modD", success=True)
        monkeypatch.setattr(rs, "set_json", boom)
        wrote = await brain_hook._flush_stats_pending(force=True)
        # Pending must still hold modD's delta (folded back).
        pending_total = brain_hook._pending_modules.get("modD", {}).get("total", 0)
        return wrote, pending_total

    wrote, pending_total = asyncio.run(run())
    assert wrote is False, "flush claimed success despite DB write failing"
    assert pending_total == 1, (
        "delta was lost on write failure — must fold back into pending for retry"
    )


def test_get_stats_force_flushes_pending(fake_store):
    """get_stats() must drain pending so the dashboard reflects recent
    signals (it is 30s-cached, so this can't re-introduce the storm)."""
    async def run():
        brain_hook._STATS_FLUSH_INTERVAL_S = 999.0
        brain_hook._stats_last_flush = 0.0
        brain_hook._stats_cache = {}
        brain_hook._stats_cache_at = 0
        await brain_hook._record_signal("modE", success=True)
        # pending not yet flushed by interval; get_stats should force it.
        result = await brain_hook.get_stats()
        return result

    result = asyncio.run(run())
    assert "modE" in result["modules"], "get_stats did not surface freshly-accumulated module"
    assert result["modules"]["modE"]["total"] == 1
