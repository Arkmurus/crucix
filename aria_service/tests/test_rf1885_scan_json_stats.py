"""R-F1885 — absorption_quarantine.stats() must produce the SAME counts via the
batched scan_json() as it did via scan_keys + N get_json round-trips.

stats() is called by brain_hook.get_stats() on every /api/aria/health hit and did
up to 500 sequential `await get_json(k)` — the dominant health-endpoint cost.
scan_json() returns (key, parsed-JSON) for all matches in ONE query.

Capability test: drive the REAL stats() (the operator's actual get_stats path)
against a temp store with known pending/promoted/rejected entries, and assert the
user-visible counts + oldest-pending — proving semantic equivalence. Plus a direct
scan_json contract test.
"""
from __future__ import annotations

import asyncio
import time

from aria_service.intel import state_store
from aria_service.intel import redis_store as rs
from aria_service.intel import absorption_quarantine as aq


def test_stats_counts_match_known_entries(tmp_path):
    async def run():
        await state_store.connect(str(tmp_path / "s.db"))
        P = aq._KEY_PREFIX
        now = time.time()
        await rs.set_json(P + "a", {"status": "pending", "enqueued_at": now - 3600})
        await rs.set_json(P + "b", {"status": "pending", "enqueued_at": now - 7200})  # oldest
        await rs.set_json(P + "c", {"status": "promoted"})
        await rs.set_json(P + "d", {"status": "rejected"})
        await rs.set_json(P + "e", {"status": "rejected"})
        await rs.set_json("crucix:aria:other:z", {"status": "pending"})  # NOT quarantine → excluded
        return await aq.stats()
    s = asyncio.run(run())
    assert s["pending"] == 2, s
    assert s["promoted_recent"] == 1, s
    assert s["rejected_recent"] == 2, s
    # oldest pending was ~2h ago → age ~2.0h (not None, ~2)
    assert s["oldest_pending_age_h"] is not None and 1.8 <= s["oldest_pending_age_h"] <= 2.2, s


def test_stats_empty(tmp_path):
    async def run():
        await state_store.connect(str(tmp_path / "s2.db"))
        # The state_store is a module-level singleton; clear any quarantine keys
        # carried across tests so "empty" is deterministic.
        for k, _ in await rs.scan_json(aq._KEY_PREFIX + "*", count=1000):
            await rs.delete(k)
        return await aq.stats()
    s = asyncio.run(run())
    assert s["pending"] == 0 and s["promoted_recent"] == 0 and s["rejected_recent"] == 0
    assert s["oldest_pending_age_h"] is None


def test_scan_json_contract(tmp_path):
    async def run():
        await state_store.connect(str(tmp_path / "s3.db"))
        await rs.set_json("p:1", {"v": 1})
        await rs.set_json("p:2", {"v": 2})
        await rs.set_json("q:1", {"v": 3})
        items = dict(await rs.scan_json("p:*"))
        return items
    items = asyncio.run(run())
    assert set(items.keys()) == {"p:1", "p:2"}, items
    assert items["p:1"] == {"v": 1}


def test_scan_json_respects_count(tmp_path):
    async def run():
        await state_store.connect(str(tmp_path / "s4.db"))
        for i in range(5):
            await rs.set_json(f"k:{i}", {"i": i})
        return await rs.scan_json("k:*", count=2)
    got = asyncio.run(run())
    assert len(got) == 2, got
