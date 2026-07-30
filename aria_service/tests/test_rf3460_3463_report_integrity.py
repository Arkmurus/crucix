"""R-F3460..R-F3463 — four report-integrity defects from the Babcock DD.

Each is small on its own and each puts a false or unusable statement in front of a
customer. All four come from one delivered report on Babcock International Group PLC.

R-F3460 — PROVENANCE. "Financial health: STRONG ... read from the issuer's own published
report (Annual Report and Financial Statements 2026) ... Source: sec_edgar_financials",
printed in a report that ALSO said EDGAR holds only ADR registration forms for this entity
and "does NOT evidence this entity's financials". The label was hardcoded for every
finding regardless of origin, so the report attributed its numbers to the one source it
had just told the reader was empty.

R-F3461 — FALSE AMBIGUITY. "Company name is AMBIGUOUS — subject was inferred, not
confirmed. 3 candidates share the top name match (1.00)". `_company_name_match` is a
Jaccard over DISTINCTIVE tokens, which deliberately discards word ORDER and corporate
suffixes — so BABCOCK INTERNATIONAL GROUP PLC, BABCOCK GROUP INTERNATIONAL LIMITED and
BABCOCK INTERNATIONAL LIMITED all reduce to {babcock, international} and tie at 1.00. The
register held a VERBATIM match for the name supplied and the report called it inferred.

R-F3462 — MISSING VINTAGE. "central-govt debt 130.7% of GDP" with no year. The request
uses `mrnev=1` (most recent NON-EMPTY), so the figure is routinely several years old. The
year was fetched — `fetch_country_indicators` records it per observation — and then
discarded by `_val()`, so no consumer could state it.

R-F3463 — ANSWERED WITH NOTHING SHOWN. "Ownership and control: ANSWERED" beside a Network
section whose entire visible content was "UBO chain nodes traversed 11". The GATE is
sound (R-F2793 requires substance, R-F3027 disqualifies an untraversed controller) — the
evidence simply was never rendered, so the reader was asked to trust a verdict rather than
shown what produced it. The fix surfaces the holders; it does NOT touch the gate.
"""
from __future__ import annotations

import pytest


# ── R-F3460 — provenance label ───────────────────────────────────────────────

def test_rf3460_issuer_report_figures_are_not_labelled_sec_edgar():
    from aria_service.intel.financial_health import financial_health_findings
    result = {
        "data_available": True, "has_financials": True,
        "issuer_report_verified": True, "health_verdict": "STRONG",
        "summary": "Financial position read from the issuer's own published report ...",
    }
    findings = financial_health_findings(result)
    health = [f for f in findings if f["title"].startswith("Financial health")]
    assert health, findings
    assert health[0]["source"] != "sec_edgar_financials", (
        "figures read from the issuer's annual report are still attributed to EDGAR")
    assert health[0]["source"] == "issuer_annual_report"
    assert "issuer" in health[0]["title"].lower(), (
        "the title must name the source too; an unqualified verdict reads as filings-derived")


def test_rf3460_sec_derived_findings_keep_their_label():
    """The asymmetry guard: genuine EDGAR findings must NOT be relabelled."""
    from aria_service.intel.financial_health import financial_health_findings
    result = {"data_available": True, "has_financials": True, "health_verdict": "STABLE",
              "matched_title": "BABCOCK PLC", "summary": "x"}
    findings = financial_health_findings(result)
    health = [f for f in findings if f["title"].startswith("Financial health")]
    assert health[0]["source"] == "sec_edgar_financials"


def test_rf3460_uk_accounts_still_cite_companies_house():
    from aria_service.intel.financial_health import financial_health_findings
    result = {"data_available": True, "has_financials": True, "health_verdict": "STABLE",
              "uk_balance_sheet": {"net_assets": 1}, "summary": "x"}
    findings = financial_health_findings(result)
    health = [f for f in findings if f["title"].startswith("Financial health")]
    assert health[0]["source"] == "companies_house_accounts"


# ── R-F3461 — exact legal-name match is not ambiguity ────────────────────────

_BABCOCK = [
    {"company_number": "02342138", "title": "BABCOCK INTERNATIONAL GROUP PLC",
     "company_status": "active", "date_of_creation": "1989-02-01"},
    {"company_number": "02554204", "title": "BABCOCK GROUP INTERNATIONAL LIMITED",
     "company_status": "dissolved", "date_of_creation": "1990-01-01"},
    {"company_number": "00065805", "title": "BABCOCK INTERNATIONAL LIMITED",
     "company_status": "active", "date_of_creation": "1900-01-01"},
]


def test_rf3461_the_token_scorer_really_does_tie_all_three():
    """Establish the CAUSE before asserting the fix, so this test documents why the
    exact-name comparison had to be added rather than the scorer 'improved'."""
    from aria_service.intel.companies_house import _company_name_match
    q = "Babcock International Group PLC"
    scores = {r["title"]: _company_name_match(q, r["title"]) for r in _BABCOCK}
    assert len(set(round(s, 6) for s in scores.values())) == 1, scores
    assert all(s == 1.0 for s in scores.values()), scores


