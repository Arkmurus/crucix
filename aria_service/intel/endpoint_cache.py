"""R-F2063 — async endpoint cache (stale-while-revalidate) for expensive,
poll-heavy endpoints.

WHY: the brain is a SINGLE-PROCESS asyncio app (one uvicorn worker → one event
loop on one core; the extra CPUs only help offloaded work). An expensive endpoint
like ``/api/aria/health`` (~21s of Redis/stats/breaker aggregation, and
``/health/perf`` even heavier) recomputes its whole aggregation on EVERY poll —
monitoring, the deploy regression suite, the dashboard — and each recompute ties
up the one loop, stalling every concurrent request. That is the "huge CPU but
slow" symptom.

WHAT: cache the result per-function with a short TTL and serve a slightly-stale
value INSTANTLY while refreshing once in the background. After the first (cold)
call, no request ever waits for the full recompute again.

  - fresh (age < ttl)        → return cached value immediately
  - stale (age ≥ ttl)        → return the stale value immediately AND kick off a
                               single background refresh (single-flight)
  - cold (never computed)    → compute under a lock so concurrent first-callers
                               don't stampede (single-flight)

A process restart (deploy) starts empty, so post-restart callers get a freshly
computed value (no stale build_rev / health right after a deploy).

SCOPE: apply ONLY to endpoints whose result does NOT depend on per-request args
(the cache keeps ONE value per decorated function). The health endpoints are
param-less, so this holds. Place the decorator INNERMOST (closest to `async def`).
"""
from __future__ import annotations

import asyncio
import functools
import logging
import time
from typing import Any, Callable

logger = logging.getLogger("aria.endpoint_cache")

_MISS = object()

# Registry so a single /diagnostic can report every cached endpoint's stats.
_REGISTRY: dict[str, "_Entry"] = {}


class _Entry:
    __slots__ = ("value", "ts", "refreshing", "lock", "hits", "misses", "stale_serves")

    def __init__(self) -> None:
        self.value: Any = _MISS
        self.ts: float = 0.0
        self.refreshing: bool = False
        self.lock = asyncio.Lock()
        self.hits = 0
        self.misses = 0
        self.stale_serves = 0


async def _refresh(fn: Callable, entry: _Entry, label: str, args: tuple, kwargs: dict,
                   timeout_s: float = 30.0) -> None:
    """Recompute in the background and replace the cached value. On failure, keep
    the existing (stale) value so a transient hiccup never blanks the endpoint."""
    try:
        # R-F2091 — bound so a hung compute can't leave entry.refreshing stuck True
        # (which would silently stop all future stale-while-revalidate refreshes).
        res = await asyncio.wait_for(fn(*args, **kwargs), timeout=timeout_s)
        entry.value = res
        entry.ts = time.monotonic()
    except Exception as e:  # keep stale value; never propagate from a bg task
        logger.debug("[endpoint_cache] background refresh of %s failed: %s", label, e)
        # §21a — a cached endpoint whose background refresh keeps failing is a
        # DEGRADED endpoint serving ever-staler data; surface it to the brain
        # (deduped by capability_gaps, so a recurring failure is one gap, not a flood).
        try:
            from .engine_wiring import wire_failure
            wire_failure(
                module="endpoint_cache",
                detail=f"background refresh of cached endpoint '{label}' failed: {type(e).__name__}: {e}",
                gap_type="engine_failure",
                source="endpoint_cache:_refresh",
            )
        except Exception:
            pass
    finally:
        entry.refreshing = False


