"""R-F2533 — rewire company_investigator's dead companies_house + news_monitor APIs.

R-F2532 made these two phases VISIBLE as dead (they called non-existent APIs and
swallowed the AttributeError). R-F2533 wires them to the REAL module functions:

  - company registry: companies_house.search_companies(query, limit) with correct key
    mapping (title/company_status/date_of_creation/company_number), UK-only jurisdiction
    gating, and honest missing-API-key surfacing via missing_key_gap().
  - news: news_monitor has NO keyword search — it's an RSS poller. Use
    get_recent_articles(limit) and filter by company-name mention.

These are capability tests driving the REAL phase functions with the upstream modules
mocked at their real signatures — a stub with the wrong signature would reproduce the
pre-fix break.
"""
from __future__ import annotations

import asyncio

import aria_service.intel.company_investigator as ci


# ─────────────────────────── companies_house ───────────────────────────
def test_registry_uses_search_companies_with_correct_mapping(monkeypatch):
    import aria_service.intel.companies_house as ch
    called = {}

    async def _fake_search(query, limit=5):
        called["query"] = query
        called["limit"] = limit
        return [{
            "company_number": "01234567",
            "title": "ACME TRADING LTD",
            "company_status": "active",
            "date_of_creation": "2001-05-04",
            "address_snippet": "1 High St, London",
            "company_type": "ltd",
        }]

    monkeypatch.setattr(ch, "search_companies", _fake_search)
    monkeypatch.setattr(ch, "missing_key_gap", lambda: None)
    # Old dead API must never be reached.
    monkeypatch.setattr(ch, "search", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("must not call companies_house.search (dead API)")), raising=False)

    rep = ci.InvestigationReport(entity_name="Acme")
    asyncio.run(ci._phase_company_registry(rep, "Acme Trading Ltd", "GB"))

    assert called["query"] == "Acme Trading Ltd"
    reg = [f for f in rep.findings if f.category == "registry"]
    assert reg, "no registry finding created"
    s = reg[0].summary
    assert "active" in s and "2001-05-04" in s and "01234567" in s, s
    assert "unknown" not in s, f"wrong key mapping: {s}"
    assert "company-information.service.gov.uk/company/01234567" in reg[0].source
    assert not any("company registry" in p for p in rep.phase_failures)


def test_registry_skips_non_uk_jurisdiction(monkeypatch):
    import aria_service.intel.companies_house as ch

    async def _boom_search(*a, **k):
        raise AssertionError("CH must NOT be queried for a non-UK jurisdiction")
    monkeypatch.setattr(ch, "search_companies", _boom_search)
    monkeypatch.setattr(ch, "missing_key_gap", lambda: None)

    rep = ci.InvestigationReport(entity_name="Acme Inc")
    asyncio.run(ci._phase_company_registry(rep, "Acme Inc", "US"))

    assert not rep.findings
    # Honestly recorded as an un-checked registry (never-false-clean), not silent.
    assert any("company registry" in p and "US" in p for p in rep.phase_failures)


def test_registry_surfaces_missing_api_key(monkeypatch):
    import aria_service.intel.companies_house as ch

    async def _boom_search(*a, **k):
        raise AssertionError("must not call search when the API key is missing")
    monkeypatch.setattr(ch, "search_companies", _boom_search)
    monkeypatch.setattr(ch, "missing_key_gap", lambda: "Companies House API key not configured — set COMPANIES_HOUSE_API_KEY")

    rep = ci.InvestigationReport(entity_name="Acme")
    asyncio.run(ci._phase_company_registry(rep, "Acme Ltd", "GB"))

    assert not rep.findings
    assert any("company registry" in p and "API key" in p for p in rep.phase_failures)


# ─────────────────────────────── news ──────────────────────────────────
def test_news_filters_recent_articles_by_mention(monkeypatch):
    import aria_service.intel.news_monitor as nm

    async def _fake_recent(limit=50):
        return [
            {"title": "Acme Trading fined by regulator", "summary": "The firm Acme Trading …",
             "url": "http://n/1", "source": "Reuters", "published": "2026-01-01"},
            {"title": "Unrelated market news", "summary": "Stocks rose today.",
             "url": "http://n/2", "source": "FT", "published": "2026-01-02"},
        ]

    monkeypatch.setattr(nm, "get_recent_articles", _fake_recent)
    # The old dead API must never be reached.
    monkeypatch.setattr(nm, "search", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("must not call news_monitor.search (dead API)")), raising=False)

    rep = ci.InvestigationReport(entity_name="Acme Trading")
    asyncio.run(ci._phase_news(rep, "Acme Trading"))

    news = [f for f in rep.findings if f.category == "news"]
    assert len(news) == 1, f"expected only the matching article, got {len(news)}"
    assert "fined by regulator" in news[0].title
    assert news[0].source == "http://n/1"
    assert not any("news" in p for p in rep.phase_failures)


def test_news_no_mention_yields_no_findings_and_no_failure(monkeypatch):
    import aria_service.intel.news_monitor as nm

    async def _fake_recent(limit=50):
        return [{"title": "Something else entirely", "summary": "No relevant company here.",
                 "url": "http://n/9", "source": "BBC", "published": "2026-01-03"}]

    monkeypatch.setattr(nm, "get_recent_articles", _fake_recent)
    rep = ci.InvestigationReport(entity_name="Zzqx Holdings")
    asyncio.run(ci._phase_news(rep, "Zzqx Holdings"))

    assert not rep.findings
    # The phase RAN successfully and found nothing — that is NOT a phase failure.
    assert not any("news" in p for p in rep.phase_failures)


if __name__ == "__main__":
    import sys
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
