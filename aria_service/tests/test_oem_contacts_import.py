"""Tests for the 2026-04-26 OEM contact graph import endpoints.

The OEM_GRAPH_ENRICH_WEEKLY autonomous task is a scan-and-report stub —
populating the 60 anchor-role slots needs human-curated data. These
endpoints close the operator-pending gap (#10 in the next-session TODO)
by giving operator a direct way to feed DSEI/Eurosatory data without
writing Redis by hand.
"""
from __future__ import annotations

import asyncio
import json


def _run(coro):
    return asyncio.run(coro)


def _setup_fake_redis(monkeypatch):
    from aria_service.intel import redis_store as rs
    store: dict[str, str] = {}

    async def fake_get_json(key):
        v = store.get(key)
        return json.loads(v) if v else None

    async def fake_set_json(key, obj, ex=None):
        store[key] = json.dumps(obj, default=str)

    monkeypatch.setattr(rs, "get_json", fake_get_json)
    monkeypatch.setattr(rs, "set_json", fake_set_json)
    return store


def test_add_contact_persists_full_record(monkeypatch):
    _setup_fake_redis(monkeypatch)
    from aria_service.intel import oem_contact_graph as oem

    cid = _run(oem.add_contact({
        "oem": "Leonardo",
        "oem_country_hq": "IT",
        "role_slot": "VP International Sales",
        "name": "Mario Rossi",
        "linkedin_url": "https://linkedin.com/in/mariorossi",
        "email": "m.rossi@leonardo.com",
        "region_focus": "AFRICA",
        "seniority": "VP",
        "source": "DSEI 2026 speaker list",
        "confidence": "HIGH",
        "notes": "Met at Mozambique panel",
    }))
    assert cid

    contacts = _run(oem.get_oem_contacts(oem="Leonardo", filled_only=True))
    rossi = [c for c in contacts if c.get("name") == "Mario Rossi"]
    assert len(rossi) == 1
    assert rossi[0]["region_focus"] == "AFRICA"
    assert rossi[0]["confidence"] == "HIGH"
    assert rossi[0]["source"] == "DSEI 2026 speaker list"


def test_add_contact_dedupes_by_oem_role_name(monkeypatch):
    """Re-importing the same person (same oem + role_slot + name) must
    UPDATE the existing record, not create a duplicate. Operator can
    re-run an import after correcting a typo without flooding the graph."""
    _setup_fake_redis(monkeypatch)
    from aria_service.intel import oem_contact_graph as oem

    cid1 = _run(oem.add_contact({
        "oem": "Rheinmetall",
        "oem_country_hq": "DE",
        "role_slot": "Head BD MENA",
        "name": "Hans Schmidt",
        "email": "old@rheinmetall.com",
    }))
    cid2 = _run(oem.add_contact({
        "oem": "Rheinmetall",
        "oem_country_hq": "DE",
        "role_slot": "Head BD MENA",
        "name": "Hans Schmidt",
        "email": "new@rheinmetall.com",  # corrected
    }))
    assert cid1 == cid2  # same composite key

    contacts = _run(oem.get_oem_contacts(oem="Rheinmetall", filled_only=True))
    schmidt = [c for c in contacts if c.get("name") == "Hans Schmidt"]
    assert len(schmidt) == 1, "duplicate detected — dedup logic broke"
    assert schmidt[0]["email"] == "new@rheinmetall.com"


def test_coverage_report_distinguishes_filled_vs_placeholder(monkeypatch):
    _setup_fake_redis(monkeypatch)
    from aria_service.intel import oem_contact_graph as oem

    contacts_before = _run(oem.get_oem_contacts())
    placeholder_count = sum(1 for c in contacts_before if not c.get("name"))
    # Bootstrap should have seeded 60 placeholders
    assert placeholder_count >= 50, "bootstrap seed missing — fewer than 50 placeholders"

    # Fill in 2 contacts
    _run(oem.add_contact({
        "oem": "Leonardo", "oem_country_hq": "IT",
        "role_slot": "VP International Sales", "name": "Mario Rossi",
    }))
    _run(oem.add_contact({
        "oem": "KNDS", "oem_country_hq": "FR",
        "role_slot": "Director Africa", "name": "Marie Dubois",
    }))

    contacts_after = _run(oem.get_oem_contacts())
    filled_count = sum(1 for c in contacts_after if c.get("name"))
    assert filled_count >= 2, "filled-in contacts not visible in list"


def test_import_endpoint_bulk_upsert(monkeypatch):
    """The import endpoint must accept a batch and return the counts
    expected by an operator running a CSV→JSON conversion."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from aria_service.routes import aria as aria_routes

    _setup_fake_redis(monkeypatch)
    app = FastAPI()
    # Bypass the bearer-token auth dependency for the test — we're
    # exercising the route handlers, not the auth layer.
    app.dependency_overrides[aria_routes._router_auth_dep] = lambda: None
    app.include_router(aria_routes.router)
    client = TestClient(app)

    payload = {
        "contacts": [
            {"oem": "Leonardo", "oem_country_hq": "IT",
             "role_slot": "VP International Sales", "name": "Mario Rossi"},
            {"oem": "KNDS", "oem_country_hq": "FR",
             "role_slot": "Director Africa", "name": "Marie Dubois"},
            # Missing required field — must be skipped, not crash
            {"oem": "Saab", "role_slot": "BD"},  # no name
        ],
    }
    r = client.post("/api/aria/oem/contacts/import", json=payload)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["imported"] == 2
    assert body["skipped"] == 1
    assert len(body["ids"]) == 2
    assert body["skipped_detail"][0]["reason"].startswith("missing required field")


def test_import_endpoint_rejects_empty_body(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from aria_service.routes import aria as aria_routes

    _setup_fake_redis(monkeypatch)
    app = FastAPI()
    # Bypass the bearer-token auth dependency for the test — we're
    # exercising the route handlers, not the auth layer.
    app.dependency_overrides[aria_routes._router_auth_dep] = lambda: None
    app.include_router(aria_routes.router)
    client = TestClient(app)

    r = client.post("/api/aria/oem/contacts/import", json={})
    assert r.status_code == 400


def test_coverage_endpoint_returns_per_oem_breakdown(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from aria_service.routes import aria as aria_routes

    _setup_fake_redis(monkeypatch)
    app = FastAPI()
    # Bypass the bearer-token auth dependency for the test — we're
    # exercising the route handlers, not the auth layer.
    app.dependency_overrides[aria_routes._router_auth_dep] = lambda: None
    app.include_router(aria_routes.router)
    client = TestClient(app)

    # Fill one slot for Leonardo
    client.post("/api/aria/oem/contacts/import", json={
        "contacts": [{
            "oem": "Leonardo", "oem_country_hq": "IT",
            "role_slot": "VP International Sales", "name": "Mario Rossi",
        }],
    })

    r = client.get("/api/aria/oem/coverage")
    assert r.status_code == 200
    body = r.json()
    assert body["total_slots"] >= 60
    assert body["filled_slots"] >= 1
    assert body["coverage_pct"] > 0
    assert "Leonardo" in body["by_oem"]
    assert body["by_oem"]["Leonardo"]["filled"] >= 1
