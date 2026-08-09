"""R-F2401 / R-F2402 — DD watchlist + by-id ACL cross-tenant (GDPR) scoping.

Operator directive (2026-07-04): "ensure you don't mix users' DD reports — that
would be a GDPR disaster; ensure there is no data leak between users."

These are the no-leak capability tests for the 5 findings surfaced by the DD
deep-dive. Every one drives the ACTUAL scoping function and asserts the
user-visible outcome: a user sees ONLY their own data, and every scoped path
FAILS CLOSED (a user who owns nothing sees nothing; owner-less data is hidden
from arbitrary users unless the configured legacy operator explicitly claims it).
"""
from __future__ import annotations

import asyncio
import json

import pytest

from aria_service.intel import dd_orchestrator as dd
from aria_service.routes import aria as routes


class _FakeRS:
    def __init__(self, initial=None):
        self.store = dict(initial or {})
        self.lists = {}

    async def get_json_strict(self, key):
        # R-F3506 — same answer, strict contract
        return await self.get_json(key)

    async def get_json(self, key):
        return self.store.get(key)

    async def set_json(self, key, value, ex=None):
        self.store[key] = value

    async def get(self, key):
        return None  # no read-until stamps in these tests

    async def lrange(self, key, start, end):
        return list(self.lists.get(key, []))


@pytest.fixture
def rs(monkeypatch):
    from aria_service.intel import redis_store as real_rs
    fake = _FakeRS()
    # R-F3506 — strict watchlist reads must be faked too
    for m in ("get_json", "get_json_strict", "set_json", "get", "lrange"):
        monkeypatch.setattr(real_rs, m, getattr(fake, m))
    return fake


@pytest.fixture(autouse=True)
def _no_operator_env(monkeypatch):
    monkeypatch.delenv("ARIA_DD_LEGACY_OWNER_UID", raising=False)
    monkeypatch.delenv("ARIA_CODER_OPERATOR_USER_ID", raising=False)
    monkeypatch.delenv("ARIA_OPERATOR_EMAIL", raising=False)


# ── #5 add_to_watchlist: per-owner dedup (no cross-tenant collision) ─────────

def test_add_watchlist_two_users_same_name_get_separate_entries(rs):
    # R-F3287 — both are USER adds; that is what this test is about (two
    # tenants who each asked). Creating an entry now requires saying so, and
    # the flag is exactly what the "Add Entity" route passes. The per-owner
    # dedup property being asserted here is unchanged.
    asyncio.run(dd.add_to_watchlist({"name": "Acme", "user_id": "alice"},
                                    requested_by_user=True))
    asyncio.run(dd.add_to_watchlist({"name": "Acme", "user_id": "bob"},
                                    requested_by_user=True))
    wl = rs.store[dd.WATCHLIST_KEY]
    owners = sorted(w.get("user_id") for w in wl if (w.get("name") == "Acme"))
    assert owners == ["alice", "bob"], f"each tenant needs their own entry, got {owners}"


# ── #2 remove_from_watchlist: owner-scoped (IDOR closed) ─────────────────────

def test_remove_watchlist_cannot_delete_other_users_entry(rs):
    rs.store[dd.WATCHLIST_KEY] = [
        {"name": "Acme", "user_id": "alice", "user_email_domain": "a.com"},
    ]
    # bob (different tenant) tries to delete alice's entry
    out = asyncio.run(dd.remove_from_watchlist("Acme", user_id="bob", user_email_domain="b.com"))
    assert out["removed"] == 0, "a user must not delete another tenant's watchlist entry"
    assert len(rs.store[dd.WATCHLIST_KEY]) == 1


def test_remove_watchlist_owner_can_delete_own(rs):
    rs.store[dd.WATCHLIST_KEY] = [{"name": "Acme", "user_id": "alice"}]
    out = asyncio.run(dd.remove_from_watchlist("Acme", user_id="alice"))
    assert out["removed"] == 1


def test_remove_watchlist_ownerless_hidden_without_operator(rs):
    """No legacy operator configured → nobody scoped can delete an owner-less
    entry (fail closed; can't grief-delete unowned data)."""
    rs.store[dd.WATCHLIST_KEY] = [{"name": "Ghost", "user_id": None}]
    out = asyncio.run(dd.remove_from_watchlist("Ghost", user_id="someone"))
    assert out["removed"] == 0


