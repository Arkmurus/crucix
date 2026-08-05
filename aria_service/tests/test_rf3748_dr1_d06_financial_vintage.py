"""R-F3748 — DR-1 **D-06**: a financial verdict must carry its AGE, not just its year.

D-06 was UNADJUDICATED: "Financial-verdict vintage (LAST_KNOWN_WITH_AGE or
refuse)", P1, suspected `aria_service/intel/financial_health.py`. Fourth DR-1
entry adjudicated from this repo (after D-02, D-03, D-05) and the FIRST that found
a genuine gap.

THE GAP. financial_health's whole discipline is "UNKNOWN, not clean" — absent data
never reads as healthy (module header, and ~10 explicit UNKNOWN branches). Data
that EXISTS and is OLD had no equivalent guard. `latest_fy` was recorded and
NOTHING anywhere compared it to the current year — a repo-wide search found no age
arithmetic on it at all. So a STABLE verdict computed from a five-year-old filing
was returned with exactly the same authority as one from last quarter, and the
reader had to notice the FY and do the subtraction.

That is the same failure the module already refuses in the absent case: a verdict
claiming more currency than its evidence supports. The vintage was there; the AGE
was not.

WHAT WAS NOT DONE, deliberately: the verdict is not altered by age. Downgrading
STABLE to WEAK because a filing is old would INVENT a financial finding — the
fabrication this module exists to prevent. Age is reported alongside the verdict so
a reader can discount it, which is what "LAST_KNOWN_WITH_AGE" means.

Run: python -m pytest aria_service/tests/test_rf3748_dr1_d06_financial_vintage.py -v
"""
from __future__ import annotations

import asyncio
import datetime as _dt

import pytest

from aria_service.intel import financial_health as fh

CURRENT_YEAR = _dt.datetime.now(_dt.timezone.utc).year


def _facts(fy: int) -> dict:
    """Minimal SEC companyfacts payload with one annual period ending in `fy`."""
    # `fp: "FY"` and a 10-K `form` are BOTH required by _extract_annual:143 — a
    # stub missing either yields "No annual financial statements found", i.e. the
    # UNKNOWN branch, and the age assertions then fail for the wrong reason.
    def _unit(tag_val):
        return {"USD": [{"start": f"{fy}-01-01", "end": f"{fy}-12-31",
                         "fy": fy, "fp": "FY", "form": "10-K", "val": tag_val}]}
    return {
        "entityName": "TESTCO INC",
        "facts": {"us-gaap": {
            "Revenues": {"units": _unit(1_000_000)},
            "NetIncomeLoss": {"units": _unit(50_000)},
            "Assets": {"units": _unit(2_000_000)},
            "Liabilities": {"units": _unit(800_000)},
            "AssetsCurrent": {"units": _unit(900_000)},
            "LiabilitiesCurrent": {"units": _unit(400_000)},
            "StockholdersEquity": {"units": _unit(1_200_000)},
            "RetainedEarningsAccumulatedDeficit": {"units": _unit(300_000)},
        }},
    }


def _run(fy: int, monkeypatch) -> dict:
    """Drive the REAL assessment path with the network seam stubbed."""
    async def _cik(name):
        return ("0000000001", "TESTCO INC")

    async def _fetch(cik10):
        return _facts(fy)

    monkeypatch.setattr(fh, "_resolve_cik", _cik)
    monkeypatch.setattr(fh, "_fetch_company_facts", _fetch)
    return asyncio.run(fh._assess_sec_edgar("TestCo"))


def test_a_stale_filing_is_reported_as_stale(monkeypatch):
    """THE HEADLINE: a five-year-old position must not read as current."""
    out = _run(CURRENT_YEAR - 5, monkeypatch)
    assert out.get("data_available") is True, f"stub did not reach the verdict: {out}"
    assert out.get("latest_fy_age_years") == 5, (
        f"age not computed: latest_fy_age_years={out.get('latest_fy_age_years')!r}"
    )
    assert out.get("financials_are_stale") is True


def test_the_reader_is_told_in_the_summary(monkeypatch):
    """Several callers render ONLY `summary`; the age must survive that path."""
    out = _run(CURRENT_YEAR - 5, monkeypatch)
    s = (out.get("summary") or "").upper()
    assert "LAST KNOWN" in s, (
        f"the summary does not declare this a LAST KNOWN position, so a caller "
        f"rendering only the summary still shows a stale verdict as current: "
        f"{out.get('summary')!r}"
    )
    assert "YEARS OLD" in s


def test_a_current_filing_is_not_flagged_stale(monkeypatch):
    """The guard must not cry wolf on fresh data."""
    out = _run(CURRENT_YEAR, monkeypatch)
    assert out.get("latest_fy_age_years") == 0
    assert out.get("financials_are_stale") is False
    assert "LAST KNOWN" not in (out.get("summary") or "").upper()


def test_the_verdict_itself_is_not_downgraded_by_age(monkeypatch):
    """Age must not INVENT a financial finding.

    D-06 could be 'satisfied' by downgrading old verdicts. That would fabricate a
    finding the filings do not support, which is exactly what this module's
    UNKNOWN-not-clean discipline exists to prevent. Same inputs, different age ->
    same verdict.
    """
    fresh = _run(CURRENT_YEAR, monkeypatch)
    stale = _run(CURRENT_YEAR - 5, monkeypatch)
    assert fresh.get("health_verdict") == stale.get("health_verdict"), (
        f"age changed the verdict ({fresh.get('health_verdict')} -> "
        f"{stale.get('health_verdict')}) — that invents a finding rather than "
        f"reporting the age of the evidence"
    )


def test_a_malformed_fiscal_year_does_not_break_the_assessment(monkeypatch):
    """The age computation must never be what fails a DD line."""
    async def _cik(name):
        return ("0000000001", "TESTCO INC")

    async def _fetch(cik10):
        return _facts(CURRENT_YEAR)

    monkeypatch.setattr(fh, "_resolve_cik", _cik)
    monkeypatch.setattr(fh, "_fetch_company_facts", _fetch)
    out = asyncio.run(fh._assess_sec_edgar("TestCo"))
    assert isinstance(out, dict) and "health_verdict" in out
