"""R-F2561 (P0.2) — web_integrity_agent self-probes attach the internal token.

The self-health prober hit token-gated internal endpoints (/report, /self/staged,
/autonomous/status, /cost/monthly/status, watchlist unread-count) with no auth →
401 on every one (false health failures + log spam, verified live 2026-07-12).
Fix: attach ARIA_INTERNAL_TOKEN (already accepted by the router auth).
"""
from __future__ import annotations

import asyncio

from aria_service.intel import web_integrity_agent as wia


def test_internal_auth_headers(monkeypatch):
    monkeypatch.setenv("ARIA_INTERNAL_TOKEN", "secret123")
    assert wia._internal_auth_headers() == {"Authorization": "Bearer secret123"}
    monkeypatch.delenv("ARIA_INTERNAL_TOKEN", raising=False)
    monkeypatch.setenv("ARIA_API_TOKEN", "api456")
    assert wia._internal_auth_headers() == {"Authorization": "Bearer api456"}
    monkeypatch.delenv("ARIA_API_TOKEN", raising=False)
    assert wia._internal_auth_headers() == {}


def test_check_endpoint_sends_bearer_token(monkeypatch):
    """Capability test: the REAL check_endpoint must send the Bearer token so a
    token-gated internal endpoint no longer 401s."""
    monkeypatch.setenv("ARIA_INTERNAL_TOKEN", "tok999")
    captured: dict = {}

    class _Resp:
        status_code = 200
        headers = {"content-type": "application/json"}
        def json(self):
            return {"status": "ok"}
        @property
        def text(self):
            return "{}"

    class _Client:
        def __init__(self, *a, **k):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return False
        async def get(self, url, headers=None, **k):
            captured["headers"] = headers
            return _Resp()
        async def post(self, url, json=None, headers=None, **k):
            captured["headers"] = headers
            return _Resp()

    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    asyncio.run(wia.check_endpoint({"path": "/api/aria/self/staged", "method": "GET", "expected": {}}))
    assert captured.get("headers") == {"Authorization": "Bearer tok999"}


if __name__ == "__main__":
    import sys
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
