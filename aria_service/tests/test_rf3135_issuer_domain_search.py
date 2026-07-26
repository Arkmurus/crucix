"""R-F3135 — the "issuer report" route never searched the issuer's own domain.

PROVEN BY LIVE PROBE, 2026-07-26, against Babcock International Group plc. The route's
only source of candidate documents was `_search_financial_footprint`, whose query is

    '"{name}" (annual report OR financial statements OR revenue OR turnover OR filing)'

It returned four sources, and NOT ONE was the issuer's own site:

    https://www.wsj.com/market-data/quotes/UK/XLON/BAB/financials     G1 False
    https://companycheck.co.uk/company/02342138/...                   G1 False
    https://financialfilings.com/companies/babcock-...                G1 False
    https://uk.advfn.com/stock-market/london/babcock-BAB/financials   G1 False

G1 (`_issuer_domain_matches`) then correctly rejected all four — a third party's summary
of a company's accounts is not the company's accounts. So a route NAMED "issuer report"
could never fire for ANY subject: every listed group read "financials unverified"
regardless of budget. R-F3131 had given this op 150s; TIME WAS NEVER THE CONSTRAINT.

The gate was right. The search never looked where the document lives.

Two further facts the probe established, both encoded below:

1. `web_search.search()` returns `SearchResult` DATACLASSES, not dicts. A first cut of
   this fix filtered on `isinstance(h, dict)` and silently dropped every hit — the new
   search returned 0 documents against a perfectly healthy backend. Caught before
   shipping, and pinned here because the failure is INVISIBLE: it looks exactly like
   "no issuer document exists".
2. The queries carry no `(a OR b)` block. Per R-F3051..R-F3056 Brave returns HTTP 200
   while SILENTLY DROPPING the quoted phrase when an OR-block is present, so the one
   search feeding this route was untargeted AND degraded.
"""
import asyncio

import pytest

from aria_service.intel import financial_health as fh
from aria_service.intel.web_search import SearchResult


BABCOCK = "Babcock International Group plc"

# What the footprint search really returned (live, verbatim) — all aggregators.
AGGREGATOR_URLS = [
    "https://www.wsj.com/market-data/quotes/UK/XLON/BAB/financials",
    "https://companycheck.co.uk/company/02342138/BABCOCK-INTERNATIONAL-GROUP-PLC/companies-house-data",
    "https://financialfilings.com/companies/babcock-international-group-plc/2026/",
    "https://uk.advfn.com/stock-market/london/babcock-BAB/financials",
]
ISSUER_URL = (
    "https://www.babcockinternational.com/wp-content/uploads/2025/06/"
    "Babcock-Annual-Report-2025.pdf"
)


def _sr(url, title="Annual Report and Accounts 2025"):
    """A REAL SearchResult, not a dict — the shape web_search actually returns."""
    return SearchResult(title=title, url=url, snippet="", source="brave")


def _patch_search(monkeypatch, results_by_query=None, results=None):
    calls = []

    async def _fake(query, **kw):
        calls.append(query)
        if results_by_query is not None:
            return results_by_query.get(query, [])
        return list(results or [])

    # financial_health does `from . import web_search` INSIDE the function, so the
    # module attribute is the real seam — there is no fh.web_search to patch.
    import aria_service.intel.web_search as ws
    monkeypatch.setattr(ws, "search", _fake)
    return calls


def test_rf3135_finds_the_issuer_document_among_aggregators(monkeypatch):
    """THE FIX: the issuer's own PDF is selected; aggregators are not."""
    hits = [_sr(u) for u in AGGREGATOR_URLS] + [_sr(ISSUER_URL)]
    _patch_search(monkeypatch, results=hits)

    out = asyncio.run(fh._search_issuer_domain_documents(BABCOCK))
    assert out, "the issuer's own annual report must be found"
    urls = [s.get("url") for s in out]
    assert ISSUER_URL in urls, urls
    for bad in AGGREGATOR_URLS:
        assert bad not in urls, f"aggregator leaked through G1: {bad}"


def test_rf3135_capability_searchresult_dataclasses_are_not_dropped(monkeypatch):
    """THE BUG I SHIPPED FIRST, and the reason this test exists.

    `web_search.search` returns SearchResult dataclasses. An `isinstance(h, dict)`
    filter drops all of them and the route reports "no issuer document" while the
    search backend is perfectly healthy — an invisible failure.
    """
    _patch_search(monkeypatch, results=[_sr(ISSUER_URL)])
    out = asyncio.run(fh._search_issuer_domain_documents(BABCOCK))
    assert out, "SearchResult dataclasses must not be silently dropped"
    assert isinstance(out[0], dict), (
        "downstream extract_issuer_financials calls .get() — results must be dicts")
    assert out[0].get("url") == ISSUER_URL


def test_rf3135_returns_dicts_extract_can_consume(monkeypatch):
    """Contract check against the real consumer's access pattern."""
    _patch_search(monkeypatch, results=[_sr(ISSUER_URL)])
    out = asyncio.run(fh._search_issuer_domain_documents(BABCOCK))
    s = out[0]
    assert s.get("url") and s.get("title") is not None
    # This is precisely what extract_issuer_financials does with each source.
    assert fh._issuer_domain_matches(str(s.get("url")), BABCOCK)


