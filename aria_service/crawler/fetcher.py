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

# R-F720 (2026-05-19) — BeautifulSoup emits XMLParsedAsHTMLWarning when
# we pass an XML payload (RSS, sitemap, OpenSearch) to the HTML parser.
# Behaviourally the HTML parser handles it fine and we have lxml + a
# html.parser fallback already; the warning was just noise in fly logs
# (seen 2026-05-19 09:33:14). Silence it at module load — we'd already
# need to detect XML payloads to pick the `features="xml"` parser, and
# the existing dual-parser logic doesn't care.
import warnings as _warnings
try:
    from bs4 import XMLParsedAsHTMLWarning as _XMLParsedAsHTMLWarning
    _warnings.filterwarnings("ignore", category=_XMLParsedAsHTMLWarning)
except Exception:
    pass

import asyncio
import logging
import time
from typing import Any

import httpx

from aria_service.search_index import db
from . import politeness
from ..intel.engine_wiring import wire_success, wire_failure  # R-F2489 §21a success+failure

logger = logging.getLogger("aria.crawler.fetcher")


# R-F2947 — deterministic "this hostname does not exist" DNS signatures. These
# mean the name has no A/AAAA record (NXDOMAIN-class) and will not start
# resolving on retry — safe to disable the domain. Deliberately EXCLUDES
# transient resolver failures ("Temporary failure in name resolution", errno -3),
# which can recover, and connection-refused/reset (host exists, port down).
_DNS_NAME_FAILURE_SIGNATURES = (
    "name or service not known",        # glibc getaddrinfo EAI_NONAME (errno -2)
    "errno -2",
    "no address associated with hostname",  # EAI_NODATA (errno -5)
    "errno -5",
    "nodename nor servname provided",   # macOS/BSD EAI_NONAME
    "name does not resolve",
)


def _is_dns_name_failure(msg: str) -> bool:
    m = (msg or "").lower()
    return any(sig in m for sig in _DNS_NAME_FAILURE_SIGNATURES)


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
        # R-F2489 §21a — genuine failure (was log-only/dark) reaches the brain.
        wire_failure(module="crawler_fetcher",
                     detail=f"fetch_for_index import extract_url_text failed: {e}",
                     gap_type="engine_failure", source="crawler_fetcher")
        return None

    t0 = time.time()
    try:
        result = await extract_url_text(url, timeout=timeout)
    except Exception as e:
        logger.debug("fetcher: extract_url_text raised on %s: %s",
                     url[:120], e)
        # R-F2489 §21a — genuine failure (was log-only/dark) reaches the brain.
        wire_failure(module="crawler_fetcher",
                     detail=f"fetch_for_index extract raised on {url[:120]}: {e}",
                     gap_type="engine_failure", source="crawler_fetcher")
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

    # R-F2489 §21a — success branch reaches the brain.
    wire_success(module="crawler_fetcher", summary=f"fetch_for_index ok: {domain}")
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
            # R-F688 (2026-05-18) — cap redirect chains at 5 hops.
            # httpx's default is 20; live fly logs 2026-05-18 10:36:45
            # showed a single email.net crawl burning ~30 hops looping on
            # `/mailfence.com/` (parked domain rotation). 5 is enough for
            # any legitimate chain (https→http, www, region-redirect,
            # canonical) and bounds wasted bandwidth + log spam when an
            # auto-registered hallucinated parked domain loops on itself.
            # Mirrors politeness.py:90 which already capped at 5.
            max_redirects=5,
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
    except httpx.ConnectError as e:
        # R-F2947 — a DNS/connection failure to an EXTERNAL crawl target is not an
        # ARIA engine failure. R-F2489's §21a wiring recorded every one as an
        # `engine_failure` capability gap; live 2026-07-23 that flooded the
        # self-improve queue with ~70 gaps / 6 min, ALL speculative permuted-TLD
        # domains that don't resolve (`[Errno -2] Name or service not known`) —
        # un-fixable noise that drowns real gaps. Classify honestly (still counted
        # in the cycle by_status), do NOT gap, and on a deterministic name-not-known
        # DISABLE the dead auto-registered domain so the loop stops hammering it
        # every cycle (§7 reversible-disable, never delete; tier-4 auto-registered
        # only — never silently kill an operator/DD-curated source).
        logger.debug("fetcher.crawl: connect error on %s: %s", url[:120], e)
        if _is_dns_name_failure(str(e)):
            status_class = "dns_error"
            if int((d_row or {}).get("tier") or 4) >= 4:
                try:
                    if await db.disable_domain(domain, reason=f"NXDOMAIN: {str(e)[:80]}"):
                        logger.info("fetcher.crawl: disabled dead domain %s (%s)",
                                    domain, str(e)[:80])
                except Exception:
                    pass
        else:
            status_class = "error"
    except httpx.HTTPError as e:
        logger.debug("fetcher.crawl: http error on %s: %s", url[:120], e)
        status_class = "error"
        # R-F2489 §21a — genuine protocol/handling failure (not a dead target) → brain.
        wire_failure(module="crawler_fetcher",
                     detail=f"fetch_for_crawl http error on {url[:120]}: {e}",
                     gap_type="engine_failure", source="crawler_fetcher")
    except Exception as e:
        logger.debug("fetcher.crawl: %s raised on %s", e, url[:120])
        status_class = "error"
        # R-F2489 §21a — unexpected failure (was log-only) → brain.
        wire_failure(module="crawler_fetcher",
                     detail=f"fetch_for_crawl raised on {url[:120]}: {e}",
                     gap_type="engine_failure", source="crawler_fetcher")

    duration_ms = int((time.time() - t0) * 1000)
    await db.mark_domain_crawled(domain)

    if status_class != "ok":
        return {"url": url, "domain": domain,
                "status_class": status_class,
                "status_code": status_code,
                "extraction_ok": False,
                "duration_ms": duration_ms}

    # R-F727 (2026-05-19) — wedge_673 captured a stale heartbeat
    # (5.89s) with the main thread mid-`bs4/builder/_lxml.py feed` inside
    # `_strip_html_to_text` called from this line. BeautifulSoup + lxml
    # holds the GIL through C-extension calls but the surrounding Python
    # bookkeeping (tree walk, decompose, get_text) is pure-Python and
    # runs on the loop. On a large feed page (multi-100 KB), the parse
    # routinely takes 5–15s. Move to a worker so concurrent crawl
    # fetches + chat requests can run while a page is being stripped.
    # R-F1882 — share the global CPU-serialisation gate (same semaphore the JSON
    # snapshot encoders use) so this pure-Python BeautifulSoup parse (holds the
    # GIL 5-15s on big pages) can't run concurrently with a JSON encode or another
    # parse and starve the event-loop heartbeat (R-F703 GIL wedge).
    from ..intel._snapshot_throttle import run_in_thread_cpu
    title, headings, body = await run_in_thread_cpu(_strip_html_to_text, html)
    if not body or len(body.split()) < 20:
        return {"url": url, "domain": domain,
                "status_class": "thin",
                "status_code": status_code,
                "extraction_ok": False,
                "duration_ms": duration_ms}

    # R-F2489 §21a — success branch reaches the brain.
    wire_success(module="crawler_fetcher", summary=f"fetch_for_crawl ok: {domain}")
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
