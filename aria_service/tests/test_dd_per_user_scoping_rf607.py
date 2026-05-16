"""R-F607 (2026-05-16) — DD reports per-user scoping tests.

Pre-R-F607 ``/api/aria/dd/reports`` returned the global Redis index to
every authenticated user — operator A could read operator B's
counterparty screenings.

R-F607 stamps ``user_id`` (and the R-F608 follow-up ``user_email_lower``
+ ``user_email_domain``) onto every DD run at orchestrate time and
filters ``list_reports`` to the requester's own runs. R-F608's
``user_email_domain`` filter is wired in the same patch but exercised
in its own R-number's test file.

These tests cover the store-layer / list_reports filter logic. Live
end-to-end is verified by curl-replay in the commit message.
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import patch

import pytest


class _FakeRS:
    """In-memory stub matching the json/set_json contract used by
    dd_orchestrator.list_reports. dd_orchestrator imports redis_store
    lazily inside the function (``from . import redis_store as rs``)
    so we monkeypatch the redis_store module's get_json / set_json
    attributes directly rather than the local ``rs`` alias."""

    def __init__(self, initial: dict | None = None):
        self.store: dict[str, list | dict] = dict(initial or {})

    async def get_json(self, key):
        return self.store.get(key)

    async def set_json(self, key, value, ex=None):
        self.store[key] = value


@pytest.fixture
def fake_rs_for_dd(monkeypatch):
    """Patch the redis_store module's get_json/set_json with the in-mem
    fake so list_reports doesn't reach for a real Redis connection."""
    from aria_service.intel import redis_store as real_rs
    fake = _FakeRS()
    monkeypatch.setattr(real_rs, "get_json", fake.get_json)
    monkeypatch.setattr(real_rs, "set_json", fake.set_json)
    return fake


def _seed_index(fake: _FakeRS, reports: list[dict]):
    """Seed the report index Redis key with the given entries."""
    from aria_service.intel import dd_orchestrator
    fake.store[dd_orchestrator.REPORT_INDEX_KEY] = list(reports)


# ── Filter behaviour ────────────────────────────────────────────────────


def test_list_reports_no_filter_returns_everything(fake_rs_for_dd):
    """Admin / autonomous-loop path — no user_id, no domain. Every entry
    returned including pre-R-F607 entries without a user_id stamp."""
    from aria_service.intel import dd_orchestrator

    _seed_index(fake_rs_for_dd, [
        {"run_id": "r1", "entity_name": "Acme", "user_id": "alice"},
        {"run_id": "r2", "entity_name": "Embraer", "user_id": "bob"},
        {"run_id": "r3", "entity_name": "Legacy", "user_id": None},  # pre-R-F607
    ])

    out = asyncio.run(dd_orchestrator.list_reports(limit=50))

    ids = [r["run_id"] for r in out]
    assert ids == ["r1", "r2", "r3"], (
        "admin / no-filter path must return the full unfiltered index"
    )


def test_list_reports_user_id_filter_isolates_own_runs(fake_rs_for_dd):
    """Operator scenario: alice calls /dd/reports?user_id=alice; she
    must only see her own runs. bob's runs and legacy un-stamped runs
    are hidden."""
    from aria_service.intel import dd_orchestrator

    _seed_index(fake_rs_for_dd, [
        {"run_id": "r1", "entity_name": "Acme", "user_id": "alice"},
        {"run_id": "r2", "entity_name": "Embraer", "user_id": "bob"},
        {"run_id": "r3", "entity_name": "Legacy", "user_id": None},
        {"run_id": "r4", "entity_name": "Aselsan", "user_id": "alice"},
    ])

    out = asyncio.run(dd_orchestrator.list_reports(limit=50, user_id="alice"))

    ids = [r["run_id"] for r in out]
    assert set(ids) == {"r1", "r4"}, f"alice must see only her runs, got {ids}"


