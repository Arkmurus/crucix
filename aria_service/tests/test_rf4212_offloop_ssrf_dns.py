"""R-F4212: DNS-backed SSRF validation must never block ARIA's serving loop."""

import asyncio
import time

import pytest

from aria_service.intel import researcher
from aria_service.intel import security
from aria_service.intel import url_safety


@pytest.fixture(autouse=True)
def _clear_dns_caches():
    security._dns_cache_clear()
    url_safety._DNS_CACHE.clear()
    yield
    security._dns_cache_clear()
    url_safety._DNS_CACHE.clear()


@pytest.mark.asyncio
async def test_async_ssrf_facades_preserve_private_address_blocks(monkeypatch):
    """Both public async guards retain the user-visible SSRF refusal policy."""
    monkeypatch.setattr(
        security.socket,
        "getaddrinfo",
        lambda *args, **kwargs: [(2, 1, 6, "", ("93.184.216.34", 0))],
    )
    assert await security.sanitise_url_async("https://safe.example.com/a") == (
        "https://safe.example.com/a"
    )
    assert await security.sanitise_url_async("http://127.0.0.1/private") is None
    ok, reason = await url_safety.is_safe_url_async(
        "http://169.254.169.254/latest/meta"
    )
    assert not ok
    assert "link_local" in reason


@pytest.mark.asyncio
async def test_real_extract_url_text_stays_responsive_during_slow_dns(monkeypatch):
    """Drive the exact live stall path and measure its event-loop responsiveness."""
    def _slow_public_resolution(host, port, *args, **kwargs):
        time.sleep(0.1)
        return [(2, 1, 6, "", ("93.184.216.34", 0))]

    class _Response:
        status_code = 200
        text = (
            "<html><head><title>Verified source</title></head><body>"
            + "Grounded public-source evidence. " * 20
            + "</body></html>"
        )

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

    async def _safe_get(client, url, **kwargs):
        return _Response()

    monkeypatch.setattr(security.socket, "getaddrinfo", _slow_public_resolution)
    monkeypatch.setattr(url_safety.socket, "getaddrinfo", _slow_public_resolution)
    monkeypatch.setattr(researcher.httpx, "AsyncClient", _Client)
    monkeypatch.setattr(url_safety, "safe_get", _safe_get)

    task = asyncio.create_task(
        researcher.extract_url_text("https://slow-extractor.example.com/report")
    )
    loop = asyncio.get_running_loop()
    last = loop.time()
    max_gap = 0.0
    while not task.done():
        await asyncio.sleep(0)
        now = loop.time()
        max_gap = max(max_gap, now - last)
        last = now

    result = await task
    assert result["extraction_ok"] is True
    assert max_gap < 0.05, f"DNS blocked the serving loop for {max_gap:.3f}s"
