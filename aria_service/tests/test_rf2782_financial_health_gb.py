"""R-F2782 phase 1 — GB registry accounts must reach financial_health.

`assess()` ran Vault -> SEC EDGAR -> web footprint. SEC EDGAR is US-only and the
footprint finds links without extracting figures, so EVERY non-US entity landed
on an evidence-free UNKNOWN. A live deep DD on BAE Systems plc (FTSE-100, fully
public UK filings) returned financial capacity UNKNOWN for exactly that reason.

These tests drive the real `assess()` (§3c) with the network boundary stubbed.

THE CENTRAL ASSERTION IS THAT THE VERDICT DOES **NOT** IMPROVE. Filing metadata
carries no revenue or solvency figures, so it raises the EVIDENCE grade while
`health_verdict` stays UNKNOWN. If a future change makes these tests fail by
turning UNKNOWN into STABLE/STRONG on filing dates alone, that change is a false
clean and must be rejected — figures come from the CH Document API in phase 2.
"""
from __future__ import annotations

import pytest

from aria_service.intel import financial_health as fh

_GB_ACCOUNTS = {
    "filed": True,
    "last_made_up_to": "2025-12-31",
    "last_type": "full",
    "period_start_on": "2025-01-01",
    "period_end_on": "2025-12-31",
    "next_due": "2026-09-30",
    "next_made_up_to": "",
    "overdue": False,
    "accounting_reference_date": {},
    "distress_flags": [],
    "has_figures": False,
}


@pytest.fixture(autouse=True)
def _no_vault_no_footprint(monkeypatch):
    """Isolate step 2b: no vault hit, no web footprint, SEC finds nothing."""
    async def _sec_miss(name, cik=None):
        return {"source": "financial_health", "entity": name, "data_available": False,
                "health_verdict": "UNKNOWN", "financials": {}, "ratios": {},
                "reason": "not a US-listed filer (no SEC EDGAR match)",
                "summary": f"{name} is not found in SEC EDGAR (not US-listed)."}

    async def _no_footprint(name, jurisdiction_iso2=""):
        return {"found": False, "sources": []}

    monkeypatch.setattr(fh, "_assess_sec_edgar", _sec_miss)
    monkeypatch.setattr(fh, "_search_financial_footprint", _no_footprint)


def _stub_ch(monkeypatch, *, accounts=None, enabled=True, profile=True):
    """Stub companies_house at the module boundary assess() imports."""
    import aria_service.intel.companies_house as ch

    monkeypatch.setattr(ch, "is_enabled", lambda: enabled)

    async def _search(query, limit=5):
        return [{"company_number": "01470151", "title": query.upper(),
                 "company_status": "active"}]

    async def _profile(number):
        if not profile:
            return None
        return {"company_number": "01470151", "company_name": "BAE SYSTEMS PLC",
                "company_status": "active",
                "accounts": accounts if accounts is not None else dict(_GB_ACCOUNTS)}

    monkeypatch.setattr(ch, "search_companies", _search)
    monkeypatch.setattr(ch, "get_company_profile", _profile)


async def test_gb_entity_gains_registry_accounts_evidence(monkeypatch):
    """The regression: GB filings must now reach the result."""
    _stub_ch(monkeypatch)
    out = await fh.assess("BAE Systems plc", jurisdiction_iso2="GB", use_vault=False)

    reg = out.get("registry_accounts")
    assert reg, "GB registry accounts did not reach financial_health (R-F2782)"
    assert reg["source"] == "companies_house"
    assert reg["company_number"] == "01470151"
    assert reg["accounts"]["last_made_up_to"] == "2025-12-31"
    assert reg["accounts"]["last_type"] == "full"
    # Citable primary source, not an assertion.
    assert reg["source_url"].startswith("https://find-and-update.company-information")
    assert "2025-12-31" in out["summary"]


