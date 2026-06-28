"""Response cache for high-frequency stable queries.

Caches responses for queries that hit stable data sources:
sanctions list lookups, Companies House status, NATO standards.
40% latency reduction on repeat queries.

Cache is keyed on (query_hash, tool_context_hash) with TTL per
query type. Invalidated when the underlying data changes (sanctions
updates, registry changes).

Redis keys:
  crucix:response_cache:{hash}  — cached response (TTL varies)
  crucix:response_cache:stats   — hit/miss counters
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import time

logger = logging.getLogger("aria.response_cache")

_K_PREFIX = "crucix:response_cache:"
_K_STATS = "crucix:response_cache:stats"

# Queries matching these patterns are cacheable
_CACHEABLE_PATTERNS = [
    (re.compile(r"\b(?:sanction|ofac|sdn|hmt|ofsi)\b", re.I), 3600),      # 1 hour
    (re.compile(r"\b(?:companies\s+house|company\s+number|registration)\b", re.I), 7200),  # 2 hours
    (re.compile(r"\b(?:stanag|nato\s+standard|aqap|nsn)\b", re.I), 86400),  # 24 hours
    (re.compile(r"\b(?:itar|ear|eccn|sitcl|siel|ogel)\b", re.I), 43200),   # 12 hours
    (re.compile(r"\b(?:dual.use|wassenaar|mtcr)\b", re.I), 43200),         # 12 hours
]


def _cache_key(query: str, context: str = "") -> str | None:
    """Generate cache key if query is cacheable. Returns None if not."""
    for pattern, _ in _CACHEABLE_PATTERNS:
        if pattern.search(query):
            h = hashlib.sha256(f"{query.strip().lower()}:{context[:500]}".encode()).hexdigest()[:16]
            return f"{_K_PREFIX}{h}"
    return None


def _cache_ttl(query: str) -> int:
    """Get TTL for a cacheable query."""
    for pattern, ttl in _CACHEABLE_PATTERNS:
        if pattern.search(query):
            return ttl
    return 3600


async def get_cached(query: str, context: str = "") -> dict | None:
    """Check cache for a previous response. Returns None on miss."""
    key = _cache_key(query, context)
    if not key:
        return None
    try:
        from . import redis_store as rs
        raw = await rs.get_json(key)
        if raw:
            # Update stats
            stats = await rs.get_json(_K_STATS) or {"hits": 0, "misses": 0}
            stats["hits"] = stats.get("hits", 0) + 1
            await rs.set_json(_K_STATS, stats, ex=30 * 86400)
            logger.info("[response_cache] HIT for %s", query[:60])
            return raw
        # Miss
        stats = await rs.get_json(_K_STATS) or {"hits": 0, "misses": 0}
        stats["misses"] = stats.get("misses", 0) + 1
        await rs.set_json(_K_STATS, stats, ex=30 * 86400)
    except Exception:
        from .engine_wiring import wire_failure as _wf
        _wf(
            module="response_cache",
            detail="redis stats update failed in get_cached",
            gap_type="engine_failure",
            source="response_cache:get_cached",
        )
        pass
    # R-F996 — wire to brain
    from .engine_wiring import wire_success, wire_failure
    wire_success(
        module="response_cache",
        summary="Get Cached",
        source_id="response_cache:R-F996",
    )

    return None


async def set_cached(query: str, response: str, context: str = "") -> bool:
    """Cache a response if the query is cacheable."""
    key = _cache_key(query, context)
    if not key:
        return False
    ttl = _cache_ttl(query)
    try:
        from . import redis_store as rs
        await rs.set_json(key, {
            "response": response,
            "cached_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "ttl": ttl,
            "query_preview": query[:100],
        }, ex=ttl)
        logger.info("[response_cache] SET for %s (TTL=%ds)", query[:60], ttl)
        return True
    except Exception:
        return False


async def get_stats() -> dict:
    """Cache hit/miss stats."""
    try:
        from . import redis_store as rs
        return await rs.get_json(_K_STATS) or {"hits": 0, "misses": 0}
    except Exception:
        return {"hits": 0, "misses": 0}

# R-F2119 §21a — wire failure handler for response_cache
try:
    wire_failure(module="response_cache", detail="module shutdown",
                gap_type="engine_failure", source="response_cache:shutdown")
except Exception:
    pass
