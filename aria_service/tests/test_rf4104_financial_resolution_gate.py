"""R-F4104 — ambiguous names cannot acquire another company's financials."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from aria_service.intel import companies_house as ch
from aria_service.intel import financial_health as fh


AMBIGUOUS = [
    {"company_number": "11111111", "title": "ACME LTD", "company_status": "active"},
    {"company_number": "22222222", "title": "ACME LTD", "company_status": "active"},
]


def test_shared_resolver_refuses_genuinely_ambiguous_live_names() -> None:
    selected, decision = ch.resolve_company_search("Acme Ltd", AMBIGUOUS)
    assert selected is None
    assert decision["ambiguous"] is True
    assert decision["candidate_count"] == 2


@pytest.mark.asyncio
async def test_registry_accounts_halts_before_profile_for_ambiguous_name(monkeypatch) -> None:
    """Capability: phase-one financial evidence must not inherit a guessed company."""
    search = AsyncMock(return_value=AMBIGUOUS)
    profile = AsyncMock()
    monkeypatch.setattr(ch, "is_enabled", lambda: True)
    monkeypatch.setattr(ch, "search_companies", search)
    monkeypatch.setattr(ch, "get_company_profile", profile)

    result = await fh._uk_registry_accounts("Acme Ltd")

    assert result is None
    search.assert_awaited_once_with("Acme Ltd", limit=3)
    profile.assert_not_awaited()


@pytest.mark.asyncio
async def test_registry_figures_halts_before_document_fetch_for_ambiguous_name(monkeypatch) -> None:
    """Capability: phase-two solvency must not use an inferred registration number."""
    search = AsyncMock(return_value=AMBIGUOUS)
    figures = AsyncMock()
    monkeypatch.setattr(ch, "is_enabled", lambda: True)
    monkeypatch.setattr(ch, "search_companies", search)
    monkeypatch.setattr(ch, "fetch_accounts_figures", figures)
    result: dict = {"has_financials": False}

    changed = await fh._enrich_with_registry_figures(result, "Acme Ltd", "GB")

    assert changed is False
    assert result == {"has_financials": False}
    search.assert_awaited_once_with("Acme Ltd", limit=3)
    figures.assert_not_awaited()


@pytest.mark.asyncio
async def test_explicit_registration_number_still_fetches_financial_evidence(monkeypatch) -> None:
    """An operator-supplied company number remains an identity anchor."""
    search = AsyncMock()
    profile = AsyncMock(return_value=None)
    monkeypatch.setattr(ch, "is_enabled", lambda: True)
    monkeypatch.setattr(ch, "search_companies", search)
    monkeypatch.setattr(ch, "get_company_profile", profile)

    result = await fh._uk_registry_accounts("Acme Ltd", "01234567")

    assert result is None
    search.assert_not_awaited()
    profile.assert_awaited_once_with("01234567")
