"""R-F4100 — all Companies House-discovered URLs cross the SSRF boundary."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

from aria_service.intel import companies_house as ch


def test_document_metadata_rejects_private_destination_before_network() -> None:
    """Capability: the real metadata fetch must not contact an upstream-supplied LAN URL."""
    client = AsyncMock()
    client.__aenter__.return_value = client
    client.__aexit__.return_value = False
    with patch.object(ch.httpx, "AsyncClient", return_value=client):
        result = asyncio.run(ch._get_json_url("http://127.0.0.1/private"))

    assert result is None
    client.get.assert_not_awaited()


def test_signed_document_redirect_rejects_private_destination() -> None:
    """A public metadata endpoint cannot redirect the content fetch into the LAN."""
    first = AsyncMock()
    first.status_code = 302
    first.headers = {"location": "http://169.254.169.254/latest/meta-data"}
    client = AsyncMock()
    client.__aenter__.return_value = client
    client.__aexit__.return_value = False
    client.get.return_value = first

    with (
        patch.object(ch.httpx, "AsyncClient", return_value=client),
        patch.object(ch.url_safety, "assert_safe_url"),
        patch.object(ch.url_safety, "safe_get", side_effect=ValueError("ssrf_blocked:link_local")) as safe_get,
    ):
        result = asyncio.run(ch._get_document_content("https://document-api.example/doc", "text/html"))

    assert result is None
    assert client.get.await_count == 1
    safe_get.assert_awaited_once_with(client, "http://169.254.169.254/latest/meta-data")
