"""R-F2382 — DD reports rebuilt from the vault (which has no user_id column)
are owner-less and vanish from every user's scoped list. list_reports must fall
back to the configured operator so the primary user keeps seeing their reports.

Capability test: drives the REAL list_reports() with an empty index + an
owner-less vault, and asserts the operator sees the reports while another user
does not.
"""
import asyncio

import pytest

from aria_service.intel import dd_orchestrator as d


class _FakeVault:
    def __init__(self, cases):
        self._cases = cases

    def list_all(self, limit=100):
        return self._cases[:limit]


def _setup(monkeypatch, cases, op_uid="2e953a9b1da0", op_email="acorrea@arkmurus.com"):
    # Empty index → triggers the R-F1973 vault rebuild path.
    async def _get_json(key):
        return []

    async def _set_json(key, val, ex=None):
        return True

    from aria_service.intel import redis_store as rs
    monkeypatch.setattr(rs, "get_json", _get_json)
    monkeypatch.setattr(rs, "set_json", _set_json)
    # Fake vault with owner-less cases.
    import aria_service.intel.dd_vault as vault_mod
    monkeypatch.setattr(vault_mod, "get_vault", lambda *a, **k: _FakeVault(cases))
    monkeypatch.setenv("ARIA_CODER_OPERATOR_USER_ID", op_uid)
    monkeypatch.setenv("ARIA_OPERATOR_EMAIL", op_email)


_OWNERLESS_CASES = [
    {"latest_report_id": "dd_aaa", "entity_name": "Assan Group", "last_run_at": 1.0,
     "entity_type": "company", "risk_level": "unknown"},  # NOTE: no user_id key
    {"latest_report_id": "dd_bbb", "entity_name": "RealPath Defence Ltd", "last_run_at": 2.0,
     "entity_type": "company", "risk_level": "unknown"},
]


def test_rf2382_operator_sees_ownerless_vault_reports(monkeypatch):
    _setup(monkeypatch, _OWNERLESS_CASES)
    got = asyncio.run(d.list_reports(user_id="2e953a9b1da0", user_email_domain="arkmurus.com"))
    got = got if isinstance(got, list) else []
    names = {(g.get("entity_name") or "").strip() for g in got}
    assert "Assan Group" in names and "RealPath Defence Ltd" in names, (
        f"operator must see the owner-less vault reports; got {names}"
    )


def test_rf2382_other_user_does_not_see_them(monkeypatch):
    _setup(monkeypatch, _OWNERLESS_CASES)
    # A different user (no domain match) must NOT see the operator's reports.
    got = asyncio.run(d.list_reports(user_id="someone_else_999", user_email_domain="other.com"))
    got = got if isinstance(got, list) else []
    assert got == [], f"owner-less reports must not leak to other users; got {len(got)}"


def test_rf2382_no_operator_configured_stays_ownerless(monkeypatch):
    # When no operator is configured, behaviour is unchanged (owner-less/admin-only).
    _setup(monkeypatch, _OWNERLESS_CASES, op_uid="", op_email="")
    monkeypatch.delenv("ARIA_CODER_OPERATOR_USER_ID", raising=False)
    scoped = asyncio.run(d.list_reports(user_id="2e953a9b1da0"))
    scoped = scoped if isinstance(scoped, list) else []
    assert scoped == [], "with no operator configured, owner-less reports stay hidden from users"
    # admin (no filter) still sees them
    admin = asyncio.run(d.list_reports())
    assert len([a for a in (admin or []) if a.get("entity_name") in ("Assan Group", "RealPath Defence Ltd")]) == 2
