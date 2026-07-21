"""R-F2817 — the DD vault must not mask a capability upgrade.

`assess()` serves any vault profile younger than `max_age_days` (30) and returns
immediately. The vault carries no capability/schema version, so a profile written
BEFORE a new evidence source existed kept suppressing that source for the whole
freshness window.

This was not theoretical. Two live production DDs after R-F2782 shipped —
BAE Systems plc (`vault_age_days: 1.1`) and Rolls-Royce Holdings plc — both came
back `from_vault: True` with NO `registry_accounts`, because both short-circuited
at step 1. The feature was live and correct and never ran.

The fix backfills missing evidence on a vault READ and re-persists it, so the
enrichment happens once (§15 pay-once) rather than on every assessment.

These tests drive the real `assess()` down the CACHED path — the one that was
broken. The pre-fix code returns the cached dict untouched, so the first test
fails against the parent commit.

The honesty constraint from R-F2782 still holds here and is asserted: enriching a
cached profile adds EVIDENCE, never a verdict.
"""
from __future__ import annotations

import time

import pytest

from aria_service.intel import financial_health as fh

_ACCOUNTS = {
    "filed": True, "last_made_up_to": "2025-12-31", "last_type": "full",
    "period_start_on": "", "period_end_on": "", "next_due": "2026-09-30",
    "next_made_up_to": "", "overdue": False, "accounting_reference_date": {},
    "distress_flags": [], "has_figures": False,
}


def _cached_profile(**over):
    """A vault profile as written BEFORE R-F2782 existed — no registry_accounts."""
    p = {
        "source": "financial_health", "entity": "BAE Systems plc",
        "data_available": False, "health_verdict": "UNKNOWN",
        "financials": {}, "ratios": {},
        "summary": "Not found in SEC EDGAR (not US-listed).",
        "_vault_updated_at": time.time() - 86400 * 1.1,   # 1.1 days — the live case
    }
    p.update(over)
    return p


class _FakeVault:
    def __init__(self, profile):
        self.profile = profile
        self.writes = []

    def get_financial_profile(self, canonical):
        return self.profile

    def set_financial_profile(self, canonical, result, entity_name="", jurisdiction=""):
        self.writes.append(result)


@pytest.fixture
def _vault(monkeypatch):
    """Install a fake vault; returns a setter so each test picks the profile."""
    import aria_service.intel.dd_vault as dv
    holder = {}

    def _install(profile):
        v = _FakeVault(profile)
        holder["v"] = v
        monkeypatch.setattr(dv, "get_vault", lambda: v)
        return v
    return _install


@pytest.fixture(autouse=True)
def _stub_ch(monkeypatch):
    """Companies House returns filings; no network."""
    import aria_service.intel.companies_house as ch
    monkeypatch.setattr(ch, "is_enabled", lambda: True)

    async def _search(query, limit=5):
        return [{"company_number": "01470151"}]

    async def _profile(number):
        return {"company_number": "01470151", "company_name": "BAE SYSTEMS PLC",
                "company_status": "active", "accounts": dict(_ACCOUNTS)}

    monkeypatch.setattr(ch, "search_companies", _search)
    monkeypatch.setattr(ch, "get_company_profile", _profile)


async def test_cached_gb_profile_is_backfilled(_vault):
    """THE REGRESSION — a pre-R-F2782 cached profile must gain the evidence."""
    _vault(_cached_profile())
    out = await fh.assess("BAE Systems plc", jurisdiction_iso2="GB")

    assert out["from_vault"] is True, "precondition: this must be the cached path"
    assert out.get("registry_accounts"), (
        "cached profile was served without registry_accounts — the vault is "
        "still masking the capability upgrade (R-F2817)"
    )
    assert out["registry_accounts"]["accounts"]["last_made_up_to"] == "2025-12-31"
    assert out.get("vault_enriched") is True
    assert "2025-12-31" in out["summary"]


async def test_enrichment_is_persisted_so_it_stays_pay_once(_vault):
    """§15 — backfill once and write it back, don't re-fetch every assessment."""
    v = _vault(_cached_profile())
    await fh.assess("BAE Systems plc", jurisdiction_iso2="GB")

    assert v.writes, "enriched profile was not written back to the vault"
    assert v.writes[-1].get("registry_accounts")


async def test_already_enriched_cache_is_not_refetched(_vault):
    """A profile that already has the evidence must not hit CH again."""
    import aria_service.intel.companies_house as ch
    v = _vault(_cached_profile(registry_accounts={"source": "companies_house",
                                                  "accounts": dict(_ACCOUNTS)}))

    async def _boom(*a, **k):
        raise AssertionError("re-fetched Companies House for an already-enriched profile")
    ch.search_companies = _boom
    ch.get_company_profile = _boom

    out = await fh.assess("BAE Systems plc", jurisdiction_iso2="GB")
    assert out["from_vault"] is True
    assert not v.writes, "no re-write expected when nothing changed"
    assert out.get("vault_enriched") is None


async def test_non_gb_cached_profile_is_untouched(_vault):
    """CH is GB-only — a cached DE profile must not gain UK filings."""
    v = _vault(_cached_profile(entity="Siemens AG"))
    out = await fh.assess("Siemens AG", jurisdiction_iso2="DE")

    assert out["from_vault"] is True
    assert "registry_accounts" not in out
    assert not v.writes


async def test_backfill_does_not_manufacture_a_verdict(_vault):
    """★ The R-F2782 honesty constraint, on the cached path too."""
    _vault(_cached_profile())
    out = await fh.assess("BAE Systems plc", jurisdiction_iso2="GB")

    assert out["health_verdict"] == "UNKNOWN"
    assert out.get("data_available") is False
    assert out["registry_accounts"]["has_figures"] is False
    assert "not a solvency assessment" in out["summary"]


async def test_stale_cache_still_falls_through_to_a_full_assessment(_vault, monkeypatch):
    """Beyond max_age_days the vault must not short-circuit at all."""
    _vault(_cached_profile(_vault_updated_at=time.time() - 86400 * 400))

    async def _sec_miss(name, cik=None):
        return {"source": "financial_health", "entity": name, "data_available": False,
                "health_verdict": "UNKNOWN", "financials": {}, "ratios": {},
                "summary": "no SEC match"}

    async def _no_footprint(name, jurisdiction_iso2=""):
        return {"found": False, "sources": []}

    monkeypatch.setattr(fh, "_assess_sec_edgar", _sec_miss)
    monkeypatch.setattr(fh, "_search_financial_footprint", _no_footprint)

    out = await fh.assess("BAE Systems plc", jurisdiction_iso2="GB")
    assert out.get("from_vault") is not True
    # the fresh path still enriches
    assert out.get("registry_accounts")


async def test_vault_write_failure_does_not_lose_the_enrichment(_vault, monkeypatch):
    """A failed re-persist must still return enriched data to the caller."""
    v = _vault(_cached_profile())

    def _boom(*a, **k):
        raise RuntimeError("vault down")
    v.set_financial_profile = _boom

    out = await fh.assess("BAE Systems plc", jurisdiction_iso2="GB")
    assert out.get("registry_accounts"), "enrichment lost when the vault write failed"
    assert out["health_verdict"] == "UNKNOWN"