def test_remove_watchlist_admin_unrestricted(rs):
    """Empty user_id = internal/admin (e.g. the re-screen self-purge) → unrestricted."""
    rs.store[dd.WATCHLIST_KEY] = [{"name": "Acme", "user_id": "alice"}]
    out = asyncio.run(dd.remove_from_watchlist("Acme", user_id=""))
    assert out["removed"] == 1


# ── #4 get_watchlist: owner-less reclaim + fail-closed default ───────────────

def test_get_watchlist_reclaims_ownerless_from_report_index(rs):
    """Owner-less watchlist entry is reclaimed via the report index by
    last_dd_run_id → surfaces to the true owner's scoped watchlist."""
    rs.store[dd.REPORT_INDEX_KEY] = [
        {"run_id": "dd_1", "user_id": "alice", "user_email_domain": "a.com"},
    ]
    rs.store[dd.WATCHLIST_KEY] = [
        {"name": "Modirum", "user_id": None, "last_dd_run_id": "dd_1"},
    ]
    out = asyncio.run(dd.get_watchlist(user_id="alice", user_email_domain="a.com"))
    assert [w["name"] for w in out] == ["Modirum"]
    assert rs.store[dd.WATCHLIST_KEY][0]["user_id"] == "alice"  # healed + persisted


def test_get_watchlist_ownerless_hidden_from_stranger_without_operator(rs):
    """GDPR fail-closed: with NO legacy operator and no index owner, an owner-less
    watchlist entry is hidden from an arbitrary scoped user."""
    rs.store[dd.REPORT_INDEX_KEY] = []
    rs.store[dd.WATCHLIST_KEY] = [{"name": "Ghost", "user_id": None, "last_dd_run_id": "none"}]
    out = asyncio.run(dd.get_watchlist(user_id="stranger", user_email_domain="x.com"))
    assert out == [], "owner-less watchlist entry must not leak to an arbitrary user"


def test_get_watchlist_does_not_mix_tenants(rs):
    rs.store[dd.REPORT_INDEX_KEY] = []
    rs.store[dd.WATCHLIST_KEY] = [
        {"name": "Alpha", "user_id": "alice", "user_email_domain": "a.com"},
        {"name": "Beta", "user_id": "bob", "user_email_domain": "b.com"},
    ]
    out = asyncio.run(dd.get_watchlist(user_id="alice", user_email_domain="a.com"))
    assert [w["name"] for w in out] == ["Alpha"], "alice must not see bob's watched entity"


# ── #1 get_watchlist_alerts: owner-scoped (global leak closed) ───────────────

def _seed_alerts(rs, alerts):
    rs.lists[dd.WATCHLIST_ALERTS_KEY] = [json.dumps(a) for a in alerts]


def test_alerts_not_leaked_across_tenants_by_stamped_owner(rs):
    rs.store[dd.WATCHLIST_KEY] = []
    _seed_alerts(rs, [
        {"entity": "Alpha", "user_id": "alice", "user_email_domain": "a.com"},
        {"entity": "Beta", "user_id": "bob", "user_email_domain": "b.com"},
    ])
    out = asyncio.run(dd.get_watchlist_alerts(since_hours=999999, user_id="bob",
                                              user_email_domain="b.com"))
    ents = [a["entity"] for a in out]
    assert ents == ["Beta"], f"bob must see only his alert, got {ents}"


def test_alerts_matched_by_scoped_watchlist_name_for_legacy_alerts(rs):
    """Legacy alerts (no stamped owner) are admitted only if the entity is on the
    CALLER'S scoped watchlist."""
    rs.store[dd.REPORT_INDEX_KEY] = []
    rs.store[dd.WATCHLIST_KEY] = [{"name": "Alpha", "user_id": "alice", "user_email_domain": "a.com"}]
    _seed_alerts(rs, [
        {"entity": "Alpha"},  # legacy, unstamped — alice watches Alpha
        {"entity": "Zeta"},   # legacy, unstamped — nobody in scope watches Zeta
    ])
    out = asyncio.run(dd.get_watchlist_alerts(since_hours=999999, user_id="alice",
                                              user_email_domain="a.com"))
    assert [a["entity"] for a in out] == ["Alpha"]


