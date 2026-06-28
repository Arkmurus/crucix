"""R-F2097 — DD vault/case per-user ownership (closes the cross-tenant IDOR).

2026-06-28 full DD found (live-confirmed): dd/case + dd/vault/search + dd/vault/case
had no ownership check and dd_cases has no owner column, so any token-holder (and,
via the web catch-all, any signed-up viewer) could read/delete every tenant's DD
cases. Fix: scope via the user's OWNED report index (the ownership oracle) —
empty user_id = internal/admin = unrestricted; a real user sees only entities they
(or a same-domain colleague) have a report for.

Capability test drives the REAL routes via TestClient with list_reports + the vault
monkeypatched, asserting cross-tenant access 404s / is filtered while the owner +
the admin (no user_id) still get through.
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
        # alice owns OWNED; bob owns nothing; no user_id (admin) → both (unused here)
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


def test_rf2097_dd_case_cross_tenant_404(monkeypatch):
    from fastapi.testclient import TestClient
    _patch(monkeypatch)
    with TestClient(_make_app()) as c:
        # Owner gets it
        assert c.get(f"/api/aria/dd/case/{OWNED}?user_id=alice").status_code == 200
        # Non-owner (bob) is 404 — no cross-tenant read, no existence leak
        assert c.get(f"/api/aria/dd/case/{OWNED}?user_id=bob").status_code == 404
        # Admin / internal (no user_id) is unrestricted
        assert c.get(f"/api/aria/dd/case/{OWNED}").status_code == 200


def test_rf2097_dd_vault_search_filtered(monkeypatch):
    from fastapi.testclient import TestClient
    _patch(monkeypatch)
    with TestClient(_make_app()) as c:
        # alice owns only OWNED → OTHER filtered out
        r = c.get("/api/aria/dd/vault/search?q=co&user_id=alice").json()
        ids = {e["canonical_entity_id"] for e in r["entries"]}
        assert ids == {OWNED}, ids
        # bob owns nothing → empty
        rb = c.get("/api/aria/dd/vault/search?q=co&user_id=bob").json()
        assert rb["entries"] == [], rb
        # admin (no user_id) → both
        ra = c.get("/api/aria/dd/vault/search?q=co").json()
        assert {e["canonical_entity_id"] for e in ra["entries"]} == {OWNED, OTHER}


def test_rf2097_dd_vault_case_and_delete_scoped(monkeypatch):
    from fastapi.testclient import TestClient
    _patch(monkeypatch)
    with TestClient(_make_app()) as c:
        # bob cannot read or delete OWNED (not his)
        assert c.get(f"/api/aria/dd/vault/case/{OWNED}?user_id=bob").json()["success"] is False
        assert c.delete(f"/api/aria/dd/vault/case/{OWNED}?user_id=bob").json()["success"] is False
        # alice (owner) can
        assert c.get(f"/api/aria/dd/vault/case/{OWNED}?user_id=alice").json()["success"] is True
        assert c.delete(f"/api/aria/dd/vault/case/{OWNED}?user_id=alice").json()["success"] is True