def test_list_reports_legacy_entries_hidden_from_user_filtered_view(fake_rs_for_dd):
    """Pre-R-F607 entries lack a user_id stamp. Until the operator
    manually claims them, they must NOT leak into anyone's user-filtered
    list — that would re-introduce the original leak."""
    from aria_service.intel import dd_orchestrator

    _seed_index(fake_rs_for_dd, [
        {"run_id": "legacy1", "entity_name": "OldRun", "user_id": None},
        {"run_id": "legacy2", "entity_name": "OldRun2"},  # field absent entirely
    ])

    out = asyncio.run(dd_orchestrator.list_reports(limit=50, user_id="alice"))

    assert out == [], "legacy entries must not leak to user-filtered list"


def test_list_reports_domain_filter_includes_same_company_runs(fake_rs_for_dd):
    """R-F608 same-company visibility: alice@ark.com sees bob@ark.com's
    runs too (default share_to_company=True). xyz@other.com is hidden."""
    from aria_service.intel import dd_orchestrator

    _seed_index(fake_rs_for_dd, [
        {"run_id": "r1", "user_id": "alice", "user_email_domain": "ark.com"},
        {"run_id": "r2", "user_id": "bob", "user_email_domain": "ark.com"},
        {"run_id": "r3", "user_id": "xyz", "user_email_domain": "other.com"},
    ])

    out = asyncio.run(
        dd_orchestrator.list_reports(
            limit=50,
            user_id="alice",
            user_email_domain="ark.com",
        )
    )

    ids = {r["run_id"] for r in out}
    assert ids == {"r1", "r2"}, f"same-company visibility broken, got {ids}"


def test_list_reports_share_to_company_false_opt_out(fake_rs_for_dd):
    """An operator can flag a sensitive DD with share_to_company=False
    to keep it private even from colleagues on the same domain."""
    from aria_service.intel import dd_orchestrator

    _seed_index(fake_rs_for_dd, [
        {
            "run_id": "private",
            "user_id": "alice",
            "user_email_domain": "ark.com",
            "share_to_company": False,
        },
        {
            "run_id": "shared",
            "user_id": "alice",
            "user_email_domain": "ark.com",
        },
    ])

    # bob (same company as alice) lists with domain filter
    out = asyncio.run(
        dd_orchestrator.list_reports(
            limit=50,
            user_id="bob",
            user_email_domain="ark.com",
        )
    )

    ids = {r["run_id"] for r in out}
    assert "shared" in ids, "shared DD should be visible to same-domain colleague"
    assert "private" not in ids, "share_to_company=False must hide DD from colleagues"


def test_list_reports_domain_only_no_user_id_still_filters(fake_rs_for_dd):
    """If the proxy passes user_email_domain but not user_id (unusual
    but defensive), the domain filter alone still narrows the view."""
    from aria_service.intel import dd_orchestrator

    _seed_index(fake_rs_for_dd, [
        {"run_id": "r1", "user_email_domain": "ark.com"},
        {"run_id": "r2", "user_email_domain": "other.com"},
    ])

    out = asyncio.run(
        dd_orchestrator.list_reports(limit=50, user_email_domain="ark.com")
    )

    assert {r["run_id"] for r in out} == {"r1"}


# ── Schema field surface ───────────────────────────────────────────────


def test_arkddreport_has_user_scoping_fields():
    """Capability: the dataclass exports user_id / user_email_lower /
    user_email_domain (so /dd/orchestrate's synchronous response carries
    the same scoping fields that the persisted Redis body uses)."""
    from aria_service.intel.dd_schema import ARKDDReport

    rep = ARKDDReport()
    d = rep.as_dict()
    assert "user_id" in d, "ARKDDReport.as_dict() must export user_id"
    assert "user_email_lower" in d
    assert "user_email_domain" in d
    assert d["user_id"] is None and d["user_email_lower"] is None


def test_arkddreport_user_fields_round_trip():
    from aria_service.intel.dd_schema import ARKDDReport

    rep = ARKDDReport(
        user_id="alice",
        user_email_lower="alice@ark.com",
        user_email_domain="ark.com",
    )
    d = rep.as_dict()
    assert d["user_id"] == "alice"
    assert d["user_email_lower"] == "alice@ark.com"
    assert d["user_email_domain"] == "ark.com"
