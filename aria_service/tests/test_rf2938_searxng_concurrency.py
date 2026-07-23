"""R-F2938 — the SearXNG client must bound concurrency against the self-hosted box.

A DD's adverse-media deep search fires up to 30 query templates through an
unbounded asyncio.gather. SearXNG is a single self-hosted instance; the 30-way
stampede rate-limited it, the caller's circuit breaker tripped OPEN after 3
failures, and the remaining templates skipped → partial=True on the adverse-media
blob → the Grade-A readiness grader read the question as UNRESOLVED even though
Brave (primary) had answered and 7 real findings were present.

These tests assert the LIMITER holds — the client never issues more than N
concurrent outbound requests — which is what stops the self-DOS.
"""
from __future__ import annotations

import asyncio

import pytest

from aria_service.intel import search_searxng as ss


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _reset_sem(monkeypatch):
    # Fresh limiter per test, bound to the test's own loop.
    ss._searxng_sem = None
    ss._searxng_sem_loop = None
    monkeypatch.setenv("SEARXNG_URL", "http://searxng.internal:8080")
    yield
    ss._searxng_sem = None
    ss._searxng_sem_loop = None


class _TrackingClient:
    """Fake httpx.AsyncClient that records max in-flight requests."""
    live = 0
    peak = 0

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, params=None):
        _TrackingClient.live += 1
        _TrackingClient.peak = max(_TrackingClient.peak, _TrackingClient.live)
        try:
            await asyncio.sleep(0.02)  # hold the slot so overlap is observable
            return type("R", (), {
                "status_code": 200,
                "json": lambda self=None: {"results": []},
            })()
        finally:
            _TrackingClient.live -= 1


def test_never_exceeds_the_configured_concurrency(monkeypatch):
    monkeypatch.setattr(ss, "_SEARXNG_CONCURRENCY", 4)
    ss._searxng_sem = None  # rebuild with the patched value
    _TrackingClient.live = 0
    _TrackingClient.peak = 0

    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", _TrackingClient)

    async def _fan_out_30():
        # Exactly the shape that caused the incident: 30 templates at once.
        await asyncio.gather(*[ss.search(f"q{i}") for i in range(30)])

    _run(_fan_out_30())
    assert _TrackingClient.peak <= 4, (
        f"SearXNG served {_TrackingClient.peak} concurrent requests — the "
        f"limiter is not holding, the self-DOS is back")
    assert _TrackingClient.peak >= 1, "nothing actually ran"


def test_all_thirty_still_complete(monkeypatch):
    """Bounding concurrency must not DROP templates — every query still runs,
    just serialised. (Dropping would REDUCE coverage, the opposite of the goal.)"""
    monkeypatch.setattr(ss, "_SEARXNG_CONCURRENCY", 4)
    ss._searxng_sem = None
    count = {"n": 0}

    class _Counter(_TrackingClient):
        async def get(self, url, params=None):
            count["n"] += 1
            return type("R", (), {"status_code": 200,
                                  "json": lambda self=None: {"results": []}})()

    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", _Counter)

    async def _fan():
        await asyncio.gather(*[ss.search(f"q{i}") for i in range(30)])

    _run(_fan())
    assert count["n"] == 30, f"only {count['n']}/30 templates reached SearXNG"


def test_default_concurrency_is_a_small_positive_int():
    assert 1 <= ss._SEARXNG_CONCURRENCY <= 16


def test_env_override_is_honoured(monkeypatch):
    monkeypatch.setenv("ARIA_SEARXNG_CONCURRENCY", "2")
    import importlib
    importlib.reload(ss)
    try:
        assert ss._SEARXNG_CONCURRENCY == 2
    finally:
        monkeypatch.delenv("ARIA_SEARXNG_CONCURRENCY", raising=False)
        importlib.reload(ss)


def test_empty_query_short_circuits_without_taking_a_slot(monkeypatch):
    """A rejected query must not consume a semaphore slot — else 30 empty
    queries could still exhaust the limiter."""
    import httpx

    class _MustNotCall(_TrackingClient):
        async def get(self, *a, **k):
            raise AssertionError("empty query should never reach the network")

    monkeypatch.setattr(httpx, "AsyncClient", _MustNotCall)
    out = _run(ss.search("   "))
    assert out["ok"] is False