def cached_endpoint(ttl_s: float = 25.0, *, name: str = "",
                    compute_timeout_s: float = 30.0) -> Callable:
    """Decorator for an async, param-less endpoint. Exposes `.cache_entry` +
    `.cache_stats()` for tests/inspection.

    R-F2091 — `compute_timeout_s` bounds every `await fn()` (cold path AND the
    proactive refresh_now). WITHOUT it, a single component that hangs on a bad
    boot (cold rag/chromadb, a contended lock) would hold the entry lock forever
    while the value is still _MISS — and every reader then blocks on that lock
    indefinitely (live incident 2026-06-28: /api/aria/health hung >90s after the
    first refresh_now wedged). With the timeout, a hung compute releases the lock,
    raises, and the next refresh tick retries — so a transient slow component can
    never PERMANENTLY wedge the endpoint."""

    def deco(fn: Callable) -> Callable:
        entry = _Entry()
        label = name or getattr(fn, "__name__", "fn")
        _REGISTRY[label] = entry

        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            now = time.monotonic()
            v = entry.value
            if v is not _MISS and (now - entry.ts) < ttl_s:
                entry.hits += 1
                return v
            if v is not _MISS:
                # stale → serve immediately, refresh once in the background
                entry.stale_serves += 1
                if not entry.refreshing:
                    entry.refreshing = True
                    asyncio.create_task(_refresh(fn, entry, label, args, kwargs, compute_timeout_s))
                return v
            # cold: single-flight compute so concurrent first-callers don't stampede
            async with entry.lock:
                if entry.value is not _MISS and (time.monotonic() - entry.ts) < ttl_s:
                    entry.hits += 1
                    return entry.value
                entry.misses += 1
                # R-F2091 — bound the cold compute so a hung component can't hold
                # the lock forever (which would block every subsequent reader).
                # R-F2150: catch TimeoutError gracefully — during boot the state
                # store may not be ready yet. Return a fallback instead of letting
                # the TimeoutError propagate and crash the request (which causes
                # Fly health checks to fail and the deploy to roll back).
                try:
                    res = await asyncio.wait_for(fn(*args, **kwargs), timeout=compute_timeout_s)
                except TimeoutError:
                    logger.warning(
                        "[R-F2150] endpoint_cache cold compute timed out for %s "
                        "(%.1fs) — returning fallback. This is normal during boot "
                        "when the state store is still initializing.",
                        label, compute_timeout_s,
                    )
                    return None
                entry.value = res
                entry.ts = time.monotonic()
                # §21a — record the endpoint cache going warm (cold→computed). Fires
                # once per endpoint per process (not on hits/refreshes), so it's a
                # low-noise success signal that balances the refresh-failure wire.
                try:
                    from .engine_wiring import wire_success
                    wire_success(module="endpoint_cache", summary=f"cache warm: {label}",
                                 source_id="endpoint_cache:R-F2063")
                except Exception:
                    pass
                return res

        async def _refresh_now(*a: Any, **kw: Any) -> Any:
            """R-F2072 (Tier 0-finish) — directly recompute and store the value,
            for a PROACTIVE background warmer loop. Unlike the request-path
            stale-while-revalidate (which only refreshes when a request arrives),
            a loop calling this on a fixed tick keeps the cache permanently warm so
            the endpoint NEVER computes on the request path — not even on the first
            poll after a cold boot. Single-flight via the same lock the cold path
            uses, so it can't race a request-triggered compute into a double-run."""
            async with entry.lock:
                # R-F2091 — bounded so a hung component times out (releasing the
                # lock) instead of wedging the endpoint until process restart.
                res = await asyncio.wait_for(fn(*a, **kw), timeout=compute_timeout_s)
                entry.value = res
                entry.ts = time.monotonic()
                entry.refreshing = False
                return res

        wrapper.refresh_now = _refresh_now   # type: ignore[attr-defined]
        wrapper.cache_entry = entry          # type: ignore[attr-defined]
        wrapper.cache_stats = lambda: {       # type: ignore[attr-defined]
            "name": label, "ttl_s": ttl_s, "hits": entry.hits,
            "misses": entry.misses, "stale_serves": entry.stale_serves,
            "age_s": (time.monotonic() - entry.ts) if entry.value is not _MISS else None,
            "warm": entry.value is not _MISS,
        }
        return wrapper

    return deco


def all_stats() -> dict:
    """Snapshot of every cached endpoint's hit/miss/staleness — for /diagnostics."""
    out = {}
    for label, e in _REGISTRY.items():
        out[label] = {
            "hits": e.hits, "misses": e.misses, "stale_serves": e.stale_serves,
            "warm": e.value is not _MISS,
            "age_s": (time.monotonic() - e.ts) if e.value is not _MISS else None,
        }
    return out
