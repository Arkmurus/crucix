"""R-F4099 — unresolved company names must not drive downstream due diligence."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

from aria_service.intel import companies_house as ch


def test_dead_exact_match_is_an_unsafe_resolution() -> None:
    decision: dict = {}
    ch._pick_best_company(
        "ACME LIMITED",
        [{
            "company_number": "01234567",
            "title": "ACME LIMITED",
            "company_status": "dissolved",
        }],
        decision,
    )

    assert decision["ambiguous"] is True
    assert "confirm a live registration number" in " ".join(decision["reasons"])


def test_ambiguous_name_halts_the_real_investigation_path() -> None:
    """Capability: no profile/officer/PSC/filing hop may inherit an ambiguous name."""
    async def run() -> dict:
        results = [
            {"company_number": "11111111", "title": "ACME LTD", "company_status": "active"},
            {"company_number": "22222222", "title": "ACME LTD", "company_status": "active"},
        ]
        profile = AsyncMock()
        officers = AsyncMock()
        psc = AsyncMock()
        filings = AsyncMock()
        with (
            patch.object(ch, "is_enabled", return_value=True),
            patch.object(ch, "search_companies", new=AsyncMock(return_value=results)),
            patch.object(ch, "get_company_profile", new=profile),
            patch.object(ch, "get_officers", new=officers),
            patch.object(ch, "get_psc", new=psc),
            patch.object(ch, "get_filing_history", new=filings),
        ):
            result = await ch.investigate_uk_entity(company_name="Acme Ltd")
        profile.assert_not_awaited()
        officers.assert_not_awaited()
        psc.assert_not_awaited()
        filings.assert_not_awaited()
        return result

    result = asyncio.run(run())
    assert result["found"] is False
    assert result["resolution_required"] is True
    assert result["resolution"]["ambiguous"] is True
    prompt = ch.format_for_prompt(result)
    assert "IDENTITY RESOLUTION REQUIRED" in prompt
    assert "confirm the Companies House registration number" in prompt
    assert "Do not continue due diligence" in prompt


def test_exact_active_name_still_runs_the_real_investigation_path() -> None:
    """The halt must not block a unique active legal-name match."""
    async def run() -> tuple[dict, AsyncMock]:
        results = [{
            "company_number": "01234567",
            "title": "ACME LIMITED",
            "company_status": "active",
        }]
        profile = AsyncMock(return_value=None)
        with (
            patch.object(ch, "is_enabled", return_value=True),
            patch.object(ch, "search_companies", new=AsyncMock(return_value=results)),
            patch.object(ch, "get_company_profile", new=profile),
        ):
            result = await ch.investigate_uk_entity(company_name="Acme Limited")
        return result, profile

    result, profile = asyncio.run(run())
    profile.assert_awaited_once_with("01234567")
    assert "resolution_required" not in result


def test_explicit_company_number_bypasses_name_resolution() -> None:
    """A registration number is already an identity anchor and remains usable."""
    async def run() -> AsyncMock:
        profile = AsyncMock(return_value=None)
        search = AsyncMock()
        with (
            patch.object(ch, "is_enabled", return_value=True),
            patch.object(ch, "search_companies", new=search),
            patch.object(ch, "get_company_profile", new=profile),
        ):
            await ch.investigate_uk_entity(company_number="01234567")
        search.assert_not_awaited()
        return profile

    profile = asyncio.run(run())
    profile.assert_awaited_once_with("01234567")
