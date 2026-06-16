"""R-F1614 — make-loud capability tests for 3 verified silent-failure hotspots.

Each hotspot previously swallowed a genuinely damaging failure and returned a
benign-looking value (empty results / unreviewed draft / silent orphan), so the
brain never learned the failure happened. R-F1614 wires each swallow to
``engine_wiring.wire_failure`` (fire-and-forget brain signal) before the swallow.

These tests drive the REAL shipping function with real input, force the inner
call to raise, and assert ``wire_failure`` was invoked with the right module.

Hotspots covered behaviourally here:
  1. web_search backends (_search_bing_news / _search_google_news /
     _search_academic / _search_duckduckgo): backend error -> [] looked like
     "no results" (confidently ungrounded).
  3. fly_deployer._destroy_machine: canary-cleanup failure swallowed ->
     orphaned Fly machine burning $ while deploy reports success.

Hotspot 2 (routes/aria.py contract self-review at ~line 8296) is wired in the
shipping code but its except-block lives inline inside ``chat_ep`` and is only
reachable after a full LLM chain populates ``response_text`` + an attached
document is present — not isolable without an entire-route mock harness, so it
is NOT asserted here (per the task: only assert hotspots actually testable).
The wiring itself is verified to compile and is exercised live.
"""
from __future__ import annotations

import asyncio

import pytest

from aria_service.intel import web_search
from aria_service.intel import engine_wiring


# ── helper: capture wire_failure calls ───────────────────────────────────────

def _install_capture(monkeypatch, target_module, attr="wire_failure"):
    """Monkeypatch wire_failure on the given module's namespace to capture calls.

    Returns the list that captured (module, detail, gap_type, source) tuples.
    """
    captured: list[dict] = []

    def _fake_wire_failure(module="", detail="", gap_type="engine_failure", source=""):
        captured.append({
            "module": module,
            "detail": detail,
            "gap_type": gap_type,
            "source": source,
        })

    monkeypatch.setattr(target_module, attr, _fake_wire_failure)
    return captured


# ── Hotspot 1: web_search backends ───────────────────────────────────────────

def test_rf1614_bing_news_backend_error_wires_failure(monkeypatch):
    """_search_bing_news: an httpx error (was a fully-silent swallow) wires."""
    captured = _install_capture(monkeypatch, web_search)

    class _BoomClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, *a, **k):
            raise RuntimeError("bing backend down")

    monkeypatch.setattr(web_search.httpx, "AsyncClient", _BoomClient)

    out = asyncio.run(web_search._search_bing_news("savunma sanayii", max_results=5))
    assert out == [], "backend error must still return [] (fail-open)"
    assert any(c["module"] == "web_search._search_bing_news" for c in captured), (
        f"expected wire_failure for bing_news, got {captured}"
    )
    sig = next(c for c in captured if c["module"] == "web_search._search_bing_news")
    assert sig["gap_type"] == "search_backend_failure"
    assert "bing backend down" in sig["detail"]


def test_rf1614_google_news_backend_error_wires_failure(monkeypatch):
    """_search_google_news: backend error -> [] now also wires the brain."""
    captured = _install_capture(monkeypatch, web_search)

    class _BoomClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, *a, **k):
            raise RuntimeError("gnews backend down")

    monkeypatch.setattr(web_search.httpx, "AsyncClient", _BoomClient)

    out = asyncio.run(web_search._search_google_news("test query", max_results=5))
    assert out == []
    assert any(c["module"] == "web_search._search_google_news" for c in captured), (
        f"expected wire_failure for google_news, got {captured}"
    )


def test_rf1614_academic_backend_error_wires_failure(monkeypatch):
    """_search_academic: search_all raising -> [] now wires the brain."""
    captured = _install_capture(monkeypatch, web_search)

    from aria_service.intel.sources import academic as _ac

    async def _boom(*a, **k):
        raise RuntimeError("academic fan-out down")

    monkeypatch.setattr(_ac, "search_all", _boom)

    out = asyncio.run(web_search._search_academic("test query", max_results=5))
    assert out == []
    assert any(c["module"] == "web_search._search_academic" for c in captured), (
        f"expected wire_failure for academic, got {captured}"
    )


def test_rf1614_duckduckgo_backend_error_wires_failure(monkeypatch):
    """_search_duckduckgo: an httpx error wires the brain (breaker-only before)."""
    captured = _install_capture(monkeypatch, web_search)

    # Force the breaker to NOT be open so we reach the request path.
    from aria_service.intel import circuit_breaker as _cb

    class _OpenFalseBreaker:
        def is_open(self):
            return False

        def record_failure(self):
            pass

        def record_success(self):
            pass

    monkeypatch.setattr(_cb, "get_breaker", lambda *a, **k: _OpenFalseBreaker())

    class _BoomClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, *a, **k):
            raise RuntimeError("ddg backend down")

    monkeypatch.setattr(web_search.httpx, "AsyncClient", _BoomClient)

    out = asyncio.run(web_search._search_duckduckgo("test query", max_results=5))
    assert out == []
    assert any(c["module"] == "web_search._search_duckduckgo" for c in captured), (
        f"expected wire_failure for duckduckgo, got {captured}"
    )


# ── Hotspot 3: fly_deployer._destroy_machine ─────────────────────────────────

def test_rf1614_destroy_machine_failure_wires_failure(monkeypatch):
    """_destroy_machine: a delete error (was bare except: pass) wires an
    orphaned-machine gap so the brain/coder can reconcile the burning $ leak."""
    # Patch wire_failure on the engine_wiring module (fly_deployer does a local
    # import `from aria_service.intel.engine_wiring import wire_failure`).
    captured = _install_capture(monkeypatch, engine_wiring)

    from aria_service.autonomous.fly_deployer import FlyDeployer

    class _BoomClient:
        async def delete(self, *a, **k):
            raise RuntimeError("fly api 500")

        async def aclose(self):
            pass

    dep = FlyDeployer(
        redis_client=None,
        aria_service_url="http://x",
        http_client=_BoomClient(),
    )

    # Must NOT raise — the swallow stays (fail-open) but is now loud.
    asyncio.run(dep._destroy_machine("aria-intel", "abc123"))

    assert any(
        c["module"] == "fly_deployer._destroy_machine" for c in captured
    ), f"expected wire_failure for _destroy_machine, got {captured}"
    sig = next(c for c in captured if c["module"] == "fly_deployer._destroy_machine")
    assert sig["gap_type"] == "orphaned_machine"
    assert "abc123" in sig["detail"]


def test_rf1614_destroy_machine_success_does_not_wire(monkeypatch):
    """Control: a successful destroy does NOT fire wire_failure (no false alarm)."""
    captured = _install_capture(monkeypatch, engine_wiring)

    from aria_service.autonomous.fly_deployer import FlyDeployer

    class _OkClient:
        async def delete(self, *a, **k):
            return None

        async def aclose(self):
            pass

    dep = FlyDeployer(
        redis_client=None,
        aria_service_url="http://x",
        http_client=_OkClient(),
    )
    asyncio.run(dep._destroy_machine("aria-intel", "abc123"))
    assert not any(
        c["module"] == "fly_deployer._destroy_machine" for c in captured
    ), f"successful destroy must not wire a failure, got {captured}"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
