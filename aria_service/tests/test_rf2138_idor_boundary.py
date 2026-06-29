"""R-F2138 — IDOR boundary fix: token-type-aware user_id scoping.

The leak was NOT in _dd_owned_entity_ids itself (which correctly returns None
for empty user_id = unrestricted for internal callers). The leak was that
EXTERNAL callers (user-facing API token) could reach it with an empty user_id
and get unrestricted access. Fix: require_aria_token now sets _AUTH_IS_INTERNAL
module-level flag, and _dd_owned_entity_ids returns empty set (deny) when an
external caller has no user_id.

Capability test drives the REAL routes via TestClient with the auth dependency
overridden to simulate both token types by setting _AUTH_IS_INTERNAL directly.
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


OWNED = "company_GB_OWNED"
OTHER = "company_GB_OTHER"


def _patch(monkeypatch):
    from aria_service.intel import dd_orchestrator
    from aria_service.intel import dd_vault

    async def _fake_list_reports(limit=50, *, user_id=None, user_email_domain=None):
        if user_id == "alice":
            return [{"canonical_entity_id": OWNED, "user_id": "alice"}]
        if user_id == "bob":
            return []
        return [{"canonical_entity_id": OWNED}, {"canonical_entity_id": OTHER}]

    async def _fake_get_case_file(cid, *, include_reports=False):
        return {"canonical_entity_id": cid, "total_versions": 2, "versions": [{}, {}]}

    class _FakeVault:
        def search(self, q, limit=50):
            return [{"canonical_entity_id": OWNED, "entity_name": "Owned Co"},
                    {"canonical_entity_id": OTHER, "entity_name": "Secret Co"}]
        def list_all(self, limit=100):
            return self.search("", limit)
        def get_case(self, cid):
            return {"canonical_entity_id": cid, "entity_name": "X"}
        def get_cross_references(self, cid):
            return []
        def get_related_cases(self, cid):
            return []
        def delete_case(self, cid):
            return True

    monkeypatch.setattr(dd_orchestrator, "list_reports", _fake_list_reports)
    monkeypatch.setattr(dd_orchestrator, "get_case_file", _fake_get_case_file)
    monkeypatch.setattr(dd_vault, "get_vault", lambda: _FakeVault())


def test_rf2138_external_token_no_user_id_denied(monkeypatch):
    """External (API token) caller with no user_id → empty set (deny).

    This is the core fix: an external caller without a user_id must NOT
    see everything. Previously, empty user_id returned None (unrestricted).
    """
    from aria_service.routes import aria as aria_routes
    _patch(monkeypatch)

    # Simulate external (API token) auth
    monkeypatch.setattr(aria_routes, "_AUTH_IS_INTERNAL", False)

    from fastapi.testclient import TestClient
    with TestClient(_make_app()) as c:
        r = c.get("/api/aria/dd/vault/search?q=co")
        assert r.status_code == 200
        data = r.json()
        # External caller with no user_id → empty results (deny)
        assert data["entries"] == [], (
            f"Expected empty entries for external caller with no user_id, got {data['entries']}"
        )


def test_rf2138_internal_token_no_user_id_unrestricted(monkeypatch):
    """Internal (service token) caller with no user_id → unrestricted (keep).

    Internal callers (WA listener, web proxy, CLI) carry the internal token
    and keep the existing unrestricted behaviour.
    """
    from aria_service.routes import aria as aria_routes
    _patch(monkeypatch)

    # Simulate internal (service token) auth
    monkeypatch.setattr(aria_routes, "_AUTH_IS_INTERNAL", True)

    from fastapi.testclient import TestClient
    with TestClient(_make_app()) as c:
        r = c.get("/api/aria/dd/vault/search?q=co")
        assert r.status_code == 200
        data = r.json()
        ids = {e["canonical_entity_id"] for e in data["entries"]}
        assert ids == {OWNED, OTHER}, (
            f"Expected all entities for internal caller with no user_id, got {ids}"
        )


def test_rf2138_external_token_with_user_id_works(monkeypatch):
    """External caller WITH a user_id → normal scoping applies."""
    from aria_service.routes import aria as aria_routes
    _patch(monkeypatch)

    monkeypatch.setattr(aria_routes, "_AUTH_IS_INTERNAL", False)

    from fastapi.testclient import TestClient
    with TestClient(_make_app()) as c:
        # alice owns only OWNED
        r = c.get("/api/aria/dd/vault/search?q=co&user_id=alice")
        assert r.status_code == 200
        data = r.json()
        ids = {e["canonical_entity_id"] for e in data["entries"]}
        assert ids == {OWNED}, (
            f"Expected only OWNED for alice, got {ids}"
        )


def test_rf2138_dd_case_external_no_user_id_denied(monkeypatch):
    """External caller with no user_id → dd/case returns 404 (deny)."""
    from aria_service.routes import aria as aria_routes
    _patch(monkeypatch)

    monkeypatch.setattr(aria_routes, "_AUTH_IS_INTERNAL", False)

    from fastapi.testclient import TestClient
    with TestClient(_make_app()) as c:
        r = c.get(f"/api/aria/dd/case/{OWNED}")
        # External caller with no user_id → _dd_owned_entity_ids returns empty set
        # → OWNED not in owned → 404
        assert r.status_code == 404, (
            f"Expected 404 for external caller with no user_id, got {r.status_code}"
        )


def test_rf2138_dd_case_internal_no_user_id_unrestricted(monkeypatch):
    """Internal caller with no user_id → dd/case returns 200 (unrestricted)."""
    from aria_service.routes import aria as aria_routes
    _patch(monkeypatch)

    monkeypatch.setattr(aria_routes, "_AUTH_IS_INTERNAL", True)

    from fastapi.testclient import TestClient
    with TestClient(_make_app()) as c:
        r = c.get(f"/api/aria/dd/case/{OWNED}")
        assert r.status_code == 200, (
            f"Expected 200 for internal caller with no user_id, got {r.status_code}"
        )