async def test_verdict_stays_unknown_on_metadata_alone(monkeypatch):
    """★ The honesty constraint. Metadata must not manufacture a clean bill."""
    _stub_ch(monkeypatch)
    out = await fh.assess("BAE Systems plc", jurisdiction_iso2="GB", use_vault=False)

    assert out["health_verdict"] == "UNKNOWN", (
        "filing metadata must NOT produce a health verdict — no revenue or "
        "solvency figures were extracted (R-F2782 phase 2 supplies those)"
    )
    assert out.get("data_available") is False
    assert out["registry_accounts"]["has_figures"] is False
    assert "UNKNOWN" in out["summary"]
    assert "not a solvency assessment" in out["summary"]


async def test_overdue_accounts_surface_as_distress(monkeypatch):
    _stub_ch(monkeypatch, accounts={**_GB_ACCOUNTS, "overdue": True,
                                    "distress_flags": ["accounts_overdue"]})
    out = await fh.assess("Some GB Co", jurisdiction_iso2="GB", use_vault=False)

    assert "accounts_overdue" in out["distress_flags"]
    assert "OVERDUE" in out["summary"]
    # Still not a verdict — a distress signal does not make figures appear.
    assert out["health_verdict"] == "UNKNOWN"


async def test_no_accounts_filed_does_not_read_clean(monkeypatch):
    _stub_ch(monkeypatch, accounts={**_GB_ACCOUNTS, "filed": False,
                                    "last_made_up_to": "", "last_type": "",
                                    "distress_flags": ["no_accounts_filed"]})
    out = await fh.assess("Shell Co", jurisdiction_iso2="GB", use_vault=False)

    assert "NO accounts filed" in out["summary"]
    assert "no_accounts_filed" in out["distress_flags"]
    assert out["health_verdict"] == "UNKNOWN"


async def test_non_gb_jurisdiction_does_not_consult_companies_house(monkeypatch):
    """CH covers GB only — a DE entity must not be given UK filings."""
    import aria_service.intel.companies_house as ch
    called = []

    async def _boom(*a, **k):
        called.append(1)
        raise AssertionError("Companies House consulted for a non-GB entity")

    monkeypatch.setattr(ch, "is_enabled", lambda: True)
    monkeypatch.setattr(ch, "search_companies", _boom)
    monkeypatch.setattr(ch, "get_company_profile", _boom)

    out = await fh.assess("Siemens AG", jurisdiction_iso2="DE", use_vault=False)
    assert not called
    assert "registry_accounts" not in out
    assert out["health_verdict"] == "UNKNOWN"


async def test_unknown_jurisdiction_does_not_guess_gb(monkeypatch):
    """An empty jurisdiction must not be treated as GB."""
    import aria_service.intel.companies_house as ch

    async def _boom(*a, **k):
        raise AssertionError("guessed GB for an unknown jurisdiction")

    monkeypatch.setattr(ch, "is_enabled", lambda: True)
    monkeypatch.setattr(ch, "search_companies", _boom)
    monkeypatch.setattr(ch, "get_company_profile", _boom)

    out = await fh.assess("Mystery Co", jurisdiction_iso2="", use_vault=False)
    assert "registry_accounts" not in out


@pytest.mark.parametrize("kwargs", [
    {"enabled": False},          # CH disabled
    {"profile": False},          # CH returns nothing / unavailable
])
async def test_companies_house_unavailable_degrades_honestly(monkeypatch, kwargs):
    """Unavailable registry = data gap, never a clean result (R-F2719)."""
    _stub_ch(monkeypatch, **kwargs)
    out = await fh.assess("BAE Systems plc", jurisdiction_iso2="GB", use_vault=False)

    assert "registry_accounts" not in out
    assert out["health_verdict"] == "UNKNOWN"


async def test_is_gb_accepts_common_spellings():
    assert fh._is_gb("GB") and fh._is_gb("uk") and fh._is_gb(" GBR ")
    assert not fh._is_gb("") and not fh._is_gb("DE") and not fh._is_gb(None)
