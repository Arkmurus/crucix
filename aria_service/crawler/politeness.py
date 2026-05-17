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

# R-F653 (2026-05-17): per-domain async lock so N concurrent callers
# don't all fire _fetch_robots_txt in parallel. Live evidence at
# 08:03:09 UTC: 11 hits to www.imo.org/robots.txt in 150ms because
# every parallel chat-pipeline caller raced past the cache check.
_robots_fetch_locks: dict[str, asyncio.Lock] = {}

# How long to keep robots.txt in the DB before re-fetching.
_ROBOTS_TTL_SEC = 24 * 60 * 60
# R-F653: shorter TTL when the cached result is empty (server returned
# nothing or hit a redirect loop). Long enough to dampen the burst,
# short enough that a fixed server is re-probed within an hour.
_ROBOTS_EMPTY_TTL_SEC = 60 * 60


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
    Failures are silent — caller treats empty as 'no restrictions'.

    R-F523 (2026-05-14) — max_redirects=5 to defuse upstream 301-loops.
    Live 12:41:30 BST cold boot after R-F522 deployed: imo.org/robots.txt
    fired 40+ identical 301s in 360ms via this fetcher (R-F522 only
    capped crawl_enhancements.check_robots, which is a different
    robots.txt code path). Same server-side bug (Location header points
    to the same URL we just came from) — same fix shape as R-F522."""
    import httpx
    for scheme in ("https", "http"):
        try:
            async with httpx.AsyncClient(timeout=timeout,
                                          follow_redirects=True,
                                          max_redirects=5) as c:
                resp = await c.get(f"{scheme}://{domain}/robots.txt",
                                   headers={"User-Agent": "ARIAsBot/1.0"})
                if resp.status_code == 200 and resp.text:
                    return resp.text
        except Exception:
            continue
    return ""


async def _get_robots_parser(domain: str) -> RobotFileParser:
    """Return a parsed robots.txt for `domain`, using the SQLite cache
    when fresh and re-fetching when stale or missing.

    R-F653 (2026-05-17): wrapped in a per-domain lock so concurrent
    callers don't all fire _fetch_robots_txt in parallel (live evidence
    of 11 racing fetches on www.imo.org/robots.txt in 150ms). Empty
    results are now ALWAYS cached — pre-R-F653 the `if d_row and robots`
    gate threw away empty fetches, causing the next caller to refetch
    from scratch even though we'd just learned the server was broken."""
    # Fast path — already-parsed parser available.
    rp = _robots_cache.get(domain)
    if rp is not None:
        return rp

    # Serialise concurrent fills for this domain.
    lock = _robots_fetch_locks.get(domain)
    if lock is None:
        lock = asyncio.Lock()
        _robots_fetch_locks[domain] = lock

    async with lock:
        # Re-check inside the lock — a peer may have filled it while we
        # were waiting on the lock.
        rp = _robots_cache.get(domain)
        if rp is not None:
            return rp

        d_row = await db.get_domain(domain)
        cached_at = d_row.get("robots_fetched_at") if d_row else None
        robots = d_row.get("robots_txt") if d_row else None

        # R-F653: distinguish staleness from "no cache" — once we have a
        # cached_at timestamp, the SQLite row is the source of truth even
        # if the cached body is empty. TTL is shorter for empty results
        # so a fixed upstream is re-probed within an hour.
        is_empty = not robots
        ttl = _ROBOTS_EMPTY_TTL_SEC if is_empty else _ROBOTS_TTL_SEC
        stale = (cached_at is None) or (_now() - cached_at > ttl)

        if stale:
            robots = await _fetch_robots_txt(domain)
            # Always cache, including empty — that's the whole point of
            # R-F653. The previous `if robots:` gate meant a broken
            # robots endpoint refetched on every request.
            if d_row is not None:
                await db.cache_robots(domain, robots or "")

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
        _robots_fetch_locks.clear()
        return
    _buckets.pop(domain, None)
    _robots_cache.pop(domain, None)
    _robots_fetch_locks.pop(domain, None)
