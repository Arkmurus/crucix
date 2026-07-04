"""R-F2407 — capability test: company_investigator web-search phases must
actually land findings from web_search.search().

Root cause (retrieval class-B bug, NOT infra-saturation):
  web_search.search() returns `SearchResult` DATACLASS objects and its
  keyword-only param is `max_results`, NOT `num_results`. The three
  web-search phases (_phase_web_search / _phase_social_media /
  _phase_procurement) called `search(query, num_results=N)` (invalid kwarg
  → TypeError) AND then treated each result as a dict via `r.get("url")`
  (AttributeError on a dataclass). Both errors were swallowed by the phase's
  broad `except Exception`, so EVERY web finding was silently dropped —
  company investigations returned "Found 1 data point" (only the non-web
  legs contributed). This drives the deep_research "1 data point" symptom
  (reasoning_router → company_investigator).

These tests drive the REAL, previously-broken phase functions with a
web_search.search that returns REAL SearchResult objects (no network), and
assert web findings now land. They also assert the two failure classes that
the fix closes so a regression re-surfaces here.
"""
from __future__ import annotations

import asyncio
import inspect

import pytest

from aria_service.intel import company_investigator as ci
from aria_service.intel import web_search as ws
from aria_service.intel.web_search import SearchResult


def _fake_results():
    return [
        SearchResult(
            title="Acme Ltd — official site",
            url="https://acme.example/about",
            snippet="Acme Ltd is a manufacturer of widgets based in London.",
            source="searxng:google",
            credibility_tier=3,
        ),
        SearchResult(
            title="Acme Ltd director filing",
            url="https://register.example/acme",
            snippet="Director: Jane Doe. Incorporated 2011.",
            source="duckduckgo",
            credibility_tier=4,
        ),
    ]


# ── Regression guards: prove the two failure classes the fix closes ──────────

def test_search_signature_has_max_results_not_num_results():
    """The real search() takes max_results (keyword-only); num_results is NOT a
    valid kwarg. Guards against the bug re-appearing at the call site."""
    params = inspect.signature(ws.search).parameters
    assert "max_results" in params, "search() lost its max_results param"
    assert "num_results" not in params, (
        "search() does NOT accept num_results — callers must use max_results"
    )


def test_search_returns_dataclass_not_dict():
    """web_search.search returns SearchResult dataclasses (no .get); the phase
    must convert them. This documents why _ws_result_to_dict exists."""
    r = _fake_results()[0]
    assert not isinstance(r, dict)
    assert not hasattr(r, "get")  # a dict would have .get — a SearchResult does not
    d = ci._ws_result_to_dict(r)
    assert isinstance(d, dict)
    assert d.get("url") == "https://acme.example/about"
    assert d.get("title", "").startswith("Acme")


def test_result_to_dict_passes_through_dicts():
    d = {"url": "https://x.example", "title": "x", "snippet": "s"}
    assert ci._ws_result_to_dict(d) is d


# ── Capability tests: the REAL broken paths now land web findings ────────────

def test_phase_web_search_lands_findings(monkeypatch):
    async def fake_search(query, **kwargs):
        # Assert the caller uses the correct kwarg — a num_results call would
        # have raised TypeError against the real search and dropped everything.
        assert "num_results" not in kwargs
        return _fake_results()

    monkeypatch.setattr(ws, "search", fake_search)

    report = ci.InvestigationReport(entity_name="Acme Ltd", jurisdiction="UK")
    asyncio.run(
        ci._phase_web_search(report, "Acme Ltd", "")
    )

    web = [f for f in report.findings if f.category == "web"]
    assert web, "web-search phase produced ZERO findings (retrieval leg amputated)"
    assert any(f.source.startswith("http") for f in web)


def test_phase_social_media_lands_findings(monkeypatch):
    async def fake_search(query, **kwargs):
        assert "num_results" not in kwargs
        return _fake_results()

    monkeypatch.setattr(ws, "search", fake_search)

    report = ci.InvestigationReport(entity_name="Acme Ltd")
    asyncio.run(
        ci._phase_social_media(report, "Acme Ltd")
    )

    social = [f for f in report.findings if f.category == "social"]
    assert social, "social-media phase produced ZERO findings"


def test_phase_contract_lookup_web_branch_lands_findings(monkeypatch):
    async def fake_search(query, **kwargs):
        assert "num_results" not in kwargs
        return _fake_results()

    # Force the name-based web_search fallback branch: make the UEI/registry
    # lookup path raise so control reaches the `_ws.search(...)` leg.
    async def boom(*a, **k):
        raise RuntimeError("no registry — fall through to web_search")

    monkeypatch.setattr(ws, "search", fake_search)
    import aria_service.intel.portal_registry as pr
    monkeypatch.setattr(pr, "lookup_contracts_by_uei", boom, raising=False)

    report = ci.InvestigationReport(entity_name="Acme Ltd")
    # No UEI → name-based web_search branch.
    asyncio.run(
        ci._phase_contract_lookup(report, "Acme Ltd", "")
    )

    contracts = [f for f in report.findings if f.category == "contracts"]
    assert contracts, "contract-lookup web branch produced ZERO web findings"


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
