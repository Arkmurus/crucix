"""R-F2376 capability tests for live monitoring remediation."""
from __future__ import annotations

import pytest

from aria_service.routes import aria


@pytest.mark.asyncio
async def test_predictor_blocks_health_counter_uses_short_stale_cache(monkeypatch):
    """Health refreshes should not repeatedly hit contended state_store reads."""
    calls = 0

    async def fake_get(key: str) -> str:
        nonlocal calls
        calls += 1
        assert key == "crucix:predictor:blocks:24h"
        return "7"

    monkeypatch.setattr(aria.rs, "get", fake_get)
    aria._PREDICTOR_BLOCKS_24H_CACHE["value"] = 0
    aria._PREDICTOR_BLOCKS_24H_CACHE["expires_at"] = 0.0

    first = await aria._get_predictor_blocks_24h_cached(ttl_s=60.0)
    second = await aria._get_predictor_blocks_24h_cached(ttl_s=60.0)

    assert first == 7
    assert second == 7
    assert calls == 1


@pytest.mark.asyncio
async def test_predictor_blocks_health_counter_returns_stale_on_read_failure(monkeypatch):
    """A state-store timeout should not make health/perf refreshes pile up."""

    async def fail_get(key: str) -> str:
        assert key == "crucix:predictor:blocks:24h"
        raise TimeoutError("state_store busy")

    monkeypatch.setattr(aria.rs, "get", fail_get)
    aria._PREDICTOR_BLOCKS_24H_CACHE["value"] = 11
    aria._PREDICTOR_BLOCKS_24H_CACHE["expires_at"] = 0.0

    assert await aria._get_predictor_blocks_24h_cached(ttl_s=60.0) == 11
    assert aria._PREDICTOR_BLOCKS_24H_CACHE["expires_at"] > 0.0
