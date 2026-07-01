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
# R-F2216 — the daily sweep pings ~200 curated sources. Pre-fix: 10 concurrent ×
# 10s timeout → worst case ~200s, which BLEW the 122s edge-proxy limit (the manual
# /run endpoint 502'd live) and could exceed the 300s cron budget → the sweep never
# completed, so last_run stayed null ("no sweep has run yet"). Tighter timeout +
# higher concurrency bounds a full sweep to ~30-45s worst case (200/40 × 6s).
_PING_TIMEOUT_S = 6.0
_MAX_CONCURRENT_PINGS = 40   # R-F2216 — was 10; see _PING_TIMEOUT_S note

# Redis keys
_K_LAST_RUN = "crucix:source_uptime:last_run"
_K_SUSPENDED = "crucix:source_uptime:suspended"          # JSON list of source names
_K_PING_HISTORY = "crucix:source_uptime:ping:{src}"      # list, newest first


async def _get_registered_sources() -> list[dict]:
    """R-F2022 — enumerate the REAL seeded defence-source catalogue.

    Root-kill: the old code getattr-probed web_atlas for list_sources /
    get_all_sources / sources_with_scores — NONE of which exist (§3b: never call
    an unverified function) — so it ALWAYS returned [], run_daily_ping no-op'd
    ("no sources registered"), and the sources-page uptime panel was permanently
    empty despite ~200 real sources being seeded. The authoritative registry of
    what IS a source is defence_source_seed._DEFENCE_SOURCES (the tier-tagged URLs
    actually seeded into web_atlas), so we read it directly.

    Each entry: {"name", "url", "reliability", "tier"}. reliability defaults to a
    neutral 0.5 — ≥ the auto-suspend threshold, so a source is only ever
    auto-suspended on REAL consecutive ping failures combined with a low score,
    never spuriously. (Per-source reliability-EMA enrichment is a follow-up; the
    live ping ok/error is the real uptime signal the panel needs today.)
    """
    try:
        from . import defence_source_seed as _seed
        raw = list(getattr(_seed, "_DEFENCE_SOURCES", []) or [])
    except Exception as e:
        logger.warning("[uptime_monitor] could not read defence_source_seed catalogue: %s", e)
        return []

    sources: list[dict] = []
    for entry in raw:
        try:
            url = entry[0]
            name = entry[1]
            tier = entry[2] if len(entry) > 2 else None
        except Exception:
            continue
        if not url or not name:
            continue
        sources.append({"name": name, "url": url, "reliability": 0.5, "tier": tier})
    return sources


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
        async with httpx.AsyncClient(  # no-breaker: an uptime monitor MUST ping even down sources to detect downtime — a breaker would defeat its purpose
            timeout=_PING_TIMEOUT_S, follow_redirects=True,
        ) as client:
            # HEAD first — cheap. Some sites reject HEAD → fall back to GET.
            try:
                r = await client.head(url, headers={"User-Agent": _UA})  # no-ssrf-check: URL is from the curated defence_source_seed catalogue, not user input
                if r.status_code == 405 or r.status_code >= 500:
                    r = await client.get(url, headers={"User-Agent": _UA})  # no-ssrf-check: curated catalogue URL, not user input
            except Exception:
                r = await client.get(url, headers={"User-Agent": _UA})  # no-ssrf-check: curated catalogue URL, not user input
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


async def _reliability_ema(source_name: str) -> float:
    """R-F2216 — the real reliability signal, computed from the ping history this
    module already records (last 30, newest-first). Pre-fix `reliability` was
    HARDCODED to 0.5 in _get_registered_sources, and the auto-suspend gate needs
    `reliability < 0.3` — so 0.5 was never below 0.3 and auto-suspend could NEVER
    fire (dead sources rotted, only manual suspend worked). We now derive an EMA
    over the ok/fail history (recent pings weighted more). Returns a NEUTRAL 0.5
    when there is too little history (<3 pings) so a source is never suspended on
    thin data — the gate's `consecutive_fails >= 3` already guarantees ≥3 pings.
    """
    try:
        from . import redis_store as rs
        import json
        raw = await rs.lrange(_K_PING_HISTORY.format(src=source_name[:100]), 0, 30)
    except Exception:
        return 0.5
    pings = []
    for r in raw:
        try:
            pings.append(bool(json.loads(r).get("ok")))
        except Exception:
            continue
    if len(pings) < 3:
        return 0.5
    # raw is newest-first; fold oldest→newest so recent pings dominate the EMA.
    alpha = 0.4
    ema: float | None = None
    for ok in reversed(pings):
        v = 1.0 if ok else 0.0
        ema = v if ema is None else (alpha * v + (1.0 - alpha) * ema)
    return round(ema if ema is not None else 0.5, 3)


