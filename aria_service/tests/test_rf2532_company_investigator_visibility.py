"""R-F2532 — company_investigator never-false-clean + dead-API fixes.

Codex deep-review (2026-07-07) found company_investigator.py calling ~7 wrong/dead
APIs (entity_resolver.resolve(jurisdiction=…), procurement_history.search(),
news_monitor.search(), companies_house.search(), conflict_tracker.get_events(),
web_crawler.crawl(), *.get_headers()) — every one raising and being swallowed to
logger.debug, so investigate_company returned a GREEN report while most enrichment
was silently dead.

R-F2532 does two things, both tested here as capability tests that drive the REAL
functions the operator hits:
  1. NEVER-FALSE-CLEAN visibility — every failed/timed-out phase is recorded on
     report.phase_failures and surfaced to the user (open_questions + a coverage-gap
     risk_indicator + an honest "not clean" summary).
  2. Fixes the two unambiguous API breaks: resolve() drops the bogus `jurisdiction`
     kwarg; procurement uses query_entity_history() and unwraps `consolidated`.
"""
from __future__ import annotations

import asyncio
import os

import aria_service.intel.company_investigator as ci


# ---------------------------------------------------------------------------
# 1. _note_phase is defensive and records
# ---------------------------------------------------------------------------
def test_note_phase_records_and_never_raises():
    rep = ci.InvestigationReport(entity_name="X")
    ci._note_phase(rep, "registry", ValueError("boom"))
    ci._note_phase(rep, "news", timed_out=True)
    ci._note_phase(rep, "bare")
    assert any("registry: boom" in p for p in rep.phase_failures)
    assert any("news: timed out" in p for p in rep.phase_failures)
    assert "bare" in rep.phase_failures
    # Must never raise even if the report is malformed.
    class _Bad:
        phase_failures = None  # append will raise
    ci._note_phase(_Bad(), "x", RuntimeError("y"))  # no exception = pass


# ---------------------------------------------------------------------------
# 2. FIX: entity_resolver.resolve is called with a VALID signature
#    (old code passed jurisdiction=… → TypeError on every DD, swallowed)
# ---------------------------------------------------------------------------
def test_entity_resolution_uses_valid_resolve_signature(monkeypatch):
    import aria_service.intel.entity_resolver as er
    seen = {}

    async def _fake_resolve(query, *, persona=None, nationality_iso2=None, fetch_history=True):
        # If the caller still passed jurisdiction=… this stub would TypeError,
        # exactly reproducing the pre-fix break.
        seen["query"] = query
        seen["nationality_iso2"] = nationality_iso2
        return {"canonical": "Acme Ltd", "entity_type": "company"}

    monkeypatch.setattr(er, "resolve", _fake_resolve)
    rep = ci.InvestigationReport(entity_name="Acme")
    asyncio.run(ci._phase_entity_resolution(rep, "Acme", "GB", ""))

    assert seen["query"] == "Acme"
    assert seen["nationality_iso2"] == "GB"           # 2-letter juris passed through
    assert rep.canonical_name == "Acme Ltd"
    assert not any("entity resolution" in p for p in rep.phase_failures)  # no swallowed TypeError


def test_entity_resolution_omits_non_iso2_jurisdiction(monkeypatch):
    import aria_service.intel.entity_resolver as er

    async def _fake_resolve(query, *, persona=None, nationality_iso2=None, fetch_history=True):
        assert nationality_iso2 is None   # "United Kingdom" is not ISO2 -> omitted
        return {"canonical": "Acme"}

    monkeypatch.setattr(er, "resolve", _fake_resolve)
    rep = ci.InvestigationReport(entity_name="Acme")
    asyncio.run(ci._phase_entity_resolution(rep, "Acme", "United Kingdom", ""))
    assert not rep.phase_failures


# ---------------------------------------------------------------------------
# 3. FIX: procurement uses query_entity_history and unwraps `consolidated`
#    (old code called .search() → AttributeError on every DD, swallowed)
# ---------------------------------------------------------------------------
def test_procurement_uses_query_entity_history(monkeypatch):
    import aria_service.intel.procurement_history as ph
    called = {}

    async def _fake_qeh(entity_name, *, jurisdiction_iso2=None, **kw):
        called["entity_name"] = entity_name
        called["jurisdiction_iso2"] = jurisdiction_iso2
        return {"consolidated": [
            {"title": "Framework award", "value": "£1m", "date": "2025-01-01", "url": "http://x"},
        ]}

    # If any code still reaches for .search(), make it explode so the test catches it.
    def _boom(*a, **k):
        raise AssertionError("procurement must not call .search() (dead API)")
    monkeypatch.setattr(ph, "query_entity_history", _fake_qeh)
    monkeypatch.setattr(ph, "search", _boom, raising=False)

    rep = ci.InvestigationReport(entity_name="Acme")
    asyncio.run(ci._phase_procurement(rep, "Acme", "GB"))

    assert called["entity_name"] == "Acme"
    assert called["jurisdiction_iso2"] == "GB"
    assert any(f.category == "procurement" for f in rep.findings)
    assert not any("procurement" in p for p in rep.phase_failures)


# ---------------------------------------------------------------------------
# 4. CAPABILITY: investigate_company surfaces enrichment failures instead of
#    returning a falsely-clean report.
# ---------------------------------------------------------------------------
def test_investigate_company_surfaces_incomplete_enrichment(monkeypatch):
    monkeypatch.setenv("ARIA_COMPANY_INVESTIGATOR_ENABLED", "1")
    monkeypatch.setattr(ci, "_ENABLED", True, raising=False)

    async def _ok(*a, **k):           # a phase that quietly does nothing
        return None

    async def _fails(report, *a, **k):  # a phase whose upstream API is dead
        ci._note_phase(report, "company registry", RuntimeError("dead API"))

    for name in (
        "_phase_entity_resolution", "_phase_web_search", "_phase_deep_crawl",
        "_phase_sanctions", "_phase_conflict_risk", "_phase_news",
        "_phase_social_media", "_phase_tech_stack", "_phase_ssl_dns",
        "_phase_procurement", "_phase_contract_lookup",
    ):
        monkeypatch.setattr(ci, name, _ok)
    monkeypatch.setattr(ci, "_phase_company_registry", _fails)

    rep = asyncio.run(ci.investigate_company("Nowhere Trading LLC", jurisdiction="GB"))

    # The failure is recorded and made VISIBLE to the user — not swallowed.
    assert rep.phase_failures, "phase failure was swallowed (false-clean)"
    assert any("Enrichment incomplete" in q for q in rep.open_questions)
    # 0 findings + failed enrichment => explicit coverage-gap flag (never-false-clean).
    assert any("Coverage gap" in r for r in rep.risk_indicators)
    # And the summary must NOT read as a clean bill of health.
    assert "NOT a clean result" in rep.summary


if __name__ == "__main__":
    import sys
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
