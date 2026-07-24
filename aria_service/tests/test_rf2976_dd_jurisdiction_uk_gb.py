"""R-F2976 — a caller-supplied jurisdiction alias ("UK") must normalize to ISO2
("GB") so the DD's GB-gated registry / officer / UBO paths fire.

Live DD 2026-07-24 (real report ARIA_DD_Silverbrook_Capital_Management_dd_0b6c78446376):
Silverbrook Capital Management is a real UK company (Companies House 04300718), but
the run was submitted with jurisdiction_iso2="UK". "UK" != "GB", so the Companies
House lookup, the officer walk, and the UBO walk (all gated on == "GB") were SKIPPED
→ no directors / no incorporation date → identity "incomplete" → confidence gate
stuck at AMBER / evidence grade D. Root cause: resolve_jurisdiction_iso2() only runs
when NO value is supplied, so a supplied alias was used raw.

This drives the REAL _run_identity path (the jurisdiction is set on report.identity
in the synchronous prefix, before any network await) and asserts the normalization.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from aria_service.intel import dd_orchestrator as ddo
from aria_service.intel.dd_schema import ARKDDReport


def _run_identity_with(juris_iso2: str) -> str | None:
    """Drive _run_identity with the given jurisdiction_iso2 and return the value it
    lands on report.identity — network calls mocked/timeout-bounded (the value is
    set before the first await, so this is deterministic)."""
    report = ARKDDReport()
    target = {
        "name": "Silverbrook Capital Management",
        "type": "company",
        "jurisdiction_iso2": juris_iso2,
        "registration_number": "04300718",
    }

    async def _drive():
        with patch("aria_service.intel.gleif.search_lei", AsyncMock(return_value=[])), \
             patch("aria_service.intel.registry_adapters.lookup_entity",
                   AsyncMock(return_value=None)):
            try:
                # signature is _run_identity(target, report) — no llm
                await asyncio.wait_for(
                    ddo._run_identity(target, report), timeout=2.0)
            except (asyncio.TimeoutError, Exception):
                pass  # value is set in the sync prefix, before any network await
        return report.identity.jurisdiction_iso2

    return asyncio.run(_drive())


def test_uk_alias_normalizes_to_gb():
    assert _run_identity_with("UK") == "GB", (
        "a UK company submitted as 'UK' must be normalized to 'GB' or the Companies "
        "House / officer / UBO paths never fire (R-F2976 regression)")


def test_lowercase_uk_normalizes_to_gb():
    assert _run_identity_with("uk") == "GB"


def test_valid_iso2_is_unchanged():
    # A correct ISO2 must pass through untouched (no false remap).
    assert _run_identity_with("GB") == "GB"
    assert _run_identity_with("AO") == "AO"


def test_usa_alias_normalizes():
    assert _run_identity_with("USA") == "US"


def test_unresolvable_value_is_kept_raw():
    # to_iso2 returns None for junk → keep the caller's raw value (never blank it).
    assert _run_identity_with("Xyz") == "Xyz"


def test_to_iso2_contract():
    # The helper the fix relies on — pinned so a taxonomy edit can't silently break it.
    from aria_service.intel.country_taxonomy import to_iso2
    assert to_iso2("UK") == "GB"
    assert to_iso2("GB") == "GB"
    assert to_iso2("USA") == "US"
    assert to_iso2("Xyz") is None


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