async def _source_health(source_name: str) -> tuple[int, float]:
    """R-F2223 — read a source's ping history ONCE and derive BOTH the consecutive
    failure count AND the reliability EMA. Pre-fix the sweep called
    _consecutive_failures + _reliability_ema separately (2 lrange reads/source →
    ~400 sequential state_store ops over 200 sources, on top of the 200 record
    writes) — that serialized the sweep past the edge/cron window so it never
    completed (last_run stayed null). One read halves the per-source read I/O.
    """
    import json
    try:
        from . import redis_store as rs
        raw = await rs.lrange(_K_PING_HISTORY.format(src=source_name[:100]), 0, 30)
    except Exception:
        return 0, 0.5
    oks: list[bool] = []
    for r in raw:
        try:
            oks.append(bool(json.loads(r).get("ok")))
        except Exception:
            continue
    # consecutive failures (history is newest-first; stop at first ok)
    fails = 0
    for ok in oks:
        if ok:
            break
        fails += 1
    # reliability EMA (neutral 0.5 on thin history — never suspend on thin data)
    if len(oks) < 3:
        return fails, 0.5
    alpha = 0.4
    ema: float | None = None
    for ok in reversed(oks):
        v = 1.0 if ok else 0.0
        ema = v if ema is None else (alpha * v + (1.0 - alpha) * ema)
    return fails, round(ema if ema is not None else 0.5, 3)


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
        # §21a — the failure branch must reach the brain, not just return quietly.
        from .engine_wiring import wire_failure
        wire_failure(
            module="source_uptime_monitor",
            detail="run_daily_ping: defence_source_seed catalogue empty — no sources to ping",
            gap_type="source_uptime_no_sources",
            source="source_uptime_monitor:R-F2022",
        )
        return {"ok": False, "reason": "no defence sources registered (defence_source_seed catalogue empty)"}

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
        # R-F2216/R-F2223 — derive BOTH signals from ONE history read (including the
        # ping just recorded above). reliability is a real EMA (was hardcoded 0.5,
        # which made the <0.3 auto-suspend gate dead); neutral 0.5 on thin history.
        fails, reliability = await _source_health(name)

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
    # R-F2022 — persist the REAL per-source results so health()/the sources-page
    # panel can render the actual up/down state. The old summary stored only
    # aggregate counts, so even a successful sweep gave the panel no rows to show.
    suspended_set = await _get_suspended()
    per_source = []
    for p in ping_results:
        nm = p.get("name") or ""
        per_source.append({
            "name": nm,
            "url": p.get("url"),
            "status": "ok" if p.get("ok") else "error",
            "http_status": p.get("status"),
            "latency_ms": p.get("latency_ms"),
            "last_success": p.get("checked_at") if p.get("ok") else None,
            "last_error": p.get("error"),
            "checked_at": p.get("checked_at"),
            "suspended": nm in suspended_set,
        })
    per_source.sort(key=lambda s: (s["status"] == "ok", s["name"]))  # down first
    summary = {
        "ran_at": datetime.now(timezone.utc).isoformat(),
        "sources_checked": len(ping_results),
        "up": up_count,
        "down": down_count,
        "suspended_now": sorted(newly_suspended),
        "recovered_now": sorted(recovered),
        "currently_suspended": sorted(suspended_set),
        "sources": per_source,
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
    # R-F996 — wire to brain
    from .engine_wiring import wire_success
    wire_success(
        module="source_uptime_monitor",
        summary="Run Daily Ping",
        source_id="source_uptime_monitor:R-F996",
    )
    # R-F2022/§21a — wire the FAILURE branch too: sources that went down are a
    # real degradation signal the brain/coder should see (not just logged).
    if down_count or newly_suspended:
        from .engine_wiring import wire_failure
        wire_failure(
            module="source_uptime_monitor",
            detail=(f"uptime sweep: {down_count}/{len(ping_results)} source(s) down, "
                    f"{len(newly_suspended)} newly suspended"),
            gap_type="source_uptime_degraded",
            source="source_uptime_monitor:R-F2022",
        )

    return summary


async def health() -> dict:
    """Dashboard + capability_card read — last run + suspension state."""
    try:
        from . import redis_store as rs
        last = await rs.get_json(_K_LAST_RUN) or {}
        suspended = sorted(await _get_suspended())
        # R-F2022 — expose the per-source array at top level so the sources-page
        # panel (reads data.sources) renders the real up/down rows, not just the
        # aggregate counts it had no way to display before.
        return {
            "last_run": last or {"ran_at": None, "sources_checked": 0, "up": 0, "down": 0, "message": "No uptime sweep has run yet. Trigger via POST /api/aria/sources/uptime/run"},
            "sources": last.get("sources", []),
            "currently_suspended": suspended,
            "suspended_count": len(suspended),
        }
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}
