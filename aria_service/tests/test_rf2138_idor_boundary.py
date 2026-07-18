"""R-F2138 / R-F2456 — IDOR boundary fix: token-type-aware user_id scoping,
bound per-REQUEST.

The leak was NOT in _dd_owned_entity_ids itself (which correctly returns None
for empty user_id = unrestricted for internal callers). The leak was that
EXTERNAL callers (user-facing API token) could reach it with an empty user_id
and get unrestricted access. Fix (R-F2138): require_aria_token records whether
the internal token authenticated; _dd_owned_entity_ids returns empty set (deny)
when an external caller has no user_id.

R-F2456: that flag was a module GLOBAL — process-wide and shared across all
concurrent requests, so a request's value could be raced/overwritten by a
concurrent request across an await boundary (fail-OPEN cross-tenant read). It is
now a ContextVar bound to the request's task context. These tests drive the flag
through an ASYNC override dep (mirroring the production async _router_auth_dep,
which is what makes the ContextVar.set propagate to the endpoint), and add a
deterministic isolation test proving two interleaved requests can't clobber each
other.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace


def _make_app(internal: bool = True):
    from fastapi import FastAPI
    from aria_service.routes import aria as aria_routes

    app = FastAPI()

    # R-F2456: async override that SETS the per-request ContextVar, exactly as the
    # production async _router_auth_dep does. An async dep runs in the request task
    # context so the .set() propagates to the endpoint + _dd_owned_entity_ids.
    async def _auth_override():
        aria_routes._auth_is_internal_var.set(internal)

    app.dependency_overrides[aria_routes._router_auth_dep] = _auth_override
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
        def get_cross_references(self, cid, **_kw):   # R-F2697 ACL kwargs
            return []
        def get_related_cases(self, cid, **_kw):      # R-F2697 ACL kwargs
            return []
        def delete_case(self, cid):
            return True

    monkeypatch.setattr(dd_orchestrator, "list_reports", _fake_list_reports)
    monkeypatch.setattr(dd_orchestrator, "get_case_file", _fake_get_case_file)
    monkeypatch.setattr(dd_vault, "get_vault", lambda: _FakeVault())


def test_rf2138_external_token_no_user_id_denied(monkeypatch):
    """External (API token) caller with no user_id → empty set (deny)."""
    _patch(monkeypatch)
    from fastapi.testclient import TestClient
    with TestClient(_make_app(internal=False)) as c:
        r = c.get("/api/aria/dd/vault/search?q=co")
        assert r.status_code == 200
        data = r.json()
        assert data["entries"] == [], (
            f"Expected empty entries for external caller with no user_id, got {data['entries']}"
        )


def test_rf2138_internal_token_no_user_id_unrestricted(monkeypatch):
    """Internal (service token) caller with no user_id → unrestricted (keep)."""
    _patch(monkeypatch)
    from fastapi.testclient import TestClient
    with TestClient(_make_app(internal=True)) as c:
        r = c.get("/api/aria/dd/vault/search?q=co")
        assert r.status_code == 200
        data = r.json()
        ids = {e["canonical_entity_id"] for e in data["entries"]}
        assert ids == {OWNED, OTHER}, (
            f"Expected all entities for internal caller with no user_id, got {ids}"
        )


def test_rf2138_external_token_with_user_id_works(monkeypatch):
    """External caller WITH a user_id → normal scoping applies."""
    _patch(monkeypatch)
    from fastapi.testclient import TestClient
    with TestClient(_make_app(internal=False)) as c:
        r = c.get("/api/aria/dd/vault/search?q=co&user_id=alice")
        assert r.status_code == 200
        data = r.json()
        ids = {e["canonical_entity_id"] for e in data["entries"]}
        assert ids == {OWNED}, f"Expected only OWNED for alice, got {ids}"


def test_rf2138_dd_case_external_no_user_id_denied(monkeypatch):
    """External caller with no user_id → dd/case returns 404 (deny)."""
    _patch(monkeypatch)
    from fastapi.testclient import TestClient
    with TestClient(_make_app(internal=False)) as c:
        r = c.get(f"/api/aria/dd/case/{OWNED}")
        assert r.status_code == 404, (
            f"Expected 404 for external caller with no user_id, got {r.status_code}"
        )


def test_rf2138_dd_case_internal_no_user_id_unrestricted(monkeypatch):
    """Internal caller with no user_id → dd/case returns 200 (unrestricted)."""
    _patch(monkeypatch)
    from fastapi.testclient import TestClient
    with TestClient(_make_app(internal=True)) as c:
        r = c.get(f"/api/aria/dd/case/{OWNED}")
        assert r.status_code == 200, (
            f"Expected 200 for internal caller with no user_id, got {r.status_code}"
        )


def test_rf2456_contextvar_isolates_concurrent_requests():
    """R-F2456 THE RACE FIX: two interleaved requests with different token types
    must NOT clobber each other's auth flag. Each asyncio Task copies the current
    context, so _auth_is_internal_var.set() inside one task cannot leak into the
    other — with the old module global, the interleaving `await` would clobber it
    and the external caller would read the internal caller's True (unrestricted).
    """
    from aria_service.routes import aria as aria_routes

    async def as_internal():
        aria_routes._auth_is_internal_var.set(True)
        await asyncio.sleep(0)  # yield — the external task runs here
        return await aria_routes._dd_owned_entity_ids("")  # None == unrestricted

    async def as_external():
        aria_routes._auth_is_internal_var.set(False)
        await asyncio.sleep(0)  # yield — the internal task runs here
        return await aria_routes._dd_owned_entity_ids("")  # set() == deny

    async def _run():
        ti = asyncio.create_task(as_internal())
        te = asyncio.create_task(as_external())
        return await asyncio.gather(ti, te)

    internal_res, external_res = asyncio.run(_run())
    assert internal_res is None, f"internal caller must stay unrestricted, got {internal_res!r}"
    assert external_res == set(), f"external caller must stay denied despite interleave, got {external_res!r}"


# ── R-F2778 (R-F2138 residual) — the by-id + report-index surfaces ──────────
# The vault/search + dd/case surfaces above were scoped in R-F2138, but the DD
# report INDEX (GET /dd/reports) and the by-id access helper still fell OPEN for an
# external token with no user_id. R-F2778 mirrors the fail-closed onto both.


def test_rf2778_reports_index_external_no_user_id_empty(monkeypatch):
    """CAPABILITY: external (API-token) caller with no user_id → GET /dd/reports
    returns NO reports (was: every tenant's reports via the admin no-filter path)."""
    _patch(monkeypatch)
    from fastapi.testclient import TestClient
    with TestClient(_make_app(internal=False)) as c:
        r = c.get("/api/aria/dd/reports")
        assert r.status_code == 200
        assert r.json()["reports"] == [], (
            f"external caller with no user_id must see no reports, got {r.json()['reports']}"
        )


def test_rf2778_reports_index_internal_no_user_id_unrestricted(monkeypatch):
    """Internal (service-token) caller with no user_id → full index (unchanged)."""
    _patch(monkeypatch)
    from fastapi.testclient import TestClient
    with TestClient(_make_app(internal=True)) as c:
        r = c.get("/api/aria/dd/reports")
        assert r.status_code == 200
        ids = {e["canonical_entity_id"] for e in r.json()["reports"]}
        assert ids == {OWNED, OTHER}, (
            f"internal caller with no user_id must see the full index, got {ids}"
        )


def test_rf2778_access_allowed_external_no_user_id_denied():
    """_dd_report_access_allowed: external token + empty user_id → DENY (view/delete
    by id). Internal token + empty user_id → ALLOW. Bypass default (True) → ALLOW."""
    from aria_service.routes import aria as aria_routes

    report = {"user_id": "someone", "user_email_domain": "x.com"}

    async def _external():
        aria_routes._auth_is_internal_var.set(False)
        return aria_routes._dd_report_access_allowed(report, "", "")

    async def _internal():
        aria_routes._auth_is_internal_var.set(True)
        return aria_routes._dd_report_access_allowed(report, "", "")

    assert asyncio.run(_external()) is False, "external no-user_id must be denied by id"
    assert asyncio.run(_internal()) is True, "internal no-user_id must stay unrestricted"


if __name__ == "__main__":
    test_rf2456_contextvar_isolates_concurrent_requests()
    print("PASS test_rf2456_contextvar_isolates_concurrent_requests")
    print("(pytest-driven tests require pytest fixtures; run via pytest for the TestClient cases)")
