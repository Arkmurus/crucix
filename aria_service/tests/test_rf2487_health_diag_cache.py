"""R-F2487 — /health's self-diagnostic read is TTL-cached so concurrent polls do
not each hit the (sometimes saturated) state_store, and it degrades to the last
snapshot on a slow/failed read instead of blocking the /health response.
"""
import asyncio
from unittest.mock import patch

import pytest

from aria_service import main


@pytest.mark.asyncio
async def test_rf2487_diagnostic_read_is_ttl_cached():
    main._HEALTH_DIAG_CACHE["data"] = None
    main._HEALTH_DIAG_CACHE["ts"] = 0.0
    calls = {"n": 0}

    async def fake_get_json(key):
        calls["n"] += 1
        return {"overall": "GREEN"}

    with patch("aria_service.intel.redis_store.get_json", new=fake_get_json):
        r1 = await main._read_self_diagnostic_cached()
        r2 = await main._read_self_diagnostic_cached()
        r3 = await main._read_self_diagnostic_cached()

    assert r1 == {"overall": "GREEN"}
    assert r2 == r1 and r3 == r1
    assert calls["n"] == 1, f"expected 1 store read within TTL, got {calls['n']}"


@pytest.mark.asyncio
async def test_rf2487_serves_last_snapshot_on_read_failure():
    # A prior snapshot exists, but the cache is stale so a refresh is attempted.
    main._HEALTH_DIAG_CACHE["data"] = {"overall": "CACHED"}
    main._HEALTH_DIAG_CACHE["ts"] = 0.0  # monotonic()-0 >> TTL -> stale -> refresh

    async def failing_get_json(key):
        raise asyncio.TimeoutError()

    with patch("aria_service.intel.redis_store.get_json", new=failing_get_json):
        r = await main._read_self_diagnostic_cached()

    assert r == {"overall": "CACHED"}, "must serve the last snapshot on read failure"
