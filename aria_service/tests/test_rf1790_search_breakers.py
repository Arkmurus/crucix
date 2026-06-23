"""R-F1790 — circuit breakers on the 3 previously-unprotected HTTP search paths.

Cross-check of ARIA's gap analysis (2026-06-23) CONFIRMED three search/fetch
paths hit external backends with NO circuit breaker, so a rate-limiting/dead
backend got hammered every call (the exact cascade breakers exist to stop):
  #8  researcher.web_search()      — raw DDG httpx.post, no breaker
  #9  web_search._search_searxng() — self-host adapter (the PRIMARY backend), no breaker
  #10 crawl_enhancements.fetch_via_wayback() — archive.org, no breaker

Fix: each path now shares/owns a circuit breaker (get_breaker) that records
success/failure and short-circuits when OPEN.

Capability (drives the REAL function): with the backend always failing, call the
function failure_threshold+N times and assert the backend is hit ONLY up to the
threshold — after that the breaker is OPEN and the path short-circuits. Without
the fix the backend would be hit on EVERY call (no plateau).
"""
import httpx
import pytest


def _failing_client(counter):
    class _C:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, *a, **k):
            counter["n"] += 1
            raise httpx.ConnectError("boom")
        async def post(self, *a, **k):
            counter["n"] += 1
            raise httpx.ConnectError("boom")
    return _C


@pytest.mark.asyncio
async def test_researcher_web_search_ddg_breaker(monkeypatch):
    from aria_service.intel import researcher as R
    from aria_service.intel import circuit_breaker as CB
    CB._breakers.pop("search:duckduckgo", None)  # fresh breaker

    calls = {"n": 0}
    monkeypatch.setattr("httpx.AsyncClient", _failing_client(calls))

    async def _fb(query, max_results, t0, *, reason=""):
        return {"ok": False, "results": [], "fallback_reason": reason}
    monkeypatch.setattr(R, "_multi_backend_fallback", _fb)

    for _ in range(7):
        await R.web_search("acme corp", max_results=3)

    # threshold=5: DDG hit on calls 1-5, breaker OPENs, calls 6-7 short-circuit.
    assert calls["n"] == 5, f"DDG should stop being hit at breaker threshold 5, got {calls['n']}"
    assert CB.get_breaker("search:duckduckgo").state == "OPEN"


@pytest.mark.asyncio
async def test_searxng_selfhost_breaker(monkeypatch):
    from aria_service.intel import web_search as WS
    from aria_service.intel import circuit_breaker as CB
    from aria_service.intel import search_searxng as SX
    CB._breakers.pop("search:searxng-selfhost", None)

    calls = {"n": 0}
    monkeypatch.setattr(SX, "is_configured", lambda: True)

    async def _search(query, count=10, lang="en"):
        calls["n"] += 1
        return {"ok": True, "configured": True, "results": []}  # 0 results = upstream blocked
    monkeypatch.setattr(SX, "search", _search)

    for _ in range(7):
        await WS._search_searxng("acme", max_results=5)

    # threshold=5: self-host hit on calls 1-5, breaker OPENs, 6-7 skip the adapter.
    assert calls["n"] == 5, f"self-host SearXNG should stop at breaker threshold 5, got {calls['n']}"
    assert CB.get_breaker("search:searxng-selfhost").state == "OPEN"


@pytest.mark.asyncio
async def test_wayback_breaker(monkeypatch):
    from aria_service.intel import crawl_enhancements as CE
    from aria_service.intel import circuit_breaker as CB
    CB._breakers.pop("fetch:wayback", None)

    calls = {"n": 0}
    monkeypatch.setattr("httpx.AsyncClient", _failing_client(calls))

    out = None
    for _ in range(5):
        out = await CE.fetch_via_wayback("https://example.com")
        assert out["ok"] is False

    # threshold=3: archive.org hit on calls 1-3, breaker OPENs, calls 4-5 short-circuit.
    assert calls["n"] == 3, f"wayback should stop hitting archive.org at threshold 3, got {calls['n']}"
    assert CB.get_breaker("fetch:wayback").state == "OPEN"
    assert "circuit OPEN" in (out["error"] or "")
