"""R-F2566 — llm_health_checker must not build an illegal 'Bearer ' header.

Bug: with an empty ARIA_LLM_KEY the probe built `Authorization: Bearer ` (whitespace-
only value), which httpx rejects as an "Illegal header value" BEFORE sending — so the
probe failed at header construction, reported the sovereign as DOWN even when up, and
recorded a capability gap every cycle. Fix: omit the header when the key is empty
(matching aria_llm_provider / openai_compat).

Capability test: drives the REAL `_probe()` and asserts (a) no illegal-header error on
an empty key + no Authorization header sent, (b) the header IS sent when a key is present.
"""
from __future__ import annotations

import asyncio

from aria_service.llm import resilience as R


class _Resp:
    status_code = 200
    text = "{}"


class _Client:
    """Captures the headers the probe passes to client.post."""
    captured: dict = {}

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, headers=None, json=None, **k):
        # httpx would raise on a "Bearer " (whitespace-only) value; emulate that so the
        # test proves the fix actually prevents the illegal header being constructed.
        for v in (headers or {}).values():
            if v is not None and str(v).strip() == "" or (isinstance(v, str) and v == "Bearer "):
                raise ValueError(f"Illegal header value {v!r}")
        _Client.captured = dict(headers or {})
        return _Resp()


def _run_probe(monkeypatch, key: str):
    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    _Client.captured = {}
    hc = R.LLMHealthChecker(endpoint="http://sovereign.test", api_key=key)
    asyncio.run(hc._probe())
    return _Client.captured


def test_empty_key_sends_no_auth_header(monkeypatch):
    # The bug: empty key -> "Bearer " -> illegal header. After the fix, NO auth header.
    captured = _run_probe(monkeypatch, "")          # must not raise
    assert "Authorization" not in captured

    captured_ws = _run_probe(monkeypatch, "   ")     # whitespace-only key -> also no header
    assert "Authorization" not in captured_ws


def test_present_key_sends_bearer(monkeypatch):
    captured = _run_probe(monkeypatch, "sk-real-token")
    assert captured.get("Authorization") == "Bearer sk-real-token"


if __name__ == "__main__":
    import sys
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
