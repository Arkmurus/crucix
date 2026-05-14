"""fetcher — polite, robots-aware HTTP fetch for the search index.

R-F501 (2026-05-14). Originally a thin coordination layer around
researcher.extract_url_text — that primitive has Wayback + Lightpanda
fallbacks that are right for one-off chat queries but wrong for bulk
crawl.

R-F508 (2026-05-14, ~30 min after R-F507 first cycle) — splits the
fetcher into TWO surfaces:

  fetch_for_crawl(url, timeout=10.0)   ← used by runner.crawl_loop
      Own httpx GET. Single attempt. NO Wayback / Lightpanda fallback.
      On 4xx/5xx/timeout, returns None with a status field so the
      runner can break down the cycle summary by reason. This stops
      the archive.is breaker from being tripped by bulk crawl traffic.

  fetch_for_index(url, timeout=20.0)   ← used by on_demand chat fill
      Original path — delegates to extract_url_text with all its
      fallback chains. Chat-time can afford 30s on a single page.

Both gates go through politeness.acquire + robots + domain registry
checks. Both enrich the result with tier / sector / language from
the domains table.

The crawler's UA is identified ("ARIA-Search-Bot/1.0") so tier-1
institutional sites (AfDB, EU Council, US BIS) can whitelist us
properly — the chat-time path keeps the Chrome rotation since
end-user reads of a single article are not the bot scenario.
"""
from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from aria_service.search_index import db
from . import politeness

logger = logging.getLogger("aria.crawler.fetcher")


# R-F501 default — used by on_demand chat fill (delegates to
# extract_url_text which uses its own random UA rotation).
_USER_AGENT = "ARIAsBot/1.0 (+https://aria-intel.fly.dev/about; respect-robots)"

# R-F508 — identified crawler bot UA. Tier-1 sites that block Chrome-
# masquerading scrapers can whitelist this against the /about page.
_CRAWLER_USER_AGENT = (
    "ARIA-Search-Bot/1.0 (+https://aria-intel.fly.dev/about; "
    "respect-robots; one-req-per-sec)"
)


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


# ─────────────────────────────────────────────────────────────────
# R-F508 — dedicated crawl path: own httpx, no fallback chains
# ─────────────────────────────────────────────────────────────────


def _strip_html_to_text(html: str, max_bytes: int = 200_000) -> tuple[str, str, str]:
    """Minimal HTML → (title, headings_blob, body_text).

    Uses BeautifulSoup's `lxml` parser if available, else the stdlib
    `html.parser`. Drops <script>, <style>, <nav>, <footer>, <aside>,
    <noscript>. Returns plain UTF-8 text capped at max_bytes.

    This is intentionally simpler than researcher._extract_structured_html
    — bulk crawl doesn't need entity extraction at fetch time; the
    indexer does its own sanitisation, language detection, and FTS5
    insert downstream.
    """
    if not html:
        return ("", "", "")
    try:
        from bs4 import BeautifulSoup
    except Exception:
        return ("", "", html[:max_bytes])
    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:
        try:
            soup = BeautifulSoup(html, "html.parser")
        except Exception:
            return ("", "", html[:max_bytes])

    title = ""
    if soup.title and soup.title.string:
        title = soup.title.string.strip()[:500]

    headings: list[str] = []
    for tag in soup.find_all(["h1", "h2", "h3"]):
        txt = (tag.get_text() or "").strip()
        if txt:
            headings.append(txt[:200])
        if len(headings) >= 20:
            break

    for unwanted in soup(["script", "style", "noscript",
                          "nav", "footer", "aside",
                          "header", "form", "iframe"]):
        unwanted.decompose()

    body = soup.get_text(separator="\n")
    if len(body.encode("utf-8", errors="replace")) > max_bytes:
        body = body.encode("utf-8", errors="replace")[:max_bytes].decode(
            "utf-8", errors="ignore")

    return (title, " | ".join(headings), body)


async def fetch_for_crawl(url: str, timeout: float = 10.0) -> dict | None:
    """R-F508 — single-attempt fetch for bulk crawl.

    Goes through the same domain-registered + robots + politeness gates
    as fetch_for_index, then does ONE httpx GET with the identified
    ARIA-Search-Bot UA. No Wayback / Lightpanda fallback — that path
    burns the archive.is rate limit during bulk crawl.

    Returns the same dict shape as fetch_for_index on success. On any
    failure (4xx, 5xx, timeout, connection error), returns a small dict
    `{"url":..., "domain":..., "status_class": "4xx"|"5xx"|"timeout"|"error"}`
    so the runner can break the cycle summary down by reason. The
    caller treats anything WITHOUT extraction_ok=True as "skip this
    page, mark domain crawled, move on".
    """
    domain = politeness.domain_of(url)
    if not domain:
        return None

    d_row = await db.get_domain(domain)
    if d_row is None or not d_row.get("enabled"):
        return None

    if not await politeness.is_allowed(url, user_agent=_CRAWLER_USER_AGENT):
        logger.debug("fetcher.crawl: robots-blocked %s", url[:120])
        return {"url": url, "domain": domain,
                "status_class": "robots_blocked",
                "extraction_ok": False}

    await politeness.acquire(domain)

    t0 = time.time()
    status_code = None
    html = ""
    status_class = "error"
    try:
        async with httpx.AsyncClient(
            timeout=timeout, follow_redirects=True,
            headers={
                "User-Agent": _CRAWLER_USER_AGENT,
                "Accept": ("text/html,application/xhtml+xml,"
                           "application/xml;q=0.9,*/*;q=0.8"),
                "Accept-Language": "en-GB,en;q=0.9",
            },
        ) as client:
            resp = await client.get(url)
            status_code = resp.status_code
            if 200 <= status_code < 300 and resp.text:
                html = resp.text
                status_class = "ok"
            elif 400 <= status_code < 500:
                status_class = "4xx"
            elif 500 <= status_code < 600:
                status_class = "5xx"
            else:
                status_class = f"{status_code}"
    except httpx.TimeoutException:
        status_class = "timeout"
    except httpx.HTTPError as e:
        logger.debug("fetcher.crawl: http error on %s: %s", url[:120], e)
        status_class = "error"
    except Exception as e:
        logger.debug("fetcher.crawl: %s raised on %s", e, url[:120])
        status_class = "error"

    duration_ms = int((time.time() - t0) * 1000)
    await db.mark_domain_crawled(domain)

    if status_class != "ok":
        return {"url": url, "domain": domain,
                "status_class": status_class,
                "status_code": status_code,
                "extraction_ok": False,
                "duration_ms": duration_ms}

    title, headings, body = _strip_html_to_text(html)
    if not body or len(body.split()) < 20:
        return {"url": url, "domain": domain,
                "status_class": "thin",
                "status_code": status_code,
                "extraction_ok": False,
                "duration_ms": duration_ms}

    return {
        "url": url,
        "canonical_url": db._canonicalize(url),
        "domain": domain,
        "title": title,
        "headings": headings,
        "body": body,
        "language": d_row.get("language"),
        "source_tier": d_row.get("tier"),
        "http_status": status_code,
        "status_code": status_code,  # alias for symmetry with failure paths
        "fetched_at": time.time(),
        "extraction_ok": True,
        "duration_ms": duration_ms,
        "status_class": "ok",
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
