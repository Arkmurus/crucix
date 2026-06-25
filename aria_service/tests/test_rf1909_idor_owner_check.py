"""R-F1909 (G3 vaccine) — the incomplete-owner-check (IDOR) gene must not regress.

R-F1826 added an owner check to ONE extraction route (get_extraction); its
mutating/listing siblings were missed, leaving live cross-tenant IDORs:
  - verify_extraction  (IDOR write — flips the DD pre-run gate)
  - correct_extraction (IDOR write — overwrite another tenant's fields)
  - recent_extractions (IDOR read — leak every tenant's filenames + ids)
and the WhatsApp account routes had no owner scoping at all (QR-hijack).

These tests drive the REAL owner-check functions with two tenants and assert
cross-tenant access is denied AND the target record is NOT mutated, plus a
source-pin that every WA account route enforces ownership.
"""
from __future__ import annotations

import pathlib

import pytest

from aria_service.intel import document_corrections as dc


@pytest.fixture
def store(monkeypatch):
    s = {"extractions": {
        "idA": {"id": "idA", "user_id": "userA", "filename": "a.pdf",
                "structured_current": {}, "updated_at": "2026-01-02"},
        "idB": {"id": "idB", "user_id": "userB", "filename": "b.pdf",
                "structured_current": {}, "updated_at": "2026-01-01"},
        "idLegacy": {"id": "idLegacy", "filename": "legacy.pdf",
                     "structured_current": {}, "updated_at": "2026-01-03"},
    }}

    async def fake_load():
        return s

    async def fake_save(_x):
        return None

    monkeypatch.setattr(dc, "_load", fake_load)
    monkeypatch.setattr(dc, "_save", fake_save)
    return s


async def test_get_extraction_owner_enforced(store):
    assert await dc.get_extraction("idB", user_id="userA") is None      # other tenant
    got = await dc.get_extraction("idA", user_id="userA")
    assert got and got["id"] == "idA"                                    # own record


async def test_verify_extraction_idor_blocked_and_no_mutation(store):
    r = await dc.verify_extraction("idB", "attacker", user_id="userA")
    assert r is None, "cross-tenant verify must be denied"
    assert "verifications" not in store["extractions"]["idB"], "record must NOT be mutated"
    # the legitimate owner can still verify
    ok = await dc.verify_extraction("idB", "userB", user_id="userB")
    assert ok and len(ok["verifications"]) == 1


async def test_correct_extraction_idor_blocked_and_no_mutation(store):
    r = await dc.correct_extraction("idB", "company_name", "HACKED", "attacker", user_id="userA")
    assert r is None, "cross-tenant correct must be denied"
    assert store["extractions"]["idB"]["structured_current"] == {}, "fields must NOT be overwritten"
    ok = await dc.correct_extraction("idB", "company_name", "RealCo", "userB", user_id="userB")
    assert ok and ok["structured_current"]["company_name"] == "RealCo"


async def test_recent_extractions_owner_scoped(store):
    ids = {e["id"] for e in await dc.recent_extractions(user_id="userA")}
    assert "idB" not in ids, "must not list another tenant's extraction"
    assert "idA" in ids and "idLegacy" in ids, "own + legacy/ownerless records shown"
    # admin / internal (user_id=None) sees everything
    all_ids = {e["id"] for e in await dc.recent_extractions(user_id=None)}
    assert {"idA", "idB", "idLegacy"} <= all_ids


def test_wa_account_routes_enforce_ownership_sourcepin():
    """Source-pin: the WhatsApp listener account routes (get/:id, /:id/qr,
    delete) must owner-gate via `_waOwns`, and create must record the owner via
    `_waUser`. (The listener mjs starts a server on import, so a source-pin is
    the reliable regression guard for this fix.)"""
    repo = pathlib.Path(__file__).resolve().parents[2]
    src = (repo / "services" / "wa-listener" / "aria_wa_listener.mjs").read_text(
        encoding="utf-8", errors="ignore")
    assert src.count("_waOwns(account, req)") >= 3, \
        "WA get/:id, /:id/qr, delete must each call _waOwns(account, req)"
    assert "_createAccount(accountId, name || accountId, _waUser(req))" in src, \
        "WA account create must record the owner via _waUser(req)"
    assert "ownerUserId" in src, "account must carry an ownerUserId field"
