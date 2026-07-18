"""R-F2770 — record_activity must DURABLY persist across event loops.

_save_activities() used asyncio.ensure_future(rs.set_json(...)) (R-F1520 fire-and-forget),
returning before the write landed. A caller on an isolated/short-lived loop (asyncio.run
per request, or the test harness's _run) closes the loop and CANCELS the pending write, so
the activity is silently LOST — the root cause of 6 competitor-tracker failures (activity/
tender/firm-aggregation/chat-context/briefing all empty). This capability test drives the
real record→(new loop)→load path and asserts the activity survives the loop boundary.
"""
from __future__ import annotations

import asyncio

import pytest

from aria_service.intel import competitor_tracker as ct_mod
from aria_service.intel import redis_store as rs


@pytest.fixture(autouse=True)
def _mem_backend():
    from aria_service.intel import intel_ledger
    rs._client = None
    rs._mem_store.clear()
    intel_ledger._cache = None
    yield
    rs._mem_store.clear()
    intel_ledger._cache = None


def test_rf2770_activity_survives_the_loop_boundary():
    # Record on ONE event loop (asyncio.run closes it when done).
    aid = asyncio.run(ct_mod.record_activity({
        "firm": "Rival Defence Ltd", "country": "Poland",
        "title": "Wins radar tender", "activity_type": "contract_award",
    }))
    assert aid

    # Load on a SEPARATE event loop — the write must already be durable.
    data = asyncio.run(ct_mod._load_activities())
    items = data.get("items", [])
    assert len(items) == 1, "activity must persist across the loop boundary (write was owned)"
    assert items[0]["id"] == aid
    assert items[0]["firm"] == "Rival Defence Ltd"
    assert items[0]["country_iso2"] == "PL"
