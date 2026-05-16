"""R-F608 (2026-05-16) — DD company-shared visibility tests.

Operator requirement: when multiple users register with the same
company email domain (e.g. both @arkmurus.com), they should see each
other's DD reports by default. R-F607 wired the user_email_domain
field through the persist + list path; R-F608 adds the per-DD
``share_to_company`` toggle (default True) so an operator can keep
a sensitive run private.

The list_reports filter logic itself is exercised by
test_dd_per_user_scoping_rf607.py — these tests focus on the
share_to_company round-trip (dataclass → persist → index → filter).
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
def fake_rs_for_dd(monkeypatch):
    from aria_service.intel import redis_store as real_rs
    fake = _FakeRS()
    monkeypatch.setattr(real_rs, "get_json", fake.get_json)
    monkeypatch.setattr(real_rs, "set_json", fake.set_json)
    return fake


# ── Dataclass surface ──────────────────────────────────────────────────


def test_arkddreport_share_to_company_defaults_true():
    """Default visibility for new DDs is "share with same-company
    colleagues" — operator requirement. Opt-out is per-DD."""
    from aria_service.intel.dd_schema import ARKDDReport

    rep = ARKDDReport()
    assert rep.share_to_company is True
    assert rep.as_dict()["share_to_company"] is True


def test_arkddreport_share_to_company_round_trip_false():
    from aria_service.intel.dd_schema import ARKDDReport

    rep = ARKDDReport(share_to_company=False)
    assert rep.share_to_company is False
    assert rep.as_dict()["share_to_company"] is False


# ── List filter honours the per-DD toggle ──────────────────────────────


def test_list_reports_default_share_lets_colleagues_see(fake_rs_for_dd):
    """Default share_to_company=True: bob@ark.com lists DDs and sees
    alice@ark.com's runs. The index entry doesn't explicitly carry
    share_to_company; missing is treated as True (default behaviour)."""
    from aria_service.intel import dd_orchestrator

    fake_rs_for_dd.store[dd_orchestrator.REPORT_INDEX_KEY] = [
        {
            "run_id": "alice-run",
            "user_id": "alice",
            "user_email_domain": "ark.com",
            # share_to_company key absent — treated as True
        },
    ]
    out = asyncio.run(
        dd_orchestrator.list_reports(
            limit=50, user_id="bob", user_email_domain="ark.com"
        )
    )
    assert {r["run_id"] for r in out} == {"alice-run"}, (
        "default-share alice run must be visible to bob (same company)"
    )


def test_list_reports_explicit_share_true_still_visible(fake_rs_for_dd):
    from aria_service.intel import dd_orchestrator

    fake_rs_for_dd.store[dd_orchestrator.REPORT_INDEX_KEY] = [
        {
            "run_id": "alice-run",
            "user_id": "alice",
            "user_email_domain": "ark.com",
            "share_to_company": True,
        },
    ]
    out = asyncio.run(
        dd_orchestrator.list_reports(
            limit=50, user_id="bob", user_email_domain="ark.com"
        )
    )
    assert {r["run_id"] for r in out} == {"alice-run"}


def test_list_reports_share_false_excludes_from_colleague_view(fake_rs_for_dd):
    """Capability: a private DD (share_to_company=False) is hidden
    from same-company colleagues but STILL visible to the owner."""
    from aria_service.intel import dd_orchestrator

    fake_rs_for_dd.store[dd_orchestrator.REPORT_INDEX_KEY] = [
        {
            "run_id": "alice-private",
            "user_id": "alice",
            "user_email_domain": "ark.com",
            "share_to_company": False,
        },
    ]
    # bob (same company) — should NOT see it
    bob_view = asyncio.run(
        dd_orchestrator.list_reports(
            limit=50, user_id="bob", user_email_domain="ark.com"
        )
    )
    assert bob_view == [], "private DD leaked to colleague"

    # alice (owner) — MUST still see her own private run
    alice_view = asyncio.run(
        dd_orchestrator.list_reports(
            limit=50, user_id="alice", user_email_domain="ark.com"
        )
    )
    assert {r["run_id"] for r in alice_view} == {"alice-private"}, (
        "private DD hidden from its own owner"
    )


def test_list_reports_share_false_does_not_block_owner_user_id_match(fake_rs_for_dd):
    """share_to_company only narrows the same-company branch. The
    owner-match branch (user_id == entry.user_id) is unaffected — your
    own private DD is always yours to see."""
    from aria_service.intel import dd_orchestrator

    fake_rs_for_dd.store[dd_orchestrator.REPORT_INDEX_KEY] = [
        {
            "run_id": "x",
            "user_id": "alice",
            "user_email_domain": "ark.com",
            "share_to_company": False,
        },
    ]
    out = asyncio.run(dd_orchestrator.list_reports(limit=50, user_id="alice"))
    assert {r["run_id"] for r in out} == {"x"}
