"""R-F653 — robots.txt fetch is serialised per domain + empty results cached.

Live evidence 2026-05-17 08:03:09 UTC: 11 hits to www.imo.org/robots.txt
in 150ms because parallel callers all raced past the parser cache check
before any of them populated it. The server returned 301-loop (Location:
itself), max_redirects exhausted, fetch returned empty — and the pre-
R-F653 cache logic threw the empty result away because of
`if d_row and robots:`. So the NEXT caller refetched from scratch.

Two fixes:
  1. Per-domain asyncio.Lock so only one fetch fires at a time.
  2. Cache the empty result (always-cache, with a shorter TTL for empty)
     so subsequent calls hit the SQLite cache and not the network.
"""
from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, patch

import pytest

from aria_service.crawler import politeness


# ── _ROBOTS_FETCH_LOCKS — exists + per-domain ─────────────────────────────

def test_rf653_per_domain_locks_dict_exists():
    """The module must expose a per-domain lock dict so concurrent
    callers can be serialised."""
    assert hasattr(politeness, "_robots_fetch_locks")
    assert isinstance(politeness._robots_fetch_locks, dict)


def test_rf653_empty_ttl_constant_exists():
    """A shorter TTL for empty cached robots must exist so a broken
    server is re-probed sooner than 24h."""
    assert hasattr(politeness, "_ROBOTS_EMPTY_TTL_SEC")
    assert 0 < politeness._ROBOTS_EMPTY_TTL_SEC < politeness._ROBOTS_TTL_SEC


# ── Concurrency: only one fetch fires for N parallel callers ──────────────

def test_rf653_concurrent_callers_share_one_fetch():
    """N concurrent _get_robots_parser calls for the same domain must
    fire _fetch_robots_txt at most once. This is the live-evidence
    behavioural test for R-F653."""
    politeness._robots_cache.clear()
    politeness._robots_fetch_locks.clear()

    fetch_count = {"n": 0}

    async def _slow_fetch(domain, timeout=10.0):
        # Yield to ensure the racing coroutines all arrive at the lock
        # before any one of them returns.
        await asyncio.sleep(0.02)
        fetch_count["n"] += 1
        return ""  # empty body — simulates broken server

    async def _fake_get_domain(domain):
        return {"domain": domain, "robots_txt": None, "robots_fetched_at": None}

    async def _fake_cache_robots(domain, robots_txt):
        return None

    async def runner():
        with patch.object(politeness, "_fetch_robots_txt", _slow_fetch), \
             patch.object(politeness.db, "get_domain", _fake_get_domain), \
             patch.object(politeness.db, "cache_robots", _fake_cache_robots):
            await asyncio.gather(*[
                politeness._get_robots_parser("imo.org") for _ in range(11)
            ])

    asyncio.run(runner())
    assert fetch_count["n"] == 1, (
        f"R-F653 regression: 11 concurrent callers fired "
        f"{fetch_count['n']} fetches — expected 1. Per-domain lock is "
        f"not serialising fills."
    )


# ── Empty results are cached so the next caller does not refetch ──────────

def test_rf653_empty_result_is_cached():
    """When _fetch_robots_txt returns empty (broken server / redirect
    loop / timeout), the result must be persisted via cache_robots so
    subsequent calls within the empty-TTL don't re-fetch."""
    politeness._robots_cache.clear()
    politeness._robots_fetch_locks.clear()

    cached: dict = {"value": None}

    async def _fetch_returns_empty(domain, timeout=10.0):
        return ""

    async def _fake_get_domain(domain):
        return {"domain": domain, "robots_txt": None, "robots_fetched_at": None}

    async def _fake_cache_robots(domain, robots_txt):
        cached["value"] = robots_txt

    async def runner():
        with patch.object(politeness, "_fetch_robots_txt", _fetch_returns_empty), \
             patch.object(politeness.db, "get_domain", _fake_get_domain), \
             patch.object(politeness.db, "cache_robots", _fake_cache_robots):
            await politeness._get_robots_parser("broken-imo-like.example")

    asyncio.run(runner())
    assert cached["value"] is not None, (
        "R-F653 regression: empty result not cached — broken server "
        "will be refetched on every is_allowed() call."
    )
    # Empty string is the expected cached value (NOT None)
    assert cached["value"] == "", (
        f"R-F653: expected empty-string cache, got {cached['value']!r}"
    )


