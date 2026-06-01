"""R-F1257: Capability test for web_atlas brain_hook batching.

Verifies that crawl_seed_homepages now batches absorbs instead of
firing one per page, preventing brain_hook background tier saturation.
"""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from aria_service.crawler.runner import crawl_seed_homepages


class TestCrawlerBatchAbsorb:
    """Capability tests for R-F1257 brain_hook batching."""

    @pytest.mark.asyncio
    async def test_batch_absorb_fired_once(self):
        """Multiple indexed pages should fire ONE absorb, not N."""
        mock_domains = [
            {"domain": "example.com", "enabled": True},
            {"domain": "test.org", "enabled": True},
        ]

        mock_results = [
            {
                "extraction_ok": True,
                "domain": "example.com",
                "title": "Example Home",
                "url": "https://example.com/",
                "canonical_url": "https://example.com/",
                "body": "Welcome to Example",
                "status_class": "ok",
            },
            {
                "extraction_ok": True,
                "domain": "test.org",
                "title": "Test Page",
                "url": "https://test.org/",
                "canonical_url": "https://test.org/",
                "body": "This is a test page",
                "status_class": "ok",
            },
        ]

        with patch("aria_service.crawler.runner.db.list_domains", new_callable=AsyncMock) as mock_list:
            mock_list.return_value = mock_domains
            with patch("aria_service.crawler.runner.fetcher.fetch_for_crawl", new_callable=AsyncMock) as mock_fetch:
                mock_fetch.side_effect = mock_results
                # Mock the indexer at the search_index module level
                with patch("aria_service.search_index.indexer.index_fetch_result", new_callable=AsyncMock) as mock_index:
                    mock_index.return_value = "doc-123"
                    with patch("aria_service.intel.brain_hook.absorb", new_callable=AsyncMock) as mock_absorb:
                        mock_absorb.return_value = {}
                        result = await crawl_seed_homepages(limit=2)
                        # Should have indexed 2 pages
                        assert result["indexed"] == 2
                        # Should have fired exactly 1 absorb (the batch)
                        assert mock_absorb.call_count == 1
                        # The batch summary should mention both pages
                        call_kwargs = mock_absorb.call_args[1]
                        assert "2 pages" in call_kwargs.get("summary", "")
                        assert "2 domains" in call_kwargs.get("summary", "")
                        assert "example.com" in call_kwargs.get("entity_name", "")
                        assert "test.org" in call_kwargs.get("entity_name", "")
                        assert call_kwargs.get("module") == "web_atlas"

    @pytest.mark.asyncio
    async def test_no_absorb_on_no_indexed_pages(self):
        """No indexed pages should fire zero absorbs."""
        with patch("aria_service.crawler.runner.db.list_domains", new_callable=AsyncMock) as mock_list:
            mock_list.return_value = []
            with patch("aria_service.intel.brain_hook.absorb", new_callable=AsyncMock) as mock_absorb:
                mock_absorb.return_value = {}
                result = await crawl_seed_homepages(limit=0)
                assert result["indexed"] == 0
                assert mock_absorb.call_count == 0
