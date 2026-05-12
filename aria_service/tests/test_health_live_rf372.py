"""R-F372 — /health/live fastpath tests.

Live evidence 2026-05-12 BST 3× failures of fly.io healthcheck:
    13:00:51 — failed under brain_hook 11.8s p95 load
    13:03:10 — post-restart cold-boot (within grace, expected)
    13:22:53 — failed during 5-keyword research cycle + dashboard fan-out

Root cause: /health does Redis ping + mastery + grounded_rate +
adversarial + mode + breakers — too much work for a 10s LB probe.
Fix: /health/live = zero-Redis fastpath that returns from in-process
state only. fly.toml healthcheck switched to /health/live.

These tests pin: (1) endpoint exists, (2) it doesn't touch Redis,
(3) returns 200 + build_rev + status:alive, (4) it's in the public
auth-bypass list so fly.io can hit it without a token.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


def _build_app():
    """Build a minimal FastAPI app with just the aria router mounted.
    We don't run lifespan (which would touch chromadb + LLM) — we just
    need the /health/live route registered."""
    from fastapi import FastAPI
    from aria_service.routes.aria import router as aria_router
    app = FastAPI()
    app.include_router(aria_router)
    return app


def test_health_live_returns_200_and_alive_status():
    app = _build_app()
    with TestClient(app) as client:
        r = client.get("/api/aria/health/live")
    assert r.status_code == 200
    body = r.json()
    assert body.get("status") == "alive"
    assert "build_rev" in body


def test_health_live_does_not_touch_redis():
    """The fastpath must not call into redis_store.get / lpush / etc.
    If it did, a slow Redis would defeat the whole purpose. Patch the
    redis_store module to raise on any call — the endpoint should still
    succeed."""
    from aria_service.intel import redis_store as rs
    app = _build_app()

    async def boom(*args, **kwargs):
        raise RuntimeError("R-F372: /health/live touched Redis — should be impossible")

    with patch.object(rs, "get", side_effect=boom), \
         patch.object(rs, "set", side_effect=boom), \
         patch.object(rs, "lrange", side_effect=boom):
        with TestClient(app) as client:
            r = client.get("/api/aria/health/live")
    assert r.status_code == 200, (
        f"R-F372 fastpath must NOT touch Redis. Got {r.status_code}: {r.text[:200]}"
    )
    assert r.json().get("status") == "alive"


def test_health_live_is_in_public_auth_bypass():
    """fly.io's load balancer doesn't send a bearer token. The /health/live
    path must be in _PUBLIC_AUTH_BYPASS_PATHS so the router auth dep
    skips the bearer check."""
    from aria_service.routes.aria import _PUBLIC_AUTH_BYPASS_PATHS
    assert "/api/aria/health/live" in _PUBLIC_AUTH_BYPASS_PATHS


def test_rich_health_still_exists():
    """R-F372 didn't remove /health — it added /health/live alongside.
    Operator-facing /health must still respond (just may be slower than
    /health/live under load)."""
    app = _build_app()
    with TestClient(app) as client:
        r = client.get("/api/aria/health")
    # May return 200 or 500 depending on Redis state in test env,
    # but the ROUTE must exist (i.e., not 404).
    assert r.status_code != 404, "/api/aria/health route disappeared"
