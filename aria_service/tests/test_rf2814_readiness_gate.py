"""R-F2814 (Stage A of the R-F2813 HA re-architecture) — the READINESS gate.

The recurring "ARIA keeps breaking" after a deploy/restart traces to one bug:
`/health/live` goes green the instant uvicorn binds, but the LLM provider stays
None for the ~10-min background warmup — and Fly/WA/web treated "alive" as "can
serve", routed a chat to a not-ready brain, and it HUNG the whole warmup window.

Stage A adds a real readiness signal so a not-ready brain is *known* and every
surface fast-fails with an honest "warming up" instead of a 15-min hang:
  * GET /health/ready — 503 while llm_provider is None, 200 once it is set.
  * POST /chat + POST /chat/stream — fast 503 {error: warming_up} while warming,
    BEFORE the async_mode branch (so no un-runnable job is registered), and §13
    mirrored across both chat and chat_stream.

These are CAPABILITY tests (§3c): they drive the real endpoints through the real
handlers, asserting the user-visible outcome (fast honest 503, never a hang).
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


def _build_app():
    """Mount the aria router on a bare FastAPI app (no lifespan → no chromadb/LLM
    boot). We drive app.state.llm_provider by hand to simulate warming vs ready.
    The router enforces require_aria_token when a token secret is set in the env;
    override it to a no-op so the test drives the readiness guard, not auth (the
    readiness guard is what we're pinning, and /chat runs behind auth in prod)."""
    from fastapi import FastAPI
    from aria_service.routes.aria import (
        router as aria_router, require_aria_token, _router_auth_dep,
    )
    app = FastAPI()
    app.include_router(aria_router)
    # The router enforces auth via _router_auth_dep (APIRouter(dependencies=[...])).
    app.dependency_overrides[_router_auth_dep] = lambda: None
    app.dependency_overrides[require_aria_token] = lambda: None
    return app


class _ExhaustedChain:
    """Real readiness shape emitted by FallbackProvider.get_health()."""

    is_configured = True

    def get_health(self):
        return {
            "resilient": False,
            "active_providers": [],
            "cooling_providers": [
                {"name": "deepseek", "reason": "billing", "seconds_remaining": 3600}
            ],
            "last_exhaustion_age_s": 10.0,
        }


class _UnknownHealthChain:
    is_configured = True

    def get_health(self):
        return {}


class _BareUnconfiguredProvider:
    is_configured = False


# ── /health/ready ────────────────────────────────────────────────────────────

def test_health_ready_503_while_llm_none():
    app = _build_app()
    app.state.llm_provider = None            # warming
    with TestClient(app) as client:
        r = client.get("/api/aria/health/ready")
    assert r.status_code == 503
    body = r.json()
    assert body["ready"] is False and body["llm_ready"] is False
    assert "build_rev" in body


def test_health_ready_200_when_llm_set():
    app = _build_app()
    app.state.llm_provider = object()        # ready — provider initialised
    app.state.rag_ready = True
    with TestClient(app) as client:
        r = client.get("/api/aria/health/ready")
    assert r.status_code == 200
    body = r.json()
    assert body["ready"] is True and body["llm_ready"] is True
    assert body["rag_ready"] is True


def test_health_ready_503_when_initialised_chain_is_exhausted():
    """An object in app.state is not readiness when no provider can serve."""
    app = _build_app()
    app.state.llm_provider = _ExhaustedChain()
    with TestClient(app) as client:
        r = client.get("/api/aria/health/ready")
    assert r.status_code == 503
    body = r.json()
    assert body["ready"] is False
    assert body["llm_ready"] is False
    assert body["llm_reason"] == "llm_unavailable"


@pytest.mark.parametrize(
    ("provider", "reason"),
    [
        (_UnknownHealthChain(), "llm_health_unavailable"),
        (_BareUnconfiguredProvider(), "llm_unavailable"),
    ],
)
def test_health_ready_fails_closed_when_provider_cannot_prove_service(provider, reason):
    app = _build_app()
    app.state.llm_provider = provider
    with TestClient(app) as client:
        r = client.get("/api/aria/health/ready")
    assert r.status_code == 503
    assert r.json()["llm_reason"] == reason


def test_health_ready_not_gated_on_heavy_graphs():
    """Readiness must NOT require the knowledge/neural graphs — chat degrades
    gracefully without them (R-F2201). llm set + graphs cold → still READY."""
    app = _build_app()
    app.state.llm_provider = object()
    app.state.knowledge_ready = False
    app.state.neural_ready = False
    with TestClient(app) as client:
        r = client.get("/api/aria/health/ready")
    assert r.status_code == 200
    assert r.json()["ready"] is True


def test_health_ready_missing_state_reads_not_ready_not_500():
    """No lifespan ran → app.state has no llm_provider at all. The endpoint must
    read that as not-ready (503), never raise a 500 (safe getattr)."""
    app = _build_app()
    with TestClient(app) as client:
        r = client.get("/api/aria/health/ready")
    assert r.status_code == 503
    assert r.json()["ready"] is False


def test_health_ready_is_public_auth_bypass():
    """Fly's readiness probe + WA/web warmup probe send no bearer token — the
    path must bypass require_aria_token like /health/live does."""
    from aria_service.routes.aria import _PUBLIC_AUTH_BYPASS_PATHS
    assert "/api/aria/health/ready" in _PUBLIC_AUTH_BYPASS_PATHS


# ── POST /chat fast-503 (the broken path) ────────────────────────────────────

def test_chat_fast_503_warming_when_llm_none():
    app = _build_app()
    app.state.llm_provider = None            # warming
    with TestClient(app) as client:
        r = client.post("/api/aria/chat", json={"message": "hello"})
    assert r.status_code == 503, f"expected fast warming-503, got {r.status_code}: {r.text[:200]}"
    detail = r.json()["detail"]
    assert detail["error"] == "warming_up"
    assert r.headers.get("Retry-After") == "5"


def test_chat_async_mode_also_fast_503_when_warming():
    """The guard sits BEFORE the async_mode branch, so an async caller (WhatsApp)
    gets the honest 503 too — no un-runnable background job is registered."""
    app = _build_app()
    app.state.llm_provider = None
    with TestClient(app) as client:
        r = client.post("/api/aria/chat",
                        json={"message": "review this", "async_mode": True})
    assert r.status_code == 503
    assert r.json()["detail"]["error"] == "warming_up"


def test_chat_fast_503_when_initialised_chain_is_exhausted():
    """Drive the real chat entry point; dead capacity must accept no job."""
    app = _build_app()
    app.state.llm_provider = _ExhaustedChain()
    with TestClient(app) as client:
        r = client.post("/api/aria/chat", json={"message": "analyse this"})
    assert r.status_code == 503
    detail = r.json()["detail"]
    assert detail["error"] == "llm_unavailable"
    assert detail["cooling_providers"][0]["reason"] == "billing"
    assert r.headers["Retry-After"] == "60"


def test_chat_async_accepts_no_job_when_chain_is_exhausted():
    app = _build_app()
    app.state.llm_provider = _ExhaustedChain()
    with TestClient(app) as client:
        r = client.post(
            "/api/aria/chat",
            json={"message": "analyse this", "async_mode": True},
        )
    assert r.status_code == 503
    assert r.json()["detail"]["error"] == "llm_unavailable"


# ── POST /chat/stream fast-503 (§13 mirror) ──────────────────────────────────

def test_chat_stream_fast_503_warming_when_llm_none():
    app = _build_app()
    app.state.llm_provider = None
    with TestClient(app) as client:
        r = client.post("/api/aria/chat/stream", json={"message": "hello"})
    assert r.status_code == 503, f"§13: stream must mirror chat's warming-503, got {r.status_code}"
    assert r.json()["detail"]["error"] == "warming_up"


def test_chat_stream_fast_503_when_chain_is_exhausted():
    app = _build_app()
    app.state.llm_provider = _ExhaustedChain()
    with TestClient(app) as client:
        r = client.post("/api/aria/chat/stream", json={"message": "analyse this"})
    assert r.status_code == 503
    assert r.json()["detail"]["error"] == "llm_unavailable"
