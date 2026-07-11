"""R-F2536 — rewire company_investigator's dead conflict_tracker API.

R-F2532 made this phase VISIBLE as dead: it called conflict_tracker.get_events()
(doesn't exist) and passed `jurisdiction or company_name` — feeding a company name as
a country. The real API is get_recent_events(country, days, limit) -> dict
(total_events / fatalities / by_type / escalation_score). It is COUNTRY-level, so
R-F2536 gates on a real jurisdiction and parses the dict, adding a risk indicator when
country escalation is elevated.
"""
from __future__ import annotations

import asyncio

import aria_service.intel.company_investigator as ci


def test_conflict_uses_get_recent_events_and_parses_dict(monkeypatch):
    import aria_service.intel.conflict_tracker as ct
    called = {}

    async def _fake(country, days=30, limit=50):
        called["country"] = country
        called["days"] = days
        return {"country": country, "total_events": 42, "fatalities": 120,
                "escalation_score": 65, "by_type": {"Battles": 30, "Protests": 12}}

    monkeypatch.setattr(ct, "get_recent_events", _fake)
    # Old dead API must never be reached.
    monkeypatch.setattr(ct, "get_events", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("must not call conflict_tracker.get_events (dead API)")), raising=False)

    rep = ci.InvestigationReport(entity_name="Acme")
    asyncio.run(ci._phase_conflict_risk(rep, "Acme", "Nigeria"))

    assert called["country"] == "Nigeria"      # country passed, NOT company name
    conf = [f for f in rep.findings if f.category == "conflict"]
    assert conf, "no conflict finding created"
    s = conf[0].summary
    assert "42 kinetic event" in s and "120 fatalities" in s and "65/100" in s and "Battles" in s
    # Elevated escalation => material risk indicator.
    assert any("elevated conflict" in ri and "Nigeria" in ri for ri in rep.risk_indicators)
    assert not any("conflict" in p for p in rep.phase_failures)


def test_conflict_skips_when_no_jurisdiction(monkeypatch):
    import aria_service.intel.conflict_tracker as ct

    async def _boom(*a, **k):
        raise AssertionError("must NOT query conflict_tracker without a country")
    monkeypatch.setattr(ct, "get_recent_events", _boom)

    rep = ci.InvestigationReport(entity_name="Acme")
    asyncio.run(ci._phase_conflict_risk(rep, "Acme", ""))  # no jurisdiction

    assert not rep.findings
    # A missing country is a missing INPUT — recorded as a soft open_question, NOT a
    # phase_failure (which would over-escalate to coverage-gap / "not clean").
    assert not any("conflict" in p for p in rep.phase_failures)
    assert any("not assessed" in q and "no country" in q for q in rep.open_questions)


def test_conflict_quiet_country_no_finding_no_failure(monkeypatch):
    import aria_service.intel.conflict_tracker as ct

    async def _quiet(country, days=30, limit=50):
        return {"country": country, "total_events": 0, "escalation_score": 0}

    monkeypatch.setattr(ct, "get_recent_events", _quiet)
    rep = ci.InvestigationReport(entity_name="Acme")
    asyncio.run(ci._phase_conflict_risk(rep, "Acme", "Luxembourg"))

    assert not rep.findings                       # 0 events -> no conflict finding
    assert not rep.risk_indicators                # and no false risk
    assert not any("conflict" in p for p in rep.phase_failures)  # not a source failure
    # NEVER-FALSE-CLEAN: 0 events is ambiguous (quiet vs out-of-coverage) — say so,
    # don't silently imply clean.
    assert any("inconclusive" in q and "Luxembourg" in q for q in rep.open_questions)


def test_conflict_low_escalation_no_risk_indicator(monkeypatch):
    import aria_service.intel.conflict_tracker as ct

    async def _low(country, days=30, limit=50):
        return {"country": country, "total_events": 3, "fatalities": 0,
                "escalation_score": 10, "by_type": {"Protests": 3}}

    monkeypatch.setattr(ct, "get_recent_events", _low)
    rep = ci.InvestigationReport(entity_name="Acme")
    asyncio.run(ci._phase_conflict_risk(rep, "Acme", "Portugal"))

    conf = [f for f in rep.findings if f.category == "conflict"]
    assert conf                                   # a low-level finding is still recorded
    assert not rep.risk_indicators                # but NOT flagged as elevated risk


if __name__ == "__main__":
    import sys
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
