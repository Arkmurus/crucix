"""R-F1010 — Tests for Web Crawler, Knowledge Prioritizer, Zero-Cost Learner."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch, MagicMock


class TestUniversalWebCrawler:
    """Test the universal web crawler."""

    @pytest.mark.asyncio
    async def test_parse_html(self):
        """HTML parsing should extract title and text."""
        from aria_service.intel.web_crawler import UniversalWebCrawler, CrawledPage
        crawler = UniversalWebCrawler()
        page = CrawledPage(url="https://example.com")
        html = "<html><head><title>Test Page</title></head><body><p>Hello world</p></body></html>"
        await crawler._parse_html(page, html)
        assert page.title == "Test Page"
        assert "Hello world" in page.text

    @pytest.mark.asyncio
    async def test_parse_html_links(self):
        """HTML parsing should extract links."""
        from aria_service.intel.web_crawler import UniversalWebCrawler, CrawledPage
        crawler = UniversalWebCrawler()
        page = CrawledPage(url="https://example.com")
        html = '<a href="https://example.com/page1">Link 1</a><a href="https://example.com/page2">Link 2</a>'
        await crawler._parse_html(page, html)
        assert len(page.links) == 2
        assert "https://example.com/page1" in page.links

    @pytest.mark.asyncio
    async def test_fetch_page_success(self, monkeypatch):
        """fetch_page should return a CrawledPage on success.

        R-F3440 — httpx was already mocked correctly, but `_fetch_page` routes through
        `url_safety.safe_get` (web_crawler.py:109), whose SSRF guard RESOLVES the hostname
        to classify it. That is real DNS on a unit-test path. Stub the resolution seam so
        the classification is deterministic and offline.
        """
        from aria_service.intel import url_safety as _us
        from aria_service.intel.web_crawler import UniversalWebCrawler
        monkeypatch.setattr(_us, "_ips_for_host", lambda h: ["93.184.216.34"])
        crawler = UniversalWebCrawler()
        with patch("httpx.AsyncClient") as mock_client:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.headers = {"content-type": "text/html"}
            mock_response.text = "<html><title>Test</title><body>Content</body></html>"
            mock_client.return_value.__aenter__.return_value.get = AsyncMock(return_value=mock_response)
            page = await crawler._fetch_page("https://example.com", 0)
        assert page is not None
        assert page.status_code == 200
        assert "Content" in page.text

    @pytest.mark.asyncio
    async def test_fetch_page_failure(self, monkeypatch):
        """fetch_page should return None on failure.

        R-F3440 — this passed VACUOUSLY whenever DNS was unavailable: the SSRF guard fails
        closed and `_fetch_page` returns None, which is exactly what this asserts. So it
        was green whether or not the mocked failure path worked at all. Pinning the
        resolver makes the None it observes come from the CONNECTION failure it is testing.
        """
        from aria_service.intel import url_safety as _us
        from aria_service.intel.web_crawler import UniversalWebCrawler
        monkeypatch.setattr(_us, "_ips_for_host", lambda h: ["93.184.216.34"])
        crawler = UniversalWebCrawler()

        # Control: with the SAME resolver stub and no injected failure, the page IS
        # returned. Without this, "returns None" proves nothing about the failure path.
        with patch("httpx.AsyncClient") as ok_client:
            ok_resp = MagicMock()
            ok_resp.status_code = 200
            ok_resp.headers = {"content-type": "text/html"}
            ok_resp.text = "<html><title>T</title><body>C</body></html>"
            ok_client.return_value.__aenter__.return_value.get = AsyncMock(return_value=ok_resp)
            control = await crawler._fetch_page("https://example.com", 0)
        assert control is not None, (
            "the control must succeed, or this test cannot distinguish a connection "
            "failure from the SSRF guard blocking the URL")

        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.get = AsyncMock(side_effect=Exception("Connection failed"))
            page = await crawler._fetch_page("https://example.com", 0)
        assert page is None

    def test_extract_structured_data(self):
        """extract_structured_data should find emails, phones, URLs."""
        from aria_service.intel.web_crawler import UniversalWebCrawler
        crawler = UniversalWebCrawler()
        text = "Contact us at info@example.com or call +44 20 7123 4567. Visit https://example.com"
        data = crawler.extract_structured_data(text)
        assert "info@example.com" in data.get("emails", [])
        assert "https://example.com" in data.get("urls", [])

    def test_get_stats(self):
        """get_stats should return crawler statistics."""
        from aria_service.intel.web_crawler import UniversalWebCrawler
        crawler = UniversalWebCrawler()
        stats = crawler.get_stats()
        assert "pages_visited" in stats
        assert "domains" in stats


class TestKnowledgePrioritizer:
    """Test the knowledge prioritizer."""

    def test_record_query(self):
        """record_query should track usage."""
        from aria_service.intel.knowledge_learner import KnowledgePrioritizer
        kp = KnowledgePrioritizer()
        kp.record_query("sanctions")
        kp.record_query("sanctions")
        kp.record_query("export_control")
        assert kp._usage_counts["sanctions"] == 2
        assert kp._usage_counts["export_control"] == 1

    def test_identify_gaps(self):
        """identify_gaps should find low-coverage topics."""
        from aria_service.intel.knowledge_learner import KnowledgePrioritizer
        kp = KnowledgePrioritizer()
        heatmap = {"sanctions": 0.9, "africa_defence": 0.2, "cyber_threats": 0.1}
        gaps = kp.identify_gaps(heatmap)
        assert len(gaps) == 2
        assert all(g["priority"] == "high" for g in gaps)

    def test_get_learning_plan(self):
        """get_learning_plan should return prioritized items."""
        from aria_service.intel.knowledge_learner import KnowledgePrioritizer
        kp = KnowledgePrioritizer()
        kp.record_query("frequently_used")
        kp.identify_gaps({"low_coverage": 0.1})
        plan = kp.get_learning_plan(max_items=5)
        assert len(plan) > 0
        assert all("topic" in p for p in plan)
        assert all("reason" in p for p in plan)


class TestZeroCostLearner:
    """Test the zero-cost learner."""

    @pytest.mark.asyncio
    async def test_run_learning_cycle(self):
        """run_learning_cycle should return results."""
        from aria_service.intel.knowledge_learner import ZeroCostLearner
        learner = ZeroCostLearner()
        with patch("aria_service.intel.correction_learner.recent_corrections_addendum", AsyncMock(return_value="corrections")):
            result = await learner.run_learning_cycle()
        assert "cycle" in result
        assert "actions" in result
        assert "knowledge_added" in result
        assert result["cycle"] == 1

    def test_get_stats(self):
        """get_stats should return learning statistics."""
        from aria_service.intel.knowledge_learner import ZeroCostLearner
        learner = ZeroCostLearner()
        stats = learner.get_stats()
        assert "cycles_completed" in stats
        assert "total_learnings" in stats
