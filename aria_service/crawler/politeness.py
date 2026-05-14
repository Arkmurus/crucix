"""politeness — per-domain rate limiting + robots.txt enforcement.

R-F501 (2026-05-14). The crawler must:
  1. Respect each domain's robots.txt (fetched once, cached 24h).
  2. Throttle to ≤ rate_limit_per_sec per domain — even when many
     coroutines try to hit the same host at once.

Token bucket per domain. Robots.txt is fetched lazily (on first request
per domain) and cached via the `domains.robots_txt` column in
search_index.db so it survives a process restart.

This module deliberately holds NO global state besides the in-process
token-bucket dict — every persistent fact lives in the SQLite domains
table so different crawler workers (eventually) see the same view.

Public:
    async def is_allowed(url: str, user_agent: str) -> bool
    async def acquire(domain: str) -> None            # blocks until permitted
    async def reset_bucket(domain: str) -> None       # tests only
"""
from __future__ import annotations

import asyncio
import logging
import time
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

from aria_service.search_index import db

logger = logging.getLogger("aria.crawler.politeness")


# domain -> {tokens: float, last_refill: epoch_s, rate: req/s, lock: Lock}
_buckets: dict[str, dict] = {}
# domain -> RobotFileParser
_robots_cache: dict[str, RobotFileParser] = {}

# How long to keep robots.txt in the DB before re-fetching.
_ROBOTS_TTL_SEC = 24 * 60 * 60


# ─────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────

def _now() -> float:
    return time.time()


def domain_of(url: str) -> str:
    """Lowercase host without leading dot or port."""
    try:
        p = urlparse(url)
        host = (p.netloc or "").lower().split(":")[0].lstrip(".")
        return host
    except Exception:
        return ""


# ─────────────────────────────────────────────────────────────────
# Robots.txt
# ─────────────────────────────────────────────────────────────────

async def _fetch_robots_txt(domain: str, timeout: float = 10.0) -> str:
    """Fetch /robots.txt over http or https; return body or empty string.
    Failures are silent — caller treats empty as 'no restrictions'."""
    import httpx
    for scheme in ("https", "http"):
        try:
            async with httpx.AsyncClient(timeout=timeout,
                                          follow_redirects=True) as c:
                resp = await c.get(f"{scheme}://{domain}/robots.txt",
                                   headers={"User-Agent": "ARIAsBot/1.0"})
                if resp.status_code == 200 and resp.text:
                    return resp.text
        except Exception:
            continue
    return ""


async def _get_robots_parser(domain: str) -> RobotFileParser:
    """Return a parsed robots.txt for `domain`, using the SQLite cache
    when fresh and re-fetching when stale or missing."""
    rp = _robots_cache.get(domain)
    if rp is not None:
        return rp

    d_row = await db.get_domain(domain)
    cached_at = d_row.get("robots_fetched_at") if d_row else None
    robots = d_row.get("robots_txt") if d_row else None

    if not robots or not cached_at or _now() - cached_at > _ROBOTS_TTL_SEC:
        robots = await _fetch_robots_txt(domain)
        if d_row and robots:
            await db.cache_robots(domain, robots)

    rp = RobotFileParser()
    if robots:
        rp.parse(robots.splitlines())
    else:
        # Empty parser allows everything by default.
        rp.parse([])
    _robots_cache[domain] = rp
    return rp


async def is_allowed(url: str, user_agent: str = "ARIAsBot/1.0") -> bool:
    """True if robots.txt permits this URL for our UA. On any error,
    we default OPEN — a missing or malformed robots.txt does not block
    crawling per RFC 9309."""
    domain = domain_of(url)
    if not domain:
        return False
    try:
        rp = await _get_robots_parser(domain)
        return bool(rp.can_fetch(user_agent, url))
    except Exception:
        return True


# ─────────────────────────────────────────────────────────────────
# Token bucket per domain
# ─────────────────────────────────────────────────────────────────

async def _bucket(domain: str) -> dict:
    b = _buckets.get(domain)
    if b is not None:
        return b

    # Read the per-domain rate from search_index.db; fall back to 1 r/s.
    rate = 1.0
    try:
        d_row = await db.get_domain(domain)
        if d_row and d_row.get("rate_limit_per_sec"):
            rate = float(d_row["rate_limit_per_sec"])
    except Exception:
        pass

    # Politeness-first: one free token to start, then strict refill at
    # `rate` tokens/sec up to a small burst capacity (capped at 3 even
    # when the configured rate is higher). This prevents thundering
    # herds on cold start while still permitting modest concurrency.
    b = {
        "tokens": 1.0,
        "last_refill": _now(),
        "rate": max(0.1, rate),
        "capacity": max(1.0, min(rate, 3.0)),
        "lock": asyncio.Lock(),
    }
    _buckets[domain] = b
    return b


async def acquire(domain: str) -> None:
    """Block until one token is available for `domain`. Token bucket
    refills continuously at `rate` per second up to `capacity`."""
    if not domain:
        return
    b = await _bucket(domain)
    async with b["lock"]:
        while True:
            now = _now()
            elapsed = now - b["last_refill"]
            b["tokens"] = min(b["capacity"],
                              b["tokens"] + elapsed * b["rate"])
            b["last_refill"] = now
            if b["tokens"] >= 1.0:
                b["tokens"] -= 1.0
                return
            # Sleep just long enough to earn one token.
            wait = (1.0 - b["tokens"]) / b["rate"]
            # Release the lock around the sleep so a parallel reset_bucket
            # can intervene if needed.
            b["lock"].release()
            try:
                await asyncio.sleep(wait)
            finally:
                await b["lock"].acquire()


async def reset_bucket(domain: str | None = None) -> None:
    """Drop the in-memory bucket (and robots cache for that domain).
    Tests use this between scenarios; production should not call it."""
    if domain is None:
        _buckets.clear()
        _robots_cache.clear()
        return
    _buckets.pop(domain, None)
    _robots_cache.pop(domain, None)
