"""R-F469 — OpenSanctions circuit breaker.

Operational hygiene item from next_session_pickup_2026_05_15: the
sanctions backbone had no circuit breaker. Under a 429 storm every
match() and search() call still hit the upstream, burning the free-tier
quota (1 req/sec) and adding 15s of per-call timeout to every screen
that fell behind.

R-F469 wires both endpoints through `circuit_breaker.get_breaker
("opensanctions.org", failure_threshold=3, cooldown_seconds=300)`.
When OPEN both paths short-circuit-return []. When CLOSED both record
success/failure with a precise reason (rate_limit/auth/server/timeout)
so dashboards can attribute the drop honestly.
"""
from __future__ import annotations

import asyncio
import pytest


def test_rf469_match_uses_breaker():
    from pathlib import Path
    src = Path(__file__).resolve().parents[2] / "aria_service" / "intel" / "sanctions.py"
    text = src.read_text(encoding="utf-8")
    # Find the match function and inspect its body
    start = text.index("async def _opensanctions_match")
    end = text.index("async def _opensanctions_search")
    body = text[start:end]
    assert 'get_breaker' in body, "match() must call get_breaker"
    assert '"opensanctions.org"' in body, "breaker name must be 'opensanctions.org'"
    assert 'failure_threshold=3' in body
    assert 'cooldown_seconds=300' in body
    assert 'is_open()' in body, "match() must short-circuit when breaker open"
    assert 'record_failure(reason="rate_limit")' in body, "429 must record rate_limit"
    assert 'record_failure(reason="auth")' in body, "401 must record auth"
    assert 'record_success()' in body, "200 must record success"


def test_rf469_search_uses_breaker():
    from pathlib import Path
    src = Path(__file__).resolve().parents[2] / "aria_service" / "intel" / "sanctions.py"
    text = src.read_text(encoding="utf-8")
    start = text.index("async def _opensanctions_search")
    # Search is the last private function in the file before the public API
    end = text.index("# ── Public API ──")
    body = text[start:end]
    assert 'get_breaker' in body, "search() must call get_breaker"
    assert '"opensanctions.org"' in body
    assert 'is_open()' in body
    assert 'record_failure(reason="rate_limit")' in body
    assert 'record_success()' in body


def test_rf469_runtime_breaker_opens_after_three_429s(monkeypatch):
    """Three consecutive 429s open the breaker; the fourth call must
    short-circuit without hitting the wire."""
    import importlib
    from aria_service.intel import circuit_breaker as _cb
    importlib.reload(_cb)  # fresh breaker registry per test
    from aria_service.intel import sanctions as _s

    # Patch _looks_like_entity_name to always pass + the breaker
    # module so its global registry is the fresh one.
    monkeypatch.setattr(_s, "_looks_like_entity_name", lambda n: True)

    call_count = {"n": 0}

    class _FakeResp:
        def __init__(self, status):
            self.status_code = status
            self.text = "rate limited"
        def json(self):
            return {}

    class _FakeClient:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a, **k):
            return False
        async def post(self, url, **k):
            call_count["n"] += 1
            return _FakeResp(429)
        async def get(self, url, **k):
            call_count["n"] += 1
            return _FakeResp(429)

    monkeypatch.setattr(_s, "httpx", type("HX", (), {
        "AsyncClient": lambda *a, **k: _FakeClient(),
        "HTTPError": Exception,
    }))

    async def run():
        # 3 failures → breaker opens
        for _ in range(3):
            await _s._opensanctions_match("Acme Corp")
        wire_calls_at_open = call_count["n"]
        # 4th call should NOT hit the wire
        await _s._opensanctions_match("Acme Corp")
        return wire_calls_at_open, call_count["n"]

    wire_at_open, wire_after = asyncio.run(run())
    assert wire_at_open == 3, f"expected 3 calls before open, got {wire_at_open}"
    assert wire_after == 3, (
        f"R-F469 regression: 4th call hit the wire — breaker did NOT open "
        f"(call count {wire_after})"
    )


def test_rf469_breaker_shared_between_match_and_search(monkeypatch):
    """match() failures count toward the same breaker that search() reads.
    OpenSanctions is one host with one shared quota — independent breakers
    would let the failed quota burn keep firing through search()."""
    import importlib
    from aria_service.intel import circuit_breaker as _cb
    importlib.reload(_cb)
    from aria_service.intel import sanctions as _s

    monkeypatch.setattr(_s, "_looks_like_entity_name", lambda n: True)

    call_count = {"match": 0, "search": 0}

    class _FakeResp:
        status_code = 429
        text = "rate limited"
        def json(self):
            return {}

    class _FakeClient:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a, **k):
            return False
        async def post(self, *a, **k):
            call_count["match"] += 1
            return _FakeResp()
        async def get(self, *a, **k):
            call_count["search"] += 1
            return _FakeResp()

    monkeypatch.setattr(_s, "httpx", type("HX", (), {
        "AsyncClient": lambda *a, **k: _FakeClient(),
        "HTTPError": Exception,
    }))

    async def run():
        # 3 match() failures
        for _ in range(3):
            await _s._opensanctions_match("Acme")
        # search() should now see breaker as open and skip the wire
        await _s._opensanctions_search("Acme")
        return dict(call_count)

    final = asyncio.run(run())
    assert final["match"] == 3, f"expected 3 match() wire calls, got {final['match']}"
    assert final["search"] == 0, (
        f"R-F469 regression: search() hit wire after match() tripped breaker "
        f"({final['search']} calls) — breakers should be shared on 'opensanctions.org'"
    )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
