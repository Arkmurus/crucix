"""R-F579 (2026-05-16) — court_records adapter tests.

Capability tests focus on the contract (input → output shape) and the
graceful-degradation paths, since hitting real CourtListener/Bailii
on every CI run would be flaky and slow.
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def test_norm_entity_strips_corporate_suffixes():
    from aria_service.intel.sources.court_records import _norm_entity
    assert _norm_entity("ACME Widgets Limited") == "ACME Widgets"
    assert _norm_entity("Aselsan A.Ş.") == "Aselsan"
    assert _norm_entity("Embraer S.A.") == "Embraer"
    assert _norm_entity("Lockheed Martin Corp.") == "Lockheed Martin"


def test_norm_entity_handles_empty_or_short():
    from aria_service.intel.sources.court_records import _norm_entity
    assert _norm_entity("") == ""
    assert _norm_entity("   ") == ""
    # 2-char inputs pass through; search functions skip them via length check
    assert _norm_entity("X") == "X"


def test_parse_bailii_rss_extracts_clean_title():
    """Capability: Google-News RSS appends '— bailii.org' to titles;
    the parser must strip that trailing publisher tag."""
    from aria_service.intel.sources.court_records import _parse_bailii_rss
    xml = """
      <rss><channel>
        <item>
          <title>Smith v Jones [2024] EWHC 1234 (QB) - bailii.org</title>
          <link>https://www.bailii.org/ew/cases/EWHC/QB/2024/1234.html</link>
          <pubDate>Wed, 15 Mar 2024 10:30:00 GMT</pubDate>
        </item>
      </channel></rss>
    """
    hits = _parse_bailii_rss(xml, limit=5)
    assert len(hits) == 1
    h = hits[0]
    assert h["title"] == "Smith v Jones [2024] EWHC 1234 (QB)"
    assert h["jurisdiction"] == "UK"
    assert h["source"] == "bailii"
    assert h["tier"] == "1a"
    assert "bailii.org" in h["citation_url"]


def test_parse_bailii_rss_handles_empty_feed():
    from aria_service.intel.sources.court_records import _parse_bailii_rss
    assert _parse_bailii_rss("", limit=5) == []
    assert _parse_bailii_rss("<rss><channel></channel></rss>", limit=5) == []


def test_search_us_courts_skips_short_inputs():
    from aria_service.intel.sources.court_records import search_us_courts
    out = asyncio.run(search_us_courts(""))
    assert out == []
    out = asyncio.run(search_us_courts("AB"))
    assert out == []


def test_search_us_courts_parses_courtlistener_response():
    """Capability: parser must handle the actual CourtListener JSON
    shape. Mock the HTTP layer; verify hit shape."""
    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.json = MagicMock(return_value={
        "results": [
            {
                "caseName": "United States v. Acme Widgets",
                "court": "S.D.N.Y.",
                "dateFiled": "2023-08-15T00:00:00Z",
                "snippet": "Defendant Acme Widgets violated EAR §§ 736 ...",
                "absolute_url": "/opinion/12345/united-states-v-acme/",
            }
        ]
    })

    async def _run():
        client = AsyncMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=None)
        client.get = AsyncMock(return_value=fake_response)
        with patch("aria_service.intel.sources.court_records.httpx.AsyncClient", return_value=client):
            from aria_service.intel.sources.court_records import search_us_courts
            return await search_us_courts("Acme Widgets Limited")

    hits = asyncio.run(_run())
    assert len(hits) == 1
    h = hits[0]
    assert h["title"] == "United States v. Acme Widgets"
    assert h["court"] == "S.D.N.Y."
    assert h["date"] == "2023-08-15"
    assert h["jurisdiction"] == "US"
    assert h["source"] == "courtlistener"
    assert h["tier"] == "1a"
    assert h["citation_url"].startswith("https://www.courtlistener.com")


def test_search_us_courts_handles_non_200():
    """Capability: HTTP errors must degrade to empty list, never raise."""
    fake_response = MagicMock()
    fake_response.status_code = 503

    async def _run():
        client = AsyncMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=None)
        client.get = AsyncMock(return_value=fake_response)
        with patch("aria_service.intel.sources.court_records.httpx.AsyncClient", return_value=client):
            from aria_service.intel.sources.court_records import search_us_courts
            return await search_us_courts("Acme Widgets")
    assert asyncio.run(_run()) == []


def test_search_uk_courts_handles_network_failure():
    """Capability: any exception in fetch returns [], never raises up."""
    async def _run():
        with patch(
            "aria_service.intel.sources.court_records.httpx.AsyncClient",
            side_effect=ConnectionError("simulated"),
        ):
            from aria_service.intel.sources.court_records import search_uk_courts
            return await search_uk_courts("Acme")
    assert asyncio.run(_run()) == []


def test_search_all_runs_both_sources_concurrently_and_dedupes():
    """Capability: search_all gathers both sources + dedupes by
    (jurisdiction, title prefix). Different jurisdictions of the same
    case caption are kept (US v UK are separate records)."""
    async def _run():
        with patch(
            "aria_service.intel.sources.court_records.search_us_courts",
            new=AsyncMock(return_value=[
                {"title": "Smith v Jones", "court": "S.D.N.Y.", "date": "2024",
                 "citation_url": "x", "snippet": "", "jurisdiction": "US",
                 "source": "courtlistener", "tier": "1a"},
                # Duplicate-within-source: same title twice
                {"title": "Smith v Jones", "court": "9th Cir.", "date": "2025",
                 "citation_url": "y", "snippet": "", "jurisdiction": "US",
                 "source": "courtlistener", "tier": "1a"},
            ]),
        ), patch(
            "aria_service.intel.sources.court_records.search_uk_courts",
            new=AsyncMock(return_value=[
                # Same caption, different jurisdiction → kept
                {"title": "Smith v Jones", "court": "EWHC", "date": "2024",
                 "citation_url": "z", "snippet": "", "jurisdiction": "UK",
                 "source": "bailii", "tier": "1a"},
            ]),
        ):
            from aria_service.intel.sources.court_records import search_all
            return await search_all("Smith")

    result = asyncio.run(_run())
    assert result["us_count"] == 2
    assert result["uk_count"] == 1
    # Dedupe: 2 US hits (same title) → 1 in merged; UK kept separately
    assert len(result["hits"]) == 2
    assert {h["jurisdiction"] for h in result["hits"]} == {"US", "UK"}


def test_search_all_handles_empty_entity():
    from aria_service.intel.sources.court_records import search_all
    out = asyncio.run(search_all(""))
    assert out["hits"] == []
    assert out["us_count"] == 0
    assert out["uk_count"] == 0


def test_search_all_one_source_failing_doesnt_kill_other():
    """Capability: if CourtListener throws, Bailii hits still return."""
    async def _run():
        with patch(
            "aria_service.intel.sources.court_records.search_us_courts",
            new=AsyncMock(side_effect=RuntimeError("network blip")),
        ), patch(
            "aria_service.intel.sources.court_records.search_uk_courts",
            new=AsyncMock(return_value=[
                {"title": "X v Y", "court": "EWHC", "date": "2024",
                 "citation_url": "z", "snippet": "", "jurisdiction": "UK",
                 "source": "bailii", "tier": "1a"},
            ]),
        ):
            from aria_service.intel.sources.court_records import search_all
            return await search_all("X")

    result = asyncio.run(_run())
    assert result["uk_count"] == 1
    assert len(result["hits"]) == 1
    assert result["hits"][0]["jurisdiction"] == "UK"