def test_rf3461_capability_an_exact_match_is_not_reported_ambiguous():
    from aria_service.intel.companies_house import _pick_best_company
    decision: dict = {}
    winner = _pick_best_company("Babcock International Group PLC", _BABCOCK, decision)
    assert winner["company_number"] == "02342138"
    assert decision["ambiguous"] is False, (
        f"an exact full legal-name match was still reported ambiguous: "
        f"{decision.get('reasons')}")


def test_rf3461_a_genuine_partial_match_stays_ambiguous():
    """The never-clamp direction: this must not silence real ambiguity."""
    from aria_service.intel.companies_house import _pick_best_company
    decision: dict = {}
    _pick_best_company("Babcock", _BABCOCK, decision)
    assert decision["ambiguous"] is True, decision


def test_rf3461_two_companies_with_the_same_legal_name_are_ambiguous():
    """Rare but real, and the register itself cannot distinguish them."""
    from aria_service.intel.companies_house import _pick_best_company
    rows = [
        {"company_number": "111", "title": "ACME LTD", "company_status": "active"},
        {"company_number": "222", "title": "Acme Ltd", "company_status": "active"},
    ]
    decision: dict = {}
    _pick_best_company("Acme Ltd", rows, decision)
    assert decision["ambiguous"] is True
    assert any("exact legal name" in r for r in decision["reasons"]), decision["reasons"]


def test_rf3461_punctuation_and_case_do_not_defeat_the_match():
    from aria_service.intel.companies_house import _exact_legal_name_matches
    # "P.L.C." IS the same legal name as "PLC" — the register and the person typing the
    # query punctuate differently, and treating that as a different company would
    # reintroduce the false ambiguity from the other direction.
    assert _exact_legal_name_matches("Babcock International Group PLC",
                                     "BABCOCK INTERNATIONAL GROUP P.L.C.")
    assert _exact_legal_name_matches("Acme Ltd.", "ACME LTD")
    # A different name is still different: normalisation must not erase distinctions.
    assert not _exact_legal_name_matches("Babcock International Group PLC",
                                         "BABCOCK INTERNATIONAL LIMITED")
    assert not _exact_legal_name_matches("", "ACME LTD")


# ── R-F3462 — indicator vintage ──────────────────────────────────────────────

def test_rf3462_the_overlay_carries_the_year():
    """The year existed upstream and was dropped by `_val`; assert it now survives."""
    import asyncio
    from aria_service.intel.sources import worldbank_indicators as wbi

    async def _fake_fetch(*a, **k):
        return {"ok": True, "country_code": "GB", "source_url": "https://x",
                "indicators": {
                    "GC.DOD.TOTL.GD.ZS": [{"value": 130.7, "year": "2022"}],
                    "NY.GDP.MKTP.CD": [{"value": 3.1e12, "year": "2023"}],
                }}

    orig = wbi.fetch_country_indicators
    wbi.fetch_country_indicators = _fake_fetch
    try:
        out = asyncio.run(wbi.country_risk_overlay("GB"))
    finally:
        wbi.fetch_country_indicators = orig

    assert out["macro"]["debt_to_gdp_pct"] == 130.7
    assert out["vintage"]["debt_to_gdp_pct"] == "2022", (
        "the vintage is still discarded, so the report cannot date the figure")
    # Different indicators legitimately have different vintages — that is the point.
    assert out["vintage"]["gdp_usd"] == "2023"


def test_rf3462_the_report_states_the_year_or_says_it_is_unknown():
    import pathlib
    src = (pathlib.Path(__file__).resolve().parents[1]
           / "intel" / "dd_orchestrator.py").read_text(encoding="utf-8", errors="replace")
    assert "R-F3462" in src
    assert "the most recent year the World Bank publishes" in src
    assert "year not reported by the source on this run" in src, (
        "a missing vintage must be stated, not silently omitted — otherwise an undated "
        "figure is indistinguishable from a dated one")


# ── R-F3463 — show the evidence behind ANSWERED ──────────────────────────────

def test_rf3463_named_holders_are_extracted_for_display():
    from aria_service.intel.dd_schema import _named_holders, _has_named_holder
    holders = [{"name": "Vanguard Group Inc"}, {"name": "BlackRock Inc"}]
    assert _has_named_holder(holders) is True
    assert _named_holders(holders) == ["Vanguard Group Inc", "BlackRock Inc"]


def test_rf3463_display_and_gate_read_the_same_thing():
    """The property that matters: what is SHOWN can never disagree with what was DECIDED.
    Anything the gate rejects must produce no displayed evidence either."""
    from aria_service.intel.dd_schema import _named_holders, _has_named_holder
    for junk in ([], [{}], ["x"], None, [{"name": ""}], [{"name": "n/a"}]):
        assert bool(_named_holders(junk)) == _has_named_holder(junk), junk


def test_rf3463_duplicates_are_collapsed():
    from aria_service.intel.dd_schema import _named_holders
    assert _named_holders([{"name": "A Ltd"}, {"name": "A Ltd"}]) == ["A Ltd"]
