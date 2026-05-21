"""R-F785 — heatmap rs.get_json timeout regression test.

Live evidence 2026-05-21 13:00 UTC: `/api/aria/learning/coverage`
hung >30s even after R-F781 (in-memory cache) and R-F775 (absorb
off loop) shipped, because the route hits `rs.get_json` BEFORE
calling build_heatmap. Under contention with continuous_update
writing the same key, the sqlite read was effectively blocking.

R-F785 wraps both rs.get_json AND rs.set_json with a 2s timeout
via asyncio.wait_for. On timeout the endpoint falls through to
build_heatmap (which has R-F781's in-memory cache).
"""
from __future__ import annotations

import asyncio


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def test_read_helper_returns_none_on_timeout(monkeypatch):
    """If rs.get_json hangs longer than the timeout, the helper
    returns None and the route falls through to build_heatmap."""
    from aria_service.routes import aria as aria_routes
    from aria_service.intel import redis_store as rs

    async def _hanging_get(_key):
        await asyncio.sleep(10.0)  # > the 2s timeout
        return {"matrix": "should_not_be_returned"}

    # Patch the get_json function directly on the real module so the
    # `from ..intel import redis_store as rs` lookup inside the
    # helper sees our fake (sys.modules patches don't help here
    # because the import resolves via the parent-package attribute).
    monkeypatch.setattr(rs, "get_json", _hanging_get)
    monkeypatch.setattr(
        aria_routes, "_HEATMAP_REDIS_READ_TIMEOUT_S", 0.1,
    )

    result = _run(aria_routes._read_heatmap_redis_cache())
    assert result is None, "helper must return None when Redis read times out"


def test_read_helper_returns_value_when_fast(monkeypatch):
    """If rs.get_json returns within budget, the helper passes the
    value through unchanged."""
    from aria_service.routes import aria as aria_routes
    from aria_service.intel import redis_store as rs

    payload = {"matrix": {"d1": {"j1": {"fact_count": 7}}}}

    async def _fast_get(_key):
        return payload

    monkeypatch.setattr(rs, "get_json", _fast_get)

    result = _run(aria_routes._read_heatmap_redis_cache())
    assert result is payload


def test_read_helper_returns_none_on_exception(monkeypatch):
    """If rs.get_json raises (e.g. sqlite locked, connection drop),
    the helper swallows + returns None — the route still serves
    via build_heatmap rather than 500ing."""
    from aria_service.routes import aria as aria_routes
    from aria_service.intel import redis_store as rs

    async def _raising_get(_key):
        raise RuntimeError("simulated state-backend failure")

    monkeypatch.setattr(rs, "get_json", _raising_get)

    result = _run(aria_routes._read_heatmap_redis_cache())
    assert result is None


def test_write_helper_swallows_timeout(monkeypatch):
    """The write side also has the 2s timeout — a slow set_json
    must not block the response. The helper returns None either
    way; we just verify it doesn't raise."""
    from aria_service.routes import aria as aria_routes
    from aria_service.intel import redis_store as rs

    async def _hanging_set(_key, _val, ex=None):
        await asyncio.sleep(10.0)

    monkeypatch.setattr(rs, "set_json", _hanging_set)
    monkeypatch.setattr(
        aria_routes, "_HEATMAP_REDIS_READ_TIMEOUT_S", 0.1,
    )

    # Should return cleanly (None) without raising.
    out = _run(aria_routes._write_heatmap_redis_cache({"matrix": {}}))
    assert out is None
