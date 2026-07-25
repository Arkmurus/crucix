"""R-F3017 — an UNKNOWN with a NAMED obstacle.

THE DEFECT. Every large listed PLC's report said only "financial capacity is
unknown". That sentence is indistinguishable from three different facts:
  (a) the company filed no accounts,
  (b) accounts were filed but are not machine-readable,
  (c) we never looked.
Only (b) is true for a PLC, and it is the one a reader can act on.

PROVEN LIVE 2026-07-25 (why (b) is not a guess): Companies House holds Cohort PLC
(05684823) 2025 group accounts as a 129-page document whose only resource is
`application/pdf`, produced by `libtiff / tiff2pdf` — a TIFF SCAN with no text
layer and no iXBRL. No parser reaches figures in it. So the honest report names
the filing AND the obstacle, and financial_capacity stays UNRESOLVED — this never
answers capacity, it explains why capacity cannot be answered.
"""
import asyncio
from unittest.mock import patch, AsyncMock

from aria_service.intel import companies_house as ch
from aria_service.intel import financial_health as fh
from aria_service.intel.dd_schema import _dd_decision_readiness


def test_rf3017_explanation_names_filing_and_obstacle():
    txt = fh._figures_unavailable_explanation({
        "unavailable_reason": "accounts_not_machine_readable",
        "made_up_to": "2025-04-30", "accounts_type": "group accounts", "pages": 129,
    })
    assert "2025-04-30" in txt and "group accounts" in txt and "129" in txt
    assert "scanned" in txt.lower() and "ixbrl" in txt.lower()
    assert "NOT assessed" in txt, "must not imply solvency was judged"


def test_rf3017_ixbrl_without_tags_is_a_different_statement():
    """A machine-readable filing we could not read tags in is NOT a scan. Saying
    'scanned PDF' about it would be a fabricated detail."""
    txt = fh._figures_unavailable_explanation({
        "unavailable_reason": "ixbrl_no_balance_sheet_figures", "made_up_to": "2025-01-31",
    })
    assert "machine-readable" in txt and "scanned" not in txt.lower()


def test_rf3017_enrichment_records_evidenced_reason_without_answering_capacity():
    async def go():
        result = {"data_available": False, "has_financials": False,
                  "health_verdict": "UNKNOWN", "summary": ""}
        pdf_only = {
            "company_number": "05684823", "figures": None,
            "unavailable_reason": "accounts_not_machine_readable",
            "made_up_to": "2025-04-30", "accounts_type": "group accounts",
            "document_formats": ["application/pdf"], "pages": 129,
            "source_url": "https://find-and-update.company-information.service.gov.uk/x",
        }
        with patch.object(ch, "is_enabled", return_value=True), \
             patch.object(ch, "fetch_accounts_figures", new=AsyncMock(return_value=pdf_only)):
            ok = await fh._enrich_with_registry_figures(result, "Cohort PLC", "GB", "05684823")
        assert ok is True, "recording WHY is evidence added"
        # never-false-clean: an explanation is not an answer
        assert result["data_available"] is False and result["has_financials"] is False
        assert result["health_verdict"] == "UNKNOWN"
        unavail = result["financial_figures_unavailable"]
        assert unavail["reason"] == "accounts_not_machine_readable"
        assert unavail["made_up_to"] == "2025-04-30" and unavail["pages"] == 129
        assert "2025-04-30" in result["summary"]
    asyncio.run(go())


def test_rf3017_blocker_states_the_reason_and_stays_unresolved():
    """The user-visible outcome: the readiness blocker a customer reads."""
    report = {"compliance": {"financial_health": {
        "data_available": False, "health_verdict": "UNKNOWN",
        "financial_figures_unavailable": {
            "reason": "accounts_not_machine_readable",
            "explanation": ("Companies House holds accounts made up to 2025-04-30 (group "
                            "accounts), 129 pages, filed as a scanned/PDF document with no "
                            "machine-readable (iXBRL) figures — solvency was NOT assessed."),
        }}}}
    q = _dd_decision_readiness(report)["questions"]["financial_capacity"]
    assert q["answered"] is False and q["status"] == "UNRESOLVED", "still not answered"
    assert "scanned" in q["blocker"], "the blocker must NAME the obstacle"
    assert q["blocker"] != "financial capacity is unknown", "the bare wording is the defect"


def test_rf3017_bare_unknown_survives_when_nothing_is_known():
    """No evidence about WHY → do not invent one."""
    report = {"compliance": {"financial_health": {"data_available": False,
                                                  "health_verdict": "UNKNOWN"}}}
    q = _dd_decision_readiness(report)["questions"]["financial_capacity"]
    assert q["blocker"] == "financial capacity is unknown"


def test_rf3017_existing_figures_callers_are_unaffected():
    """The new dict shape must not look like a success to a `figures` gate."""
    async def go():
        result = {"data_available": False, "has_financials": False, "health_verdict": "UNKNOWN"}
        no_reason = {"company_number": "1", "figures": None}   # unknown-unknown
        with patch.object(ch, "is_enabled", return_value=True), \
             patch.object(ch, "fetch_accounts_figures", new=AsyncMock(return_value=no_reason)):
            ok = await fh._enrich_with_registry_figures(result, "X Ltd", "GB", "1")
        assert ok is False and "financial_figures_unavailable" not in result
    asyncio.run(go())