# ── Non-empty results still cached normally ───────────────────────────────

def test_rf653_non_empty_result_still_cached():
    """The empty-cache change must not regress the normal-case caching."""
    politeness._robots_cache.clear()
    politeness._robots_fetch_locks.clear()

    cached: dict = {"value": None}

    async def _fetch_returns_real(domain, timeout=10.0):
        return "User-agent: *\nDisallow: /private/\n"

    async def _fake_get_domain(domain):
        return {"domain": domain, "robots_txt": None, "robots_fetched_at": None}

    async def _fake_cache_robots(domain, robots_txt):
        cached["value"] = robots_txt

    async def runner():
        with patch.object(politeness, "_fetch_robots_txt", _fetch_returns_real), \
             patch.object(politeness.db, "get_domain", _fake_get_domain), \
             patch.object(politeness.db, "cache_robots", _fake_cache_robots):
            rp = await politeness._get_robots_parser("realsite.example")
            # And the parser must reflect the fetched body
            assert rp.can_fetch("ARIAsBot/1.0", "https://realsite.example/page") is True
            assert rp.can_fetch("ARIAsBot/1.0", "https://realsite.example/private/x") is False

    asyncio.run(runner())
    assert cached["value"] == "User-agent: *\nDisallow: /private/\n"


# ── Cache freshness uses cached_at, not body presence ─────────────────────

def test_rf653_fresh_empty_cache_is_not_refetched():
    """If the SQLite cache has an empty body but a fresh `cached_at`
    timestamp (within the empty-TTL), _get_robots_parser must NOT
    refetch — that was the pre-R-F653 bug."""
    politeness._robots_cache.clear()
    politeness._robots_fetch_locks.clear()

    fetch_count = {"n": 0}

    async def _fetch(domain, timeout=10.0):
        fetch_count["n"] += 1
        return "User-agent: *\nDisallow:\n"  # would be fresh content

    fresh_ts = time.time() - 10  # 10s ago, well inside the empty-TTL

    async def _fake_get_domain(domain):
        return {
            "domain": domain,
            "robots_txt": "",          # empty body cached previously
            "robots_fetched_at": fresh_ts,
        }

    async def _fake_cache(domain, robots_txt):
        return None

    async def runner():
        with patch.object(politeness, "_fetch_robots_txt", _fetch), \
             patch.object(politeness.db, "get_domain", _fake_get_domain), \
             patch.object(politeness.db, "cache_robots", _fake_cache):
            await politeness._get_robots_parser("fresh-empty.example")

    asyncio.run(runner())
    assert fetch_count["n"] == 0, (
        f"R-F653 regression: fresh empty cache triggered a refetch "
        f"({fetch_count['n']} fetches). The SQLite row's cached_at "
        f"should be the source of truth, not the presence of body."
    )


# ── reset_bucket clears the lock dict too ─────────────────────────────────

def test_rf653_reset_bucket_clears_locks():
    """reset_bucket must drop the per-domain lock so tests get clean state."""
    politeness._robots_fetch_locks["temp.example"] = asyncio.Lock()
    asyncio.run(politeness.reset_bucket("temp.example"))
    assert "temp.example" not in politeness._robots_fetch_locks

    politeness._robots_fetch_locks["a.example"] = asyncio.Lock()
    politeness._robots_fetch_locks["b.example"] = asyncio.Lock()
    asyncio.run(politeness.reset_bucket(None))
    assert len(politeness._robots_fetch_locks) == 0
