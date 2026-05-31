"""
Capability test for R-F1177: URL-to-entity extraction in the DD orchestrator.
Tests that _extract_entity_from_url can fetch a website and extract the company name.
"""
import pytest
from aria_service.intel.dd_orchestrator import _extract_entity_from_url, _enrich_target_from_url


@pytest.mark.asyncio
async def test_extract_entity_from_url_known_site():
    """Fetch a known website and verify the entity name is extracted from the <title> tag."""
    result = await _extract_entity_from_url("https://myskyegroove.com")
    assert result is not None
    assert result["name"] is not None, "Should extract a company name from the title tag"
    assert "skyegroove" in result["name"].lower() or "Skyegroove" in result["name"], \
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
