"""R-F2777 (2026-07-18) — public-webmail cross-tenant DD leak.

The R-F608 "same-company" share treats any two users on the SAME email domain
as colleagues who may see each other's DD reports (``share_to_company`` default
True). That is correct for a CORPORATE domain (acme.com) but a CROSS-TENANT
DISCLOSURE for a PUBLIC WEBMAIL domain: two unrelated strangers on gmail.com are
NOT one company, so sharing leaks one user's due-diligence to another.

R-F2777 excludes public-webmail domains from every domain-share decision point
(list_reports, get_watchlist, remove_from_watchlist, and the by-id
``_dd_report_access_allowed``) → a free-webmail user gets OWNER-EXACT access only.

C8 (LIST 1): these tests run against an ISOLATED temp DD vault so the durable
vault (which list_reports reconciles on every read) cannot contaminate the
assertion. The cross-user disclosure tests FAIL against the pre-R-F2777 code
(A@gmail.com sees B@gmail.com's DD) and PASS after the fix.
"""
from __future__ import annotations

import asyncio
import pytest


class _FakeRS:
    def __init__(self):
        self.store: dict = {}

    async def get_json(self, key):
        return self.store.get(key)

    async def set_json(self, key, value, ex=None):
        self.store[key] = value


@pytest.fixture
def isolated_dd(monkeypatch, tmp_path):
    """Isolate BOTH the volatile state_store index (FakeRS) AND the durable DD
    vault (temp SQLite) so no real report can bleed into these assertions."""
    from aria_service.intel import redis_store as real_rs
    from aria_service.intel import dd_vault

    fake = _FakeRS()
    monkeypatch.setattr(real_rs, "get_json", fake.get_json)
    monkeypatch.setattr(real_rs, "set_json", fake.set_json)

    temp_vault = dd_vault.DDVault(db_path=tmp_path / "dd_vault_test.db")
    monkeypatch.setattr(dd_vault, "_vault_instance", temp_vault)
    return fake


# ── The helper itself ──────────────────────────────────────────────────


def test_is_public_webmail_domain_classifier():
    from aria_service.intel.dd_orchestrator import _is_public_webmail_domain

    for d in ("gmail.com", "GMAIL.COM", " outlook.com ", "hotmail.co.uk",
              "yahoo.com", "icloud.com", "proton.me", "gmx.net", "aol.com"):
        assert _is_public_webmail_domain(d) is True, f"{d!r} should be public webmail"
    for d in ("arkmurus.com", "acme.co", "gov.uk", "", None):
        assert _is_public_webmail_domain(d) is False, f"{d!r} should NOT be public webmail"


# ── list_reports: the cross-tenant leak (C8 disclosure test) ────────────


def test_gmail_user_cannot_see_another_gmail_user_dd(isolated_dd):
    """CAPABILITY / DISCLOSURE: userA@gmail.com must NOT see userB@gmail.com's
    company-shared DD. FAILS pre-R-F2777 (leak), PASSES after."""
    from aria_service.intel import dd_orchestrator

    isolated_dd.store[dd_orchestrator.REPORT_INDEX_KEY] = [
        {
            "run_id": "userB-run",
            "user_id": "userB",
            "user_email_domain": "gmail.com",
            "share_to_company": True,  # default share — the leak vector
        },
    ]
    a_view = asyncio.run(
        dd_orchestrator.list_reports(
            limit=50, user_id="userA", user_email_domain="gmail.com"
        )
    )
    assert a_view == [], (
        "CROSS-TENANT LEAK: userA@gmail.com saw userB@gmail.com's DD"
    )


def test_gmail_owner_still_sees_own_dd(isolated_dd):
    """Owner-exact access is unaffected — a webmail user always sees their OWN DD."""
    from aria_service.intel import dd_orchestrator

    isolated_dd.store[dd_orchestrator.REPORT_INDEX_KEY] = [
        {
            "run_id": "userB-run",
            "user_id": "userB",
            "user_email_domain": "gmail.com",
            "share_to_company": True,
        },
    ]
    b_view = asyncio.run(
        dd_orchestrator.list_reports(
            limit=50, user_id="userB", user_email_domain="gmail.com"
        )
    )
    assert {r["run_id"] for r in b_view} == {"userB-run"}


def test_corporate_domain_still_shares(isolated_dd):
    """REGRESSION GUARD: a real corporate domain must STILL company-share
    (R-F608 behaviour preserved for non-webmail domains)."""
    from aria_service.intel import dd_orchestrator

    isolated_dd.store[dd_orchestrator.REPORT_INDEX_KEY] = [
        {
            "run_id": "alice-run",
            "user_id": "alice",
            "user_email_domain": "arkmurus.com",
            "share_to_company": True,
        },
    ]
    bob_view = asyncio.run(
        dd_orchestrator.list_reports(
            limit=50, user_id="bob", user_email_domain="arkmurus.com"
        )
    )
    assert {r["run_id"] for r in bob_view} == {"alice-run"}, (
        "corporate-domain colleague share must survive R-F2777"
    )


# ── by-id access (view/delete) ──────────────────────────────────────────


def test_report_access_by_id_blocks_webmail_stranger():
    from aria_service.routes.aria import _dd_report_access_allowed

    b_report = {
        "user_id": "userB",
        "user_email_domain": "gmail.com",
        "share_to_company": True,
    }
    # stranger on the same webmail domain — DENIED
    assert _dd_report_access_allowed(b_report, "userA", "gmail.com") is False
    # owner — ALLOWED
    assert _dd_report_access_allowed(b_report, "userB", "gmail.com") is True
    # corporate colleague — still ALLOWED (regression guard)
    corp = {"user_id": "alice", "user_email_domain": "arkmurus.com",
            "share_to_company": True}
    assert _dd_report_access_allowed(corp, "bob", "arkmurus.com") is True


# ── watchlist read scoping ──────────────────────────────────────────────


def test_watchlist_webmail_not_shared(isolated_dd):
    from aria_service.intel import dd_orchestrator

    isolated_dd.store[dd_orchestrator.WATCHLIST_KEY] = [
        {
            "name": "Acme Ltd",
            "user_id": "userB",
            "user_email_domain": "gmail.com",
            "share_to_company": True,
        },
    ]
    a_wl = asyncio.run(
        dd_orchestrator.get_watchlist(user_id="userA", user_email_domain="gmail.com")
    )
    assert a_wl == [], "webmail stranger saw another user's watchlist entry"
