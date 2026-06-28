"""
Centralized User-Agent rotation pool for all ARIA HTTP clients.

2026-04-12: unified from deep_researcher.py's local pool. All modules
(researcher, web_search, tender_monitor, sanctions, registry_adapters)
should import random_ua() from here instead of hard-coding UA strings.

Anti-bot best practices:
  - 12 realistic browser UAs (Chrome/Firefox/Safari/Edge on Win/Mac/Linux)
  - Random delay helper for polite crawling
  - Rotate on every request to avoid fingerprinting
"""
import random
import asyncio

_USER_AGENTS = [
    # Chrome on Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    # Chrome on Mac
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    # Chrome on Linux
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    # Firefox on Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    # Firefox on Mac
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14.5; rv:125.0) Gecko/20100101 Firefox/125.0",
    # Firefox on Linux
    "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:125.0) Gecko/20100101 Firefox/125.0",
    # Safari on Mac
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    # Edge on Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0",
    # Samsung Internet
    "Mozilla/5.0 (Linux; Android 14; SM-S918B) AppleWebKit/537.36 (KHTML, like Gecko) SamsungBrowser/24.0 Chrome/122.0.0.0 Mobile Safari/537.36",
    # Chrome on Android
    "Mozilla/5.0 (Linux; Android 14; Pixel 8 Pro) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36",
    # Safari on iOS
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1",
]


def random_ua() -> str:
    """Return a random User-Agent string from the pool."""
    # R-F1001 - wire to brain
    from .engine_wiring import wire_success, wire_failure
    wire_success(
        module="ua_rotation",
        summary="Random Ua",
        source_id="ua_rotation:R-F1001",
    )

    return random.choice(_USER_AGENTS)


def random_headers() -> dict:
    """Return common browser headers with a random UA.

    R-F273 (2026-05-11) — enriched with Sec-Fetch-* + DNT +
    Upgrade-Insecure-Requests + Connection so anti-bot fingerprinters
    (Cloudflare/AWS WAF/Akamai) don't trivially identify the request as
    non-browser. AfDB and SEACE Peru were both returning 403 with the
    bare User-Agent + Accept pair; adding the realistic header set
    pushes through most "Default-deny anything that isn't a browser"
    rules. These additions are safe for APIs (REST endpoints ignore
    Sec-Fetch-* headers entirely) so the existing TED/SAM.gov/Crossref
    callers continue to work unchanged.
    """
    return {
        "User-Agent": random_ua(),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        # Browser-fingerprint hardening (R-F273)
        "Connection": "keep-alive",
        "DNT": "1",
        "Upgrade-Insecure-Requests": "1",
        # Sec-Fetch-* — every Chrome/Firefox HTML request sends these.
        # Absence is the most common anti-bot trip signal in 2025+.
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        # Cache-Control matches a fresh navigation, not a stale fetch
        "Cache-Control": "max-age=0",
    }


async def polite_delay(min_sec: float = 1.5, max_sec: float = 5.0) -> None:
    """Random delay between requests to avoid rate-limiting."""
    await asyncio.sleep(random.uniform(min_sec, max_sec))
