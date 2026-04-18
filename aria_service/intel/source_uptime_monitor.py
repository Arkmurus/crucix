"""Source uptime monitor — daily ping of registered sources, auto-suspend.

Why this exists
───────────────
ARIA's self-assessment (2026-04-18) identified a gap: "No system to
detect when a trusted source degrades (goes offline, becomes
unreliable)". web_atlas already tracks reliability EMA on fetched
content; source_validator handles quality at crawl time. What's
missing is a PROACTIVE uptime check — pinging registered sources
independently so we know a source is broken BEFORE we try to cite it.

This module pings every source in web_atlas daily and:
  - Logs HTTP status + latency
  - Auto-suspends sources with reliability EMA < 0.3 AND 3+ consecutive
    ping failures (belt-and-braces — neither signal alone triggers)
  - Publishes health to Redis for dashboard + capability_card
  - Feeds brain_hook when a source is suspended so self_metrics shows
    the drift

Not a replacement for
─────────────────────
  - web_atlas: still tracks per-fetch reliability (live signal)
  - source_validator: still rejects low-quality content at crawl time

This module adds: proactive liveness signal (do sources still respond
at all?) and auto-suspension on combined degradation signals.

Public API
──────────
  async run_daily_ping() -> dict
      Fire the full ping sweep. Returns summary. Called by
      SOURCE-UPTIME-DAILY autonomous task.

  async health() -> dict
      Read latest ping state for dashboard / capability_card.

  async suspend(source: str, reason: str) -> None
      Manually suspend a source. Useful for operator overrides.

  async unsuspend(source: str) -> None
      Lift a suspension.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any

import httpx

logger = logging.getLogger("aria.source_uptime_monitor")

_UA = "ARIA-Source-Monitor/1.0 research@arkmurus.com"

# Thresholds
_RELIABILITY_SUSPEND_THRESHOLD = 0.3   # EMA must be below this
_CONSECUTIVE_PING_FAILS_THRESHOLD = 3  # AND this many consecutive failures
_PING_TIMEOUT_S = 10.0
_MAX_CONCURRENT_PINGS = 10

# Redis keys
_K_LAST_RUN = "crucix:source_uptime:last_run"
_K_SUSPENDED = "crucix:source_uptime:suspended"          # JSON list of source names
_K_PING_HISTORY = "crucix:source_uptime:ping:{src}"      # list, newest first


async def _get_registered_sources() -> list[dict]:
    """Pull the list of sources tracked by web_atlas. Each entry:
      {"name": str, "url": str, "reliability": float, "samples": int}

    We treat web_atlas as authoritative for what IS a source; this
    module only adds the uptime dimension.
    """
    try:
        from . import web_atlas
        # web_atlas exposes various read helpers depending on version.
        # Try the common ones.
        for fn_name in ("list_sources", "get_all_sources", "sources_with_scores"):
            fn = getattr(web_atlas, fn_name, None)
            if callable(fn):
                import inspect
                res = fn()
                if inspect.iscoroutine(res):
                    res = await res
                if res:
                    return list(res)
    except Exception as e:
        logger.debug("[uptime_monitor] web_atlas read failed: %s", e)
    return []


async def _ping_one(source: dict) -> dict:
    """Ping a single source URL. Returns ping result dict."""
    name = source.get("name") or source.get("url") or ""
    url = source.get("url") or source.get("name") or ""
    if not url or not url.startswith(("http://", "https://")):
        return {
            "name": name, "url": url, "ok": False,
            "status": None, "latency_ms": 0,
            "error": "invalid url",
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }

    t0 = time.time()
    try:
        async with httpx.AsyncClient(
            timeout=_PING_TIMEOUT_S, follow_redirects=True,
        ) as client:
            # HEAD first — cheap. Some sites reject HEAD → fall back to GET.
            try:
                r = await client.head(url, headers={"User-Agent": _UA})
                if r.status_code == 405 or r.status_code >= 500:
                    r = await client.get(url, headers={"User-Agent": _UA})
            except Exception:
                r = await client.get(url, headers={"User-Agent": _UA})
            latency_ms = int((time.time() - t0) * 1000)
            return {
                "name": name, "url": url,
                "ok": r.status_code < 400,
                "status": r.status_code,
                "latency_ms": latency_ms,
                "error": None if r.status_code < 400 else f"HTTP {r.status_code}",
                "checked_at": datetime.now(timezone.utc).isoformat(),
            }
    except Exception as e:
        return {
            "name": name, "url": url, "ok": False,
            "status": None,
            "latency_ms": int((time.time() - t0) * 1000),
            "error": f"{type(e).__name__}: {str(e)[:160]}",
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }


async def _record_ping(ping: dict) -> None:
    """Append ping result to per-source history. Keep last 30."""
    try:
        from . import redis_store as rs
        import json
        key = _K_PING_HISTORY.format(src=ping["name"][:100])
        await rs.lpush(key, json.dumps(ping, default=str)[:2000])
        await rs.ltrim(key, 0, 29)
    except Exception as e:
        logger.debug("[uptime_monitor] record_ping failed: %s", e)


async def _consecutive_failures(source_name: str) -> int:
    """Count consecutive recent ping failures for a source. Walks
    ping_history newest-first, stops at first `ok=True`."""
    try:
        from . import redis_store as rs
        import json
        raw = await rs.lrange(_K_PING_HISTORY.format(src=source_name[:100]), 0, 10)
    except Exception:
        return 0
    count = 0
    for r in raw:
        try:
            p = json.loads(r)
        except Exception:
            continue
        if p.get("ok"):
            break
        count += 1
    return count


async def _get_suspended() -> set[str]:
    try:
        from . import redis_store as rs
        data = await rs.get_json(_K_SUSPENDED) or []
        return set(data) if isinstance(data, list) else set()
    except Exception:
        return set()


async def _set_suspended(names: set[str]) -> None:
    try:
        from . import redis_store as rs
        await rs.set_json(_K_SUSPENDED, sorted(names))
    except Exception as e:
        logger.debug("[uptime_monitor] set_suspended failed: %s", e)


async def suspend(source: str, reason: str = "manual") -> dict:
    """Manually suspend a source."""
    names = await _get_suspended()
    names.add(source)
    await _set_suspended(names)
    try:
        from . import brain_hook as _bh
        await _bh.absorb(
            module="source_uptime_monitor",
            summary=f"Source suspended: {source}",
            detail=f"Reason: {reason}",
            entity_name=source,
            success=False,
            gap_type="source_auto_suspended",
            gap_detail=reason,
            confidence="CONFIRMED",
        )
    except Exception:
        pass
    logger.warning("[uptime_monitor] SUSPENDED %s — %s", source, reason)
    return {"suspended": source, "reason": reason}


async def unsuspend(source: str) -> dict:
    names = await _get_suspended()
    if source in names:
        names.remove(source)
        await _set_suspended(names)
    return {"unsuspended": source}


async def is_suspended(source: str) -> bool:
    return source in await _get_suspended()


# ── Main sweep ─────────────────────────────────────────────────────────────

async def run_daily_ping() -> dict:
    """Ping every registered source. Auto-suspend on combined degradation
    (reliability EMA < 0.3 AND 3+ consecutive ping failures).
    """
    sources = await _get_registered_sources()
    if not sources:
        return {"ok": False, "reason": "no sources registered in web_atlas"}

    # Ping with bounded concurrency
    sem = asyncio.Semaphore(_MAX_CONCURRENT_PINGS)

    async def _guarded_ping(s: dict) -> dict:
        async with sem:
            return await _ping_one(s)

    ping_results = await asyncio.gather(
        *[_guarded_ping(s) for s in sources],
        return_exceptions=False,
    )

    # Record history
    for p in ping_results:
        await _record_ping(p)

    # Compute auto-suspension candidates
    already_suspended = await _get_suspended()
    newly_suspended: list[str] = []
    recovered: list[str] = []
    for src, ping in zip(sources, ping_results):
        name = src.get("name") or ""
        reliability = float(src.get("reliability", 0.5))
        fails = await _consecutive_failures(name)

        should_suspend = (
            reliability < _RELIABILITY_SUSPEND_THRESHOLD
            and fails >= _CONSECUTIVE_PING_FAILS_THRESHOLD
        )
        if should_suspend and name not in already_suspended:
            await suspend(name, reason=(
                f"reliability_ema={reliability:.2f} "
                f"consecutive_ping_fails={fails}"
            ))
            newly_suspended.append(name)
        elif (
            name in already_suspended
            and ping.get("ok")
            and reliability >= _RELIABILITY_SUSPEND_THRESHOLD
        ):
            # Source recovered — both signals clear
            await unsuspend(name)
            recovered.append(name)

    # Persist run summary
    up_count = sum(1 for p in ping_results if p.get("ok"))
    down_count = len(ping_results) - up_count
    summary = {
        "ran_at": datetime.now(timezone.utc).isoformat(),
        "sources_checked": len(ping_results),
        "up": up_count,
        "down": down_count,
        "suspended_now": sorted(newly_suspended),
        "recovered_now": sorted(recovered),
        "currently_suspended": sorted(await _get_suspended()),
    }

    try:
        from . import redis_store as rs
        await rs.set_json(_K_LAST_RUN, summary)
    except Exception:
        pass

    # Feed brain
    try:
        from . import brain_hook as _bh
        await _bh.absorb(
            module="source_uptime_monitor",
            summary=(
                f"Source uptime sweep: {up_count}/{len(ping_results)} up. "
                f"New suspensions: {len(newly_suspended)}. "
                f"Recovered: {len(recovered)}."
            ),
            detail=f"newly_suspended={newly_suspended}; recovered={recovered}",
            success=(down_count == 0 and len(newly_suspended) == 0),
            confidence="CONFIRMED",
        )
    except Exception:
        pass

    logger.info(
        "[uptime_monitor] sweep done: %d/%d up, %d suspended, %d recovered",
        up_count, len(ping_results), len(newly_suspended), len(recovered),
    )
    return summary


async def health() -> dict:
    """Dashboard + capability_card read — last run + suspension state."""
    try:
        from . import redis_store as rs
        last = await rs.get_json(_K_LAST_RUN)
        suspended = sorted(await _get_suspended())
        return {
            "last_run": last,
            "currently_suspended": suspended,
            "suspended_count": len(suspended),
        }
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}
