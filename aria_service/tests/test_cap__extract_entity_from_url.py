"""
Capability test for R-F1177: URL-to-entity extraction in the DD orchestrator.
Tests that _extract_entity_from_url can fetch a website and extract the company name.
"""
import pytest
from aria_service.intel.dd_orchestrator import _extract_entity_from_url, _enrich_target_from_url


@pytest.mark.asyncio
async def test_extract_entity_from_url_known_site(monkeypatch):
    """Extract the entity name from a page's <title> tag.

    R-F3440 — this used to fetch https://myskyegroove.com LIVE. Two problems: it made the
    result depend on a third party's uptime and on their choice of title text, and it put
    real DNS + HTTP on the unit-test path, which is the failure class that hangs this suite
    rather than failing it (R-F2812/R-F3298/R-F3318/R-F3319, and the R-F3439 root cause).

    What this test is actually FOR is ARIA's title-to-name extraction, so the fetch is
    stubbed and the extraction is exercised for real, on the exact page shape the live site
    used to return.
    """
    import httpx

    html = ("<html><head><title>Skyegroove || Nigeria's Leading Aviation Service Provider"
            "</title></head><body><p>Plot 242 Muhammadu Buhari Way, Abuja. We provide "
            "aviation ground handling and charter services across West Africa.</p>"
            "</body></html>")

    class _Resp:
        status_code = 200
        headers = {"content-type": "text/html; charset=utf-8"}
        text = html
        content = html.encode()

        def raise_for_status(self):
            return None

    class _Client:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, *a, **k): return _Resp()

    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    # dd_orchestrator._ssrf_safe_url calls socket.getaddrinfo DIRECTLY (dd_orchestrator.py
    # :458) to classify the host as public or private. That is correct production
    # behaviour and it is also live DNS. Stubbing the RESOLVER rather than the guard keeps
    # the real classification logic in the path — the guard itself has dedicated coverage
    # in test_rf1811_dd_url_ssrf_guard.py, and weakening it here would hide a regression.
    import socket
    monkeypatch.setattr(
        socket, "getaddrinfo",
        lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))])

    result = await _extract_entity_from_url("https://myskyegroove.com")
    assert result is not None
    assert result["name"] is not None, "Should extract a company name from the title tag"
    assert "skyegroove" in result["name"].lower(), \
        f"Expected 'Skyegroove' in extracted name, got: {result['name']}"
    assert result["domain"] == "myskyegroove.com"
    assert result["title"] is not None, "Should extract the raw title tag"
    assert len(result["snippet"]) > 0, "Should extract visible text snippet"


@pytest.mark.asyncio
async def test_extract_entity_from_url_timeout_safe():
    """Verify the function handles slow/unreachable URLs gracefully (timeout safety)."""
    result = await _extract_entity_from_url("https://this-domain-does-not-exist-12345.com")
    assert result is not None
    # Should fall back to domain-derived name
    assert result["name"] is not None, "Should fall back to domain-derived name on timeout"
    assert result["domain"] == "this-domain-does-not-exist-12345.com"


@pytest.mark.asyncio
async def test_enrich_target_from_url_populates_name():
    """Verify _enrich_target_from_url populates the name field from a URL."""
    target = {"website": "https://myskyegroove.com"}
    enriched = await _enrich_target_from_url(target)
    assert enriched.get("name") is not None, "Should populate name from URL"
    assert "skyegroove" in enriched["name"].lower(), \
        f"Expected 'Skyegroove' in name, got: {enriched['name']}"
    assert enriched.get("domain") == "myskyegroove.com"


@pytest.mark.asyncio
async def test_enrich_target_from_url_skips_if_name_exists():
    """Verify enrichment is skipped when the target already has a name."""
    target = {"name": "ACME Corp", "website": "https://myskyegroove.com"}
    enriched = await _enrich_target_from_url(target)
    assert enriched["name"] == "ACME Corp", "Should NOT overwrite existing name"


@pytest.mark.asyncio
async def test_enrich_target_from_url_no_url():
    """Verify enrichment handles missing URL gracefully."""
    target = {}
    enriched = await _enrich_target_from_url(target)
    assert enriched == target, "Should return unchanged target when no URL"


@pytest.mark.asyncio
async def test_extract_entity_from_url_handles_missing_scheme():
    """Verify the function handles URLs without scheme."""
    result = await _extract_entity_from_url("myskyegroove.com")
    assert result is not None
    assert result["name"] is not None
    assert "skyegroove" in result["name"].lower()
