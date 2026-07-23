"""R-F2918 — GB registry liveness must be recorded where the real call happens.

GB is deliberately `_COVERED_ELSEWHERE` (registry_coverage.py:50): it has NO entry in
the registry_adapters dispatch table, because dd_orchestrator._run_identity has a
dedicated Companies House branch. The consequence nobody had noticed is that
`lookup_entity(name, "GB")` never touches Companies House at all — it falls straight
through to the GLEIF global fallback — and the CH branch recorded nothing. So GB could
never move off `unproven`, while being the best-covered jurisdiction in the inventory.

Proven live on aria-intel 2026-07-23: is_enabled=True, BAE SYSTEMS PLC ->
company_number 01470151, status active, incorporated 1979-12-31, 13 current officers.
The R-F2911 sweep still showed GB as FALLBACK, because it probes the adapter path that
GB is not on.

The fix records at the real call site rather than bolting a duplicate GB adapter onto
the dispatch table — two code paths to one registry is how they drift apart.
"""
from __future__ import annotations

import asyncio

import pytest

from aria_service.intel import dd_orchestrator as ddo


def test_rf2918_gb_branch_records_success_on_a_real_profile(monkeypatch):
    """CAPABILITY: drive the GB identity branch and assert the coverage write."""
    calls: list[tuple] = []
    from aria_service.intel import registry_adapters as ra
    monkeypatch.setattr(ra, "_record_coverage_outcome",
                        lambda iso2, adapter, outcome: calls.append((iso2, adapter, outcome)))

    from aria_service.intel import companies_house as ch
    monkeypatch.setattr(ch, "missing_key_gap", lambda: None)
    monkeypatch.setattr(ch, "consume_unavailable", lambda: None)

    async def _fake_investigate(company_number=None, company_name=None):
        return {
            "found": True,
            "profile": {"company_number": "01470151", "company_name": "BAE SYSTEMS PLC",
                        "company_status": "active", "date_of_creation": "1979-12-31"},
            "officers": {"current": [{"name": "A"}], "past": [], "total": 1},
            "psc": {"current": [], "past": [], "total": 0},
        }

    monkeypatch.setattr(ch, "investigate_uk_entity", _fake_investigate, raising=False)

    report = ddo.ARKDDReport()
    asyncio.run(ddo._run_identity(
        {"name": "BAE SYSTEMS PLC", "type": "company", "jurisdiction_iso2": "GB"},
        report,
    ))

    gb = [c for c in calls if c[0] == "GB"]
    assert gb, "the GB branch recorded NO coverage outcome — GB stays unproven forever"
    assert gb[0] == ("GB", "companies_house", "success")
    # And the real work still happened.
    assert report.identity.registration_number == "01470151"


def test_rf2918_gb_records_empty_when_companies_house_finds_nothing(monkeypatch):
    """An empty CH response must NOT read as liveness."""
    calls: list[tuple] = []
    from aria_service.intel import registry_adapters as ra
    monkeypatch.setattr(ra, "_record_coverage_outcome",
                        lambda iso2, adapter, outcome: calls.append((iso2, adapter, outcome)))

    from aria_service.intel import companies_house as ch
    monkeypatch.setattr(ch, "missing_key_gap", lambda: None)
    monkeypatch.setattr(ch, "consume_unavailable", lambda: None)

    async def _fake_empty(company_number=None, company_name=None):
        return {"found": False, "profile": {}, "officers": {}, "psc": {}, "error": "no match"}

    monkeypatch.setattr(ch, "investigate_uk_entity", _fake_empty, raising=False)

    asyncio.run(ddo._run_identity(
        {"name": "NOSUCHCOMPANY LTD", "type": "company", "jurisdiction_iso2": "GB"},
        ddo.ARKDDReport(),
    ))

    gb = [c for c in calls if c[0] == "GB"]
    assert gb and gb[0][2] == "empty", f"expected empty, got {gb}"


def test_rf2918_gb_records_error_when_the_lookup_throws(monkeypatch):
    """Recording only the happy path would make the inventory incapable of ever
    showing GB as `failing` — the same one-sided reporting this surface exists to
    avoid."""
    calls: list[tuple] = []
    from aria_service.intel import registry_adapters as ra
    monkeypatch.setattr(ra, "_record_coverage_outcome",
                        lambda iso2, adapter, outcome: calls.append((iso2, adapter, outcome)))

    from aria_service.intel import companies_house as ch
    monkeypatch.setattr(ch, "missing_key_gap", lambda: None)

    async def _fake_boom(company_number=None, company_name=None):
        raise RuntimeError("Companies House 503")

    monkeypatch.setattr(ch, "investigate_uk_entity", _fake_boom, raising=False)

    asyncio.run(ddo._run_identity(
        {"name": "BAE SYSTEMS PLC", "type": "company", "jurisdiction_iso2": "GB"},
        ddo.ARKDDReport(),
    ))

    gb = [c for c in calls if c[0] == "GB"]
    assert gb and gb[0][2] == "error", f"expected error, got {gb}"


def test_rf2918_gb_is_declared_covered_elsewhere_not_in_dispatch():
    """Pin the premise. If GB is ever added to the adapter dispatch table, this fix
    becomes a DOUBLE record and the reason for recording here disappears — the test
    should fail loudly rather than let two paths drift."""
    from aria_service.intel import registry_coverage as rc
    from aria_service.intel import registry_adapters as ra

    assert rc._COVERED_ELSEWHERE.get("GB") == "companies_house"
    assert "GB" not in ra._DISPATCH, (
        "GB gained an adapter-dispatch entry — the GB branch in dd_orchestrator now "
        "double-records coverage. Remove one of the two paths."
    )
