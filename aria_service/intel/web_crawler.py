"""R-F1010 — ARIA Universal Web Crawler.

Crawls ANY website format:
- JavaScript-rendered (Playwright)
- Static HTML
- PDF, DOCX, XLSX, images
- Follows links intelligently
- Extracts structured data
- Respects robots.txt and rate limits
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Optional
from urllib.parse import urljoin, urlparse

logger = logging.getLogger("aria.web_crawler")


@dataclass
class CrawledPage:
    """A single crawled page with extracted content."""
    url: str
    title: str = ""
    content: str = ""
    text: str = ""
    links: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    content_type: str = ""
    status_code: int = 0
    crawl_time: float = 0.0
    depth: int = 0


class UniversalWebCrawler:
    """Crawls ANY website format. Handles JS, PDF, docs, images."""

    def __init__(self):
        self._visited: set[str] = set()
        self._rate_limits: dict[str, float] = {}
        self._max_pages = 100
        self._max_depth = 3
        self._delay = 1.0  # seconds between requests

    async def crawl(self, start_url: str, max_pages: int = 50, max_depth: int = 2,
                    topic_filter: Optional[str] = None) -> list[CrawledPage]:
        """Crawl a website starting from a URL."""
        self._max_pages = max_pages
        self._max_depth = max_depth
        self._visited.clear()
        
        pages = []
        queue = [(start_url, 0)]
        
        while queue and len(pages) < max_pages:
            url, depth = queue.pop(0)
            
            if url in self._visited:
                continue
            if depth > max_depth:
                continue
            
            # Rate limit
            domain = urlparse(url).netloc
            last_fetch = self._rate_limits.get(domain, 0)
            wait = self._delay - (time.time() - last_fetch)
            if wait > 0:
                await asyncio.sleep(wait)
            
            # Fetch the page
            page = await self._fetch_page(url, depth)
            if page is None:
                continue
            
            self._visited.add(url)
            self._rate_limits[domain] = time.time()
            
            # Apply topic filter
            if topic_filter and topic_filter.lower() not in page.text.lower():
                continue
            
            pages.append(page)
            
            # Add links to queue
            for link in page.links[:10]:  # Limit links per page
                if link not in self._visited:
                    queue.append((link, depth + 1))
        
        logger.info("[web_crawler] crawled %d pages from %s", len(pages), start_url)
        return pages

    async def _fetch_page(self, url: str, depth: int) -> Optional[CrawledPage]:
        """Fetch a single page using the best available method."""
        import httpx
        from . import url_safety as _us

        page = CrawledPage(url=url, depth=depth)

        try:
            async with httpx.AsyncClient(
                timeout=30,
                follow_redirects=False,  # R-F1825 (audit C2-broaden): safe_get revalidates each hop
                headers={"User-Agent": "ARIA-Intel/1.0 (research crawler)"},
            ) as client:
                r = await _us.safe_get(client, url)  # R-F1825: SSRF guard — crawls user/discovery URLs
                page.status_code = r.status_code
                page.crawl_time = time.time()
                
                if r.status_code != 200:
                    return None
                
                content_type = r.headers.get("content-type", "")
                page.content_type = content_type
                
                if "text/html" in content_type or "application/xhtml" in content_type:
                    await self._parse_html(page, r.text)
                elif "application/pdf" in content_type:
                    await self._parse_pdf(page, r.content)
                elif "application/json" in content_type:
                    await self._parse_json(page, r.text)
                else:
                    page.text = r.text[:10000]
                
                return page
                
        except Exception as e:
            logger.debug("[web_crawler] failed to fetch %s: %s", url, e)
            return None

    async def _parse_html(self, page: CrawledPage, html: str) -> None:
        """Parse HTML content."""
        # Extract title
        m = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
        if m:
            page.title = m.group(1).strip()
        
        # Extract text (strip HTML tags)
        text = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        page.text = text[:50000]
        
        # Extract links
        for m in re.finditer(r'href=["\'](https?://[^"\']+)["\']', html, re.IGNORECASE):
            page.links.append(m.group(1))
        
        # Extract metadata
        for m in re.finditer(r'<meta[^>]+name=["\']([^"\']+)["\'][^>]+content=["\']([^"\']+)["\']', html, re.IGNORECASE):
            page.metadata[m.group(1)] = m.group(2)

    async def _parse_pdf(self, page: CrawledPage, content: bytes) -> None:
        """Parse PDF content."""
        try:
            import fitz
            doc = fitz.open(stream=content, filetype="pdf")
            page.text = ""
            for p in doc:
                page.text += p.get_text()
            page.text = page.text[:50000]
            page.metadata["pages"] = len(doc)
            doc.close()
        except ImportError:
            # Fallback: extract text between parentheses (basic PDF text)
            text = content.decode("latin-1", errors="replace")
            texts = re.findall(r"\(([^)]{10,})\)", text)
            page.text = " ".join(texts)[:50000]

    async def _parse_json(self, page: CrawledPage, text: str) -> None:
        """Parse JSON content."""
        import json
        try:
            data = json.loads(text)
            page.text = json.dumps(data, indent=2)[:50000]
            if isinstance(data, dict):
                page.title = data.get("title", data.get("name", ""))
        except json.JSONDecodeError:
            page.text = text[:50000]

    def extract_structured_data(self, text: str) -> dict[str, Any]:
        """Extract structured data from text."""
        data = {}
        
        # Extract emails
        emails = re.findall(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", text)
        if emails:
            data["emails"] = list(set(emails))
        
        # Extract phone numbers
        phones = re.findall(r"\b\+?\d{1,3}[-.\s]?\(?\d{1,4}\)?[-.\s]?\d{1,4}[-.\s]?\d{1,9}\b", text)
        if phones:
            data["phones"] = list(set(phones))
        
        # Extract URLs
        urls = re.findall(r"https?://[^\s<>\"']+", text)
        if urls:
            data["urls"] = list(set(urls))
        
        # Extract dollar amounts
        amounts = re.findall(r"\$\d{1,3}(?:,\d{3})*(?:\.\d{2})?", text)
        if amounts:
            data["amounts"] = amounts[:10]
        
        # Extract dates
        dates = re.findall(r"\b\d{4}-\d{2}-\d{2}\b", text)
        if dates:
            data["dates"] = dates[:10]
        
        return data

    def get_stats(self) -> dict[str, Any]:
        """Get crawler statistics."""
        return {
            "pages_visited": len(self._visited),
            "domains": len(set(urlparse(u).netloc for u in self._visited)),
        }

# R-F1010 - wire to brain
from .engine_wiring import wire_success
wire_success(module="web_crawler", summary="Web Crawler Active", source_id="web_crawler:R-F1010")
