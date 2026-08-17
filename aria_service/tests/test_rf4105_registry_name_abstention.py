"""R-F4105 — registry ranking cannot silently become legal identity."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from aria_service.intel import registry_adapters as ra


class _Response:
    def __init__(self, payload: dict, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code

    def json(self) -> dict:
        return self._payload


class _Client:
    def __init__(self, responses: list[_Response]):
        self._responses = iter(responses)
        self.get = AsyncMock(side_effect=lambda *args, **kwargs: next(self._responses))

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False


def test_selector_accepts_one_exact_but_refuses_ranked_ambiguity() -> None:
    rows = [{"name": "ACME HOLDINGS"}, {"name": "ACME SYSTEMS"}]
    assert ra._exact_or_single_registry_result(rows, "Acme Systems", "name") == rows[1]
    assert ra._exact_or_single_registry_result(rows, "Acme", "name") is None


@pytest.mark.asyncio
async def test_switzerland_abstains_when_multiple_partial_names_are_ranked(monkeypatch) -> None:
    from aria_service.intel import zefix

    monkeypatch.setattr(zefix, "search_company", AsyncMock(return_value=[
        {"name": "ACME HOLDING AG", "uid": "CHE-1"},
        {"name": "ACME SYSTEMS AG", "uid": "CHE-2"},
    ]))

    assert await ra._lookup_switzerland("Acme", None) is None


@pytest.mark.asyncio
async def test_uae_abstains_when_difc_returns_multiple_partial_names() -> None:
    client = _Client([
        _Response({"results": [
            {"name": "ACME HOLDING LTD", "license_number": "1"},
            {"name": "ACME SYSTEMS LTD", "license_number": "2"},
        ]}),
        _Response({}, 404),
        _Response({}, 404),
    ])
    with patch.object(ra.httpx, "AsyncClient", return_value=client):
        assert await ra._lookup_uae("Acme", None) is None


@pytest.mark.asyncio
async def test_germany_abstains_when_multiple_partial_names_are_ranked() -> None:
    client = _Client([_Response([
        {"name": "ACME HOLDING GMBH", "id": "1"},
        {"name": "ACME SYSTEMS GMBH", "id": "2"},
    ])])
    with patch.object(ra.httpx, "AsyncClient", return_value=client):
        assert await ra._lookup_germany("Acme", None) is None


@pytest.mark.asyncio
async def test_france_abstains_when_multiple_partial_names_are_ranked() -> None:
    client = _Client([_Response({"results": [
        {"nom_complet": "ACME HOLDING SAS", "siren": "111111111"},
        {"nom_complet": "ACME SYSTEMS SAS", "siren": "222222222"},
    ]})])
    with patch.object(ra.httpx, "AsyncClient", return_value=client):
        assert await ra._lookup_france("Acme", None) is None
