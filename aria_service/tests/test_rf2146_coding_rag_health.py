"""R-F2146 — coding RAG stats exposed in /api/aria/health endpoint.

The health endpoint now includes coding_rag stats (constitutional rules count,
fixes count, failures count, codebase chunks count) alongside the main RAG stats.
"""
from __future__ import annotations

from types import SimpleNamespace


def _make_app():
    from fastapi import FastAPI
    from aria_service.routes import aria as aria_routes

    app = FastAPI()
    app.dependency_overrides[aria_routes._router_auth_dep] = lambda: None
    app.include_router(aria_routes.router)
    app.state.llm_provider = SimpleNamespace(is_configured=True, name="fake")
    app.state.current_data = None
    return app


def test_rf2146_health_contains_coding_rag_key():
    """Health endpoint response includes 'coding_rag' key in infra section."""
    from fastapi.testclient import TestClient
    with TestClient(_make_app()) as c:
        r = c.get("/api/aria/health")
        assert r.status_code in (200, 503), f"Expected 200 or 503, got {r.status_code}"
        data = r.json()
        infra = data.get("infra", {})
        assert "coding_rag" in infra, (
            f"Health response should include 'coding_rag' in infra. "
            f"Got keys: {list(infra.keys())}"
        )


def test_rf2146_coding_rag_stats_is_dict():
    """coding_rag stats is a dict (possibly empty if RAG unavailable)."""
    from fastapi.testclient import TestClient
    with TestClient(_make_app()) as c:
        r = c.get("/api/aria/health")
        data = r.json()
        coding_rag = data.get("infra", {}).get("coding_rag", {})
        assert isinstance(coding_rag, dict), (
            f"coding_rag should be a dict, got {type(coding_rag)}"
        )


def test_rf2146_coding_rag_has_expected_keys_when_ready():
    """When coding RAG is ready, it has the expected collection count keys."""
    from fastapi.testclient import TestClient
    with TestClient(_make_app()) as c:
        r = c.get("/api/aria/health")
        data = r.json()
        coding_rag = data.get("infra", {}).get("coding_rag", {})
        # If ready, it should have the count keys
        if coding_rag.get("ready"):
            assert "total_fixes" in coding_rag, "coding_rag should have total_fixes"
            assert "total_failures" in coding_rag, "coding_rag should have total_failures"
            assert "total_codebase_chunks" in coding_rag, "coding_rag should have total_codebase_chunks"
            assert "total_constitutional_rules" in coding_rag, "coding_rag should have total_constitutional_rules"
