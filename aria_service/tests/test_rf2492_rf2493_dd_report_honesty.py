"""R-F2492 + R-F2493 — DD report HONESTY fixes (codex review of the Modirum report).

R-F2492: the financial-footprint search must not render generic web hits as
"financial references" — social/profile domains (incl. LinkedIn *company* pages) are
blocked wholesale, and a real financial-DOCUMENT term is required (not a bare generic
token like "revenue"/"results" that matches marketing pages).

R-F2493: a report whose own verdict is INSUFFICIENT EVIDENCE (confidence gate fired —
via the GREEN gate OR a data-starved AMBER) must be hard-capped to Grade D. Before,
a data-starved INSUFFICIENT report never set confidence_gate_triggered, so the grade
could still be C/60 (the Modirum symptom).
"""
import asyncio

import aria_service.intel.web_search as _ws
import aria_service.intel.financial_health as fh
from aria_service.intel.dd_schema import _dd_quality_assessment


class _Hit:
    def __init__(self, url, title, snippet):
        self.url, self.title, self.snippet = url, title, snippet


def _run_footprint(hits):
    async def _fake_search(q, max_results=10):
        return hits
    orig = _ws.search
    _ws.search = _fake_search
    try:
        return asyncio.run(fh._search_financial_footprint("Modirum Gespi", "BR"))
    finally:
        _ws.search = orig


# ---------- R-F2492: financial-reference classification ----------

def test_linkedin_company_page_not_a_financial_reference():
    hits = [_Hit("https://www.linkedin.com/company/modirum-gespi",
                 "Modirum Gespi | LinkedIn", "Modirum Gespi revenue and team on LinkedIn")]
    out = _run_footprint(hits)
    urls = [s["url"] for s in out["sources"]]
    assert not any("linkedin.com" in u for u in urls), f"LinkedIn must be blocked, got {urls}"
    assert out["found"] is False, out


def test_generic_hit_without_financial_document_term_rejected():
    # kara5.com — mentions the entity + a WEAK token ("results"), but is not a filing.
    hits = [_Hit("https://kara5.com/modirum-gespi", "Modirum Gespi", "Modirum Gespi results page")]
    out = _run_footprint(hits)
    assert out["found"] is False, f"generic non-document hit must not be a financial reference: {out}"


def test_real_financial_document_accepted():
    hits = [_Hit("https://ri.modirum.com/annual-report-2024",
                 "Modirum Gespi Annual Report 2024",
                 "Audited financial statements and balance sheet for Modirum Gespi FY2024")]
    out = _run_footprint(hits)
    urls = [s["url"] for s in out["sources"]]
    assert out["found"] is True and any("annual-report" in u for u in urls), out


# ---------- R-F2493: INSUFFICIENT-EVIDENCE hard cap ----------

def _strong_report(confidence_gate: bool) -> dict:
    """A report with otherwise-strong evidence signals (would score high) — isolates
    the confidence-gate hard cap."""
    return {
        "confidence_gate_triggered": confidence_gate,
        "identity": {
            "registration_status": "Active", "incorporation_date": "2010-01-01",
            "directors": [{"name": "A"}],
            "sanctions_screen": {"verified_sources": ["OFAC"]},
        },
        "digital": {
            "source_tier_breakdown": {"T1": 5, "T2": 4, "T3": 2},
            "press_coverage": [{"t": i} for i in range(10)],
        },
        "verification": {"citations_checked": 10, "citations_grounded": 9},
        "adverse_media": {"ok": True, "findings_count": 2},
        "compliance": {"export_control": {"recommendation": "NLR"}},
    }


def test_insufficient_report_hard_capped_to_grade_D():
    gated = _dd_quality_assessment(_strong_report(confidence_gate=True))
    assert gated["score"] <= 40, gated
    assert gated["grade"] == "D", gated


def test_non_gated_report_not_capped():
    clean = _dd_quality_assessment(_strong_report(confidence_gate=False))
    assert clean["score"] > 40 and clean["grade"] in ("A", "B"), clean


if __name__ == "__main__":
    for fn in (test_linkedin_company_page_not_a_financial_reference,
               test_generic_hit_without_financial_document_term_rejected,
               test_real_financial_document_accepted,
               test_insufficient_report_hard_capped_to_grade_D,
               test_non_gated_report_not_capped):
        fn()
        print("PASS", fn.__name__)
    print("ALL PASS")
