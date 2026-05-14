"""fetcher — polite, robots-aware HTTP fetch for the search index.

R-F501 (2026-05-14). Thin coordination layer around the existing
`aria_service.intel.researcher.extract_url_text` primitive (R-F126).

We DO NOT reimplement the actual fetch — extract_url_text already has:
  - httpx async with random UA (post R-F17..F20 Chrome rotation)
  - SSRF guard via url_safety.is_safe_url
  - Archive.org Wayback fallback on 401/402/403/404/410/429/451/5xx
  - Lightpanda fallback for JS-heavy SPAs
  - Structured-text return shape

This module adds:
  - politeness.acquire(domain) before any fetch
  - politeness.is_allowed(url) robots.txt enforcement
  - SQLite domain lookup → enrich result with tier / sector / language
  - mark_domain_crawled telemetry

Public:
    async def fetch_for_index(url: str, *, timeout=20.0) -> dict | None

Returns a normalised dict ready for the indexer, or None when the URL
was skipped (robots-denied / SSRF / fetch-failed). The indexer's job
is then to strip noise + upsert into the documents table.
"""
from __future__ import annotations

import logging
import time
from typing import Any

from aria_service.search_index import db
from . import politeness

logger = logging.getLogger("aria.crawler.fetcher")


_USER_AGENT = "ARIAsBot/1.0 (+https://aria-intel.fly.dev/about; respect-robots)"


async def fetch_for_index(url: str, timeout: float = 20.0) -> dict | None:
    """Fetch a single URL through the polite + extract_url_text pipeline.

    Returns:
      {url, canonical_url, domain, title, headings, body, language,
       source_tier, http_status, fetched_at, extraction_ok, duration_ms}
    or None when robots blocked / SSRF blocked / domain not registered.
    """
    domain = politeness.domain_of(url)
    if not domain:
        return None

    # 1. Domain known? We only index from the curated registry.
    d_row = await db.get_domain(domain)
    if d_row is None:
        # New domain discovered by link discovery — silently skip in
        # Phase 1. Phase 2 (link discovery) will auto-register at tier 4.
        return None
    if not d_row.get("enabled"):
        return None

    # 2. Robots.txt
    allowed = await politeness.is_allowed(url, user_agent=_USER_AGENT)
    if not allowed:
        logger.info("fetcher: robots-blocked %s", url[:120])
        return None

    # 3. Politeness gate
    await politeness.acquire(domain)

    # 4. Delegate to the existing fast extractor.
    try:
        from aria_service.intel.researcher import extract_url_text
    except Exception as e:
        logger.error("fetcher: cannot import extract_url_text: %s", e)
        return None

    t0 = time.time()
    try:
        result = await extract_url_text(url, timeout=timeout)
    except Exception as e:
        logger.debug("fetcher: extract_url_text raised on %s: %s",
                     url[:120], e)
        return None

    duration_ms = int((time.time() - t0) * 1000)

    if not result or not result.get("extraction_ok"):
        logger.debug("fetcher: extraction_ok=False for %s (%s)",
                     url[:120], (result or {}).get("error", ""))
        # We still record the attempt at the domain level so we don't
        # hammer a dead host.
        await db.mark_domain_crawled(domain)
        return None

    body = result.get("text") or ""
    title = result.get("title") or ""
    # Headings: extract_url_text exposes structured `headings` if available.
    headings = ""
    h_list = result.get("headings")
    if isinstance(h_list, list):
        headings = " | ".join(str(h)[:200] for h in h_list[:20])
    elif isinstance(h_list, str):
        headings = h_list[:2000]

    await db.mark_domain_crawled(domain)

    return {
        "url": url,
        "canonical_url": db._canonicalize(url),
        "domain": domain,
        "title": title,
        "headings": headings,
        "body": body,
        "language": d_row.get("language"),  # seed-list language; indexer can override via detection
        "source_tier": d_row.get("tier"),
        "http_status": 200,
        "fetched_at": time.time(),
        "extraction_ok": True,
        "duration_ms": duration_ms,
    }


# Test-only helper: bypass extract_url_text (which requires httpx + LLM
# imports) and synthesise a fake fetch result. Used by R-F501 tests so
# they don't have to hit the network or import researcher.py.
async def _test_synthesize_fetch_result(
    url: str, *, title: str, headings: str, body: str,
    language: str | None = None,
) -> dict | None:
    """Synthesised fetch result for tests. Still goes through the
    politeness + robots + domain-registered gates so tests verify those
    paths."""
    domain = politeness.domain_of(url)
    d_row = await db.get_domain(domain)
    if d_row is None or not d_row.get("enabled"):
        return None
    if not await politeness.is_allowed(url, user_agent=_USER_AGENT):
        return None
    await politeness.acquire(domain)
    await db.mark_domain_crawled(domain)
    return {
        "url": url,
        "canonical_url": db._canonicalize(url),
        "domain": domain,
        "title": title,
        "headings": headings,
        "body": body,
        "language": language or d_row.get("language"),
        "source_tier": d_row.get("tier"),
        "http_status": 200,
        "fetched_at": time.time(),
        "extraction_ok": True,
        "duration_ms": 0,
    }