def test_rf3135_no_or_block_in_any_query():
    """R-F3051..R-F3056: Brave silently drops the quoted phrase when an OR-block is
    present, returning HTTP 200 with degraded results."""
    for tmpl in fh._ISSUER_DOC_QUERIES:
        assert " OR " not in tmpl, (
            f"OR-block reintroduced into an issuer query — Brave will silently drop "
            f"the quoted phrase: {tmpl}")
        assert "{name}" in tmpl


def test_rf3135_stops_at_the_first_productive_query(monkeypatch):
    """Cost discipline (§17): don't run three searches when the first one answers."""
    q1 = fh._ISSUER_DOC_QUERIES[0].format(name=BABCOCK)
    calls = _patch_search(monkeypatch, results_by_query={q1: [_sr(ISSUER_URL)]})
    out = asyncio.run(fh._search_issuer_domain_documents(BABCOCK))
    assert out
    assert len(calls) == 1, f"expected 1 search, made {len(calls)}: {calls}"


def test_rf3135_falls_through_to_later_queries(monkeypatch):
    """...but a barren first query must not end the search."""
    q3 = fh._ISSUER_DOC_QUERIES[2].format(name=BABCOCK)
    calls = _patch_search(monkeypatch, results_by_query={q3: [_sr(ISSUER_URL)]})
    out = asyncio.run(fh._search_issuer_domain_documents(BABCOCK))
    assert out, "a document reachable only by the third query must still be found"
    assert len(calls) == 3


def test_rf3135_memory_rag_hits_are_excluded(monkeypatch):
    """memory:// is ARIA's own RAG — that is ARIA quoting herself, not the issuer
    publishing (R-F2346). A self-citation must never evidence a solvency verdict."""
    _patch_search(monkeypatch, results=[
        _sr("memory://582d7291606e"), _sr(ISSUER_URL)])
    out = asyncio.run(fh._search_issuer_domain_documents(BABCOCK))
    assert [s.get("url") for s in out] == [ISSUER_URL]


def test_rf3135_search_failure_is_survivable(monkeypatch):
    """A dead backend must yield no documents, never an exception into the DD layer."""
    async def _boom(query, **kw):
        raise RuntimeError("backend down")
    import aria_service.intel.web_search as ws
    monkeypatch.setattr(ws, "search", _boom)
    assert asyncio.run(fh._search_issuer_domain_documents(BABCOCK)) == []


def test_rf3135_capability_enrich_prefers_issuer_document(monkeypatch):
    """END TO END on the registered capability: aggregators from the footprint plus an
    issuer document from the new search — the issuer document must reach extraction
    FIRST, because extract_issuer_financials takes the first source passing G1."""
    _patch_search(monkeypatch, results=[_sr(ISSUER_URL)])

    seen = {}

    async def _fake_extract(sources, name, llm, **kw):
        seen["first_url"] = str((sources[0] or {}).get("url") or "")
        seen["count"] = len(sources)
        return {"ok": False, "reason": "probe"}

    monkeypatch.setattr(fh, "extract_issuer_financials", _fake_extract)
    monkeypatch.setattr(fh, "_dd_llm_for_capability", lambda: object())

    result = {
        "data_available": False,
        "has_financials": False,
        "search_footprint": {"found": True,
                             "sources": [{"url": u} for u in AGGREGATOR_URLS]},
    }
    ok = asyncio.run(fh._enrich_with_issuer_report(result, BABCOCK, "GB", ""))
    assert ok is False                      # the stub refuses; that is fine
    assert seen.get("first_url") == ISSUER_URL, (
        f"the issuer document must be offered first, got {seen.get('first_url')!r}")
    assert seen.get("count") == len(AGGREGATOR_URLS) + 1, (
        "footprint sources must be preserved, not replaced")
    assert (result.get("issuer_document_search") or {}).get("found") == 1


def test_rf3135_capability_no_issuer_doc_leaves_behaviour_unchanged(monkeypatch):
    """The fix is additive: when no issuer document exists, the route behaves exactly
    as before rather than regressing the aggregator path."""
    _patch_search(monkeypatch, results=[_sr(u) for u in AGGREGATOR_URLS])

    seen = {}

    async def _fake_extract(sources, name, llm, **kw):
        seen["urls"] = [str((s or {}).get("url") or "") for s in sources]
        return {"ok": False, "reason": "probe"}

    monkeypatch.setattr(fh, "extract_issuer_financials", _fake_extract)
    monkeypatch.setattr(fh, "_dd_llm_for_capability", lambda: object())

    result = {"data_available": False, "has_financials": False,
              "search_footprint": {"found": True,
                                   "sources": [{"url": u} for u in AGGREGATOR_URLS]}}
    asyncio.run(fh._enrich_with_issuer_report(result, BABCOCK, "GB", ""))
    assert seen.get("urls") == AGGREGATOR_URLS
    assert "issuer_document_search" not in result


def test_rf3135_already_answered_route_is_not_disturbed(monkeypatch):
    """A stronger route (registry accounts) already answered — do not re-search."""
    calls = _patch_search(monkeypatch, results=[_sr(ISSUER_URL)])
    result = {"data_available": True, "has_financials": True}
    assert asyncio.run(fh._enrich_with_issuer_report(result, BABCOCK, "GB", "")) is False
    assert calls == [], "no search may run once financials are already established"