def test_alerts_failclosed_when_user_watches_nothing(rs):
    """GDPR fail-closed: a user with an empty scoped watchlist and no stamped-owner
    alerts sees NO alerts (pre-fix the GLOBAL list leaked to everyone)."""
    rs.store[dd.REPORT_INDEX_KEY] = []
    rs.store[dd.WATCHLIST_KEY] = []
    _seed_alerts(rs, [{"entity": "Alpha"}, {"entity": "Beta"}])
    out = asyncio.run(dd.get_watchlist_alerts(since_hours=999999, user_id="lonely",
                                              user_email_domain="x.com"))
    assert out == [], "a user who watches nothing must see zero alerts"


def test_alerts_admin_sees_all(rs):
    """Empty user_id = internal/admin (the monitoring loop) → full list."""
    _seed_alerts(rs, [{"entity": "Alpha"}, {"entity": "Beta"}])
    out = asyncio.run(dd.get_watchlist_alerts(since_hours=999999, user_id=""))
    assert {a["entity"] for a in out} == {"Alpha", "Beta"}


def test_unread_count_is_owner_scoped(rs):
    """get_watchlist_unread_count must count ONLY the caller's own alerts — it
    inherits the R-F2401 owner-scoping (pre-fix it counted every tenant's)."""
    rs.store[dd.WATCHLIST_KEY] = []
    _seed_alerts(rs, [
        {"entity": "Alpha", "user_id": "alice", "user_email_domain": "a.com"},
        {"entity": "Beta", "user_id": "bob", "user_email_domain": "b.com"},
        {"entity": "Gamma", "user_id": "bob", "user_email_domain": "b.com"},
    ])
    n_bob = asyncio.run(dd.get_watchlist_unread_count(
        "bob", since_hours=999999, user_email_domain="b.com"))
    assert n_bob == 2, f"bob must count only his 2 alerts, got {n_bob}"
    n_alice = asyncio.run(dd.get_watchlist_unread_count(
        "alice", since_hours=999999, user_email_domain="a.com"))
    assert n_alice == 1, f"alice must count only her 1 alert, got {n_alice}"


# ── #3 R-F2402: by-id report ACL fails CLOSED on owner-less reports ──────────

def test_ownerless_report_failclosed_for_stranger(monkeypatch):
    monkeypatch.delenv("ARIA_DD_LEGACY_OWNER_UID", raising=False)
    monkeypatch.delenv("ARIA_CODER_OPERATOR_USER_ID", raising=False)
    f = routes._dd_report_access_allowed
    assert f({"run_id": "x"}, "stranger", "evil.com") is False

    # R-F3800 — declare the internal tier rather than inheriting it. R-F3628 flipped
    # `_AUTH_INTERNAL_DEFAULT` to False (fail-closed), so an undeclared context is now
    # denied; this line passed only on the old permissive default.
    routes._auth_is_internal_var.set(True)
    assert f({"run_id": "x"}, "", "") is True  # internal bypass unchanged

    # And the half that must never be relaxed: unscoped WITHOUT the internal service
    # token stays closed, even on an owner-less report (R-F2778 + R-F2402 together).
    routes._auth_is_internal_var.set(False)
    assert f({"run_id": "x"}, "", "") is False
    routes._auth_is_internal_var.set(True)


def test_ownerless_report_allowed_for_legacy_operator(monkeypatch):
    monkeypatch.setenv("ARIA_DD_LEGACY_OWNER_UID", "operator123")
    f = routes._dd_report_access_allowed
    assert f({"run_id": "x"}, "operator123", "") is True
    assert f({"run_id": "x"}, "someone_else", "any.com") is False


def test_owned_report_still_owner_and_company_scoped(monkeypatch):
    monkeypatch.delenv("ARIA_DD_LEGACY_OWNER_UID", raising=False)
    f = routes._dd_report_access_allowed
    R = {"user_id": "alice", "user_email_domain": "a.com", "share_to_company": True}
    assert f(R, "alice", "a.com") is True
    assert f(R, "colleague", "a.com") is True          # same company, shared
    assert f(R, "colleague", "evil.com") is False      # different company
    assert f({**R, "share_to_company": False}, "colleague", "a.com") is False  # opted out
