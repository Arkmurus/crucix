"""R-F4106 — documented registry transport exceptions stay host-confined."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from aria_service.intel import registry_adapters as ra


class _Response:
    status_code = 404
    text = ""

    @staticmethod
    def json() -> dict:
        return {}


class _Client:
    def __init__(self) -> None:
        self.get = AsyncMock(return_value=_Response())

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False


@pytest.mark.asyncio
async def test_poland_registration_input_cannot_change_registry_origin() -> None:
    client = _Client()
    with patch.object(ra.httpx, "AsyncClient", return_value=client):
        await ra._lookup_poland("ignored", "https://127.0.0.1/x/1234567890")

    url = client.get.await_args.args[0]
    assert url == f"{ra._PL_API_BASE}/OdpisPelny/1234567890?rejestr=P&format=json"


@pytest.mark.asyncio
async def test_hungary_name_input_cannot_change_registry_origin() -> None:
    client = _Client()
    with patch.object(ra.httpx, "AsyncClient", return_value=client):
        await ra._lookup_hungary("//127.0.0.1/admin", None)

    url = client.get.await_args.args[0]
    assert url.startswith("https://www.e-cegjegyzek.hu/?")
    assert "127.0.0.1" in url


@pytest.mark.asyncio
async def test_finland_name_input_cannot_change_registry_origin() -> None:
    client = _Client()
    with patch.object(ra.httpx, "AsyncClient", return_value=client):
        await ra._lookup_finland("//127.0.0.1/admin", None)

    url = client.get.await_args.args[0]
    assert url.startswith(f"{ra._FI_PRH_BASE}?")
    assert "127.0.0.1" in url


@pytest.mark.asyncio
async def test_brazil_registration_input_cannot_change_registry_origin() -> None:
    client = _Client()
    with patch.object(ra.httpx, "AsyncClient", return_value=client):
        await ra._lookup_brazil("ignored", "https://127.0.0.1/12.345.678/0001-95")

    url = client.get.await_args.args[0]
    assert url == f"{ra._BR_RECEITAWS_BASE}/12345678000195"


@pytest.mark.asyncio
async def test_germany_name_input_cannot_change_registry_origin() -> None:
    client = _Client()
    with patch.object(ra.httpx, "AsyncClient", return_value=client):
        await ra._lookup_germany("//127.0.0.1/admin", None)

    url = client.get.await_args.args[0]
    assert url == f"{ra._DE_API_BASE}/companies/by_name"
