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

# R-F2266 — a browser-like UA. The old "ARIA-Source-Monitor/1.0 …" UA was
# rejected outright by common WAFs (Cloudflare/Akamai) in front of gov/OEM
# sites, so live Tier-1a sources (fatf-gafi.org, adb.org, …) reported 403/406
# and were falsely marked "down". A realistic UA lets the liveness probe reach
# the origin the same way a real client would.
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36 ARIA-Source-Monitor/1.1"
)

# R-F2266 — HTTP statuses where the server ANSWERED but gated this specific
# client (auth wall / bot challenge / method-not-allowed / rate-limit). The
# source is REACHABLE and live — it is NOT down. Conflating "blocked" with
# "dead" was the root cause of ~38/69 false failures on the uptime panel
# (same honesty lesson as R-F2233's 401-vs-unreachable split). These never
# count toward the auto-suspend gate.
_REACHABLE_BUT_BLOCKED = {401, 403, 405, 406, 429, 451}


def _classify_ping(status: int | None, exc_name: str | None) -> tuple[bool, str]:
    """Honest reachability verdict for one ping.

    Returns (reachable, classification). ``reachable`` is what the panel's
    up/down count and the auto-suspend gate use — a source is only "down"
    when it is genuinely unreachable (transport failure), the seeded URL is
    gone (404/410), or the origin is erroring (5xx). A WAF/auth block means
    the server is alive, so it is reachable=True with classification 'blocked'.
    """
    if status is None:
        return False, (exc_name or "unreachable")
    if 200 <= status < 400:
        return True, "up"
    if status in _REACHABLE_BUT_BLOCKED:
        return True, "blocked"
    if status in (404, 410):
        return False, "not_found"
    if status >= 500:
        return False, "server_error"
    return False, f"http_{status}"

# Thresholds
_RELIABILITY_SUSPEND_THRESHOLD = 0.3   # EMA must be below this
_CONSECUTIVE_PING_FAILS_THRESHOLD = 3  # AND this many consecutive failures
# R-F2216 — the daily sweep pings ~200 curated sources. Pre-fix: 10 concurrent ×
# 10s timeout → worst case ~200s, which BLEW the 122s edge-proxy limit (the manual
# /run endpoint 502'd live) and could exceed the 300s cron budget → the sweep never
# completed, so last_run stayed null ("no sweep has run yet"). Tighter timeout +
# higher concurrency bounds a full sweep to ~30-45s worst case (200/40 × 6s).
# R-F2266 — 6s was too aggressive a CONNECT budget for distant gov/OEM origins
# reached over datacenter egress (TLS handshake to .go.kr/.gov.in/.admin.ch from
# lhr routinely needs >6s), producing false ConnectTimeout "down" verdicts. The
# sweep is now off the request path (R-F2223 backgrounds it) with O(1) state I/O
# (R-F2225), so a 12s ceiling is affordable: 200 sources / 40 concurrent × 12s ≈
# 60s worst case, well inside the cron budget. Split connect vs read so a slow
# TLS handshake gets room without letting a hung read run long.
_PING_TIMEOUT = httpx.Timeout(12.0, connect=10.0)
_MAX_CONCURRENT_PINGS = 40   # R-F2216 — was 10; see timeout note
_SOURCE_UPTIME_STALE_AFTER_S = 30 * 60 * 60  # daily 02:00 UTC task + 6h grace

# Redis keys
_K_LAST_RUN = "crucix:source_uptime:last_run"
_K_SUSPENDED = "crucix:source_uptime:suspended"          # JSON list of source names
_K_PING_HISTORY = "crucix:source_uptime:ping:{src}"      # list, newest first (legacy)
# R-F2225 — single running-state blob {name: {ema, n, fails, last_ok, last_check}}.
# Replaces the per-source _K_PING_HISTORY lists in the hot loop: the sweep now does
# ONE read + ONE write for ALL sources instead of ~400 sequential lpush/lrange ops
# through the saturation-sensitive single-connection state_store (which made a
# 200-source sweep take many minutes and never finish inside the request/cron budget).
_K_SOURCE_STATE = "crucix:source_uptime:state"
_EMA_ALPHA = 0.4


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


async def _ping_one(source: dict, client: httpx.AsyncClient | None = None) -> dict:
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

    # R-F2266 — realistic browser headers so WAFs don't 403 a bare bot ping.
    _headers = {
        "User-Agent": _UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    _light_headers = {
        "User-Agent": _UA,
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
    }
    t0 = time.time()
    try:
        if client is None:
            async with httpx.AsyncClient(  # no-breaker: an uptime monitor MUST ping even down sources to detect downtime — a breaker would defeat its purpose
                timeout=_PING_TIMEOUT, follow_redirects=True,
            ) as owned_client:
                return await _ping_one(source, client=owned_client)

        # HEAD first — cheap. Many sites lie on HEAD (ABR/QinetiQ return HEAD
        # 404 while GET 200). Confirm any non-success HEAD with GET before
        # judging the URL dead.
        try:
            r = await client.head(url, headers=_headers)  # no-ssrf-check: URL is from the curated defence_source_seed catalogue, not user input
            if r.status_code >= 400:
                r = await client.get(url, headers=_headers)  # no-ssrf-check: curated catalogue URL, not user input
        except httpx.ReadTimeout:
            # DFAT hangs on browser-style Accept headers but answers a simple
            # GET quickly. Keep this narrow to ReadTimeout so ConnectTimeout
            # sources like GeM do not get a second long network wait.
            r = await client.get(url, headers=_light_headers)  # no-ssrf-check: curated catalogue URL, not user input
        except Exception:
            r = await client.get(url, headers=_headers)  # no-ssrf-check: curated catalogue URL, not user input
        latency_ms = int((time.time() - t0) * 1000)
        reachable, classification = _classify_ping(r.status_code, None)
        return {
            "name": name, "url": url,
            "ok": reachable,                 # R-F2266 — reachable (incl. WAF-blocked) is UP, not down
            "status": r.status_code,
            "classification": classification,  # up | blocked | not_found | server_error | http_4xx
            "latency_ms": latency_ms,
            "error": None if reachable else (
                "blocked" if classification == "blocked" else f"HTTP {r.status_code}"
            ),
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as e:
        reachable, classification = _classify_ping(None, type(e).__name__)
        return {
            "name": name, "url": url, "ok": reachable,
            "status": None,
            "classification": classification,
            "latency_ms": int((time.time() - t0) * 1000),
            "error": f"{type(e).__name__}: {str(e)[:160]}",
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }


# ── R-F2225 — O(1)-I/O running source state (pure + storage helpers) ──────────
# (Replaced the per-source _K_PING_HISTORY lpush/lrange helpers — _record_ping,
#  _consecutive_failures, _reliability_ema, _source_health — whose ~400 sequential
#  state_store ops per sweep made a 200-source sweep take minutes and never finish.)
def _update_source_state(prev: dict | None, ok: bool, checked_at: str) -> dict:
    """Fold one ping into a source's running state (in memory). Pure/testable.
    ema: running reliability EMA (recent pings weighted more); n: sample count;
    fails: consecutive failures (reset on ok)."""
    prev = prev or {}
    prev_ema = float(prev.get("ema", 0.5))
    n = int(prev.get("n", 0))
    v = 1.0 if ok else 0.0
    ema = v if n == 0 else round(_EMA_ALPHA * v + (1.0 - _EMA_ALPHA) * prev_ema, 3)
    fails = 0 if ok else int(prev.get("fails", 0)) + 1
    return {"ema": ema, "n": n + 1, "fails": fails, "last_ok": bool(ok), "last_check": checked_at}


def _suspend_reliability(st: dict) -> float:
    """The reliability the auto-suspend gate uses: the real EMA once there are ≥3
    samples, else a NEUTRAL 0.5 so a barely-seen source is never suspended on thin
    data (the gate's consecutive_fails>=3 also guarantees ≥3 samples)."""
    return float(st.get("ema", 0.5)) if int(st.get("n", 0)) >= 3 else 0.5


async def _get_source_state() -> dict:
    try:
        from . import redis_store as rs
        data = await rs.get_json(_K_SOURCE_STATE) or {}
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


async def _set_source_state(state: dict) -> None:
    try:
        from . import redis_store as rs
        await rs.set_json(_K_SOURCE_STATE, state)
    except Exception as e:
        logger.debug("[uptime_monitor] set_source_state failed: %s", e)


async def _get_suspended() -> set[str]:
    try:
        from . import redis_store as rs
        data = await rs.get_json(_K_SUSPENDED) or []
        return set(data) if isinstance(data, list) else set()
    except Exception:
        return set()


async def _set_suspended(names: set[str]) -> bool:
    try:
        from . import redis_store as rs
        await rs.set_json(_K_SUSPENDED, sorted(names))
        return True
    except Exception as e:
        logger.debug("[uptime_monitor] set_suspended failed: %s", e)
        return False


async def suspend(source: str, reason: str = "manual") -> dict:
    """Manually suspend a source."""
    names = await _get_suspended()
    names.add(source)
    persisted = await _set_suspended(names)
    if persisted is False:
        return {"ok": False, "error": "failed to persist suspended source", "source": source}
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
    return {"ok": True, "suspended": source, "reason": reason}


async def unsuspend(source: str) -> dict:
    names = await _get_suspended()
    if source not in names:
        return {"ok": True, "unsuspended": source, "changed": False}
    names.remove(source)
    persisted = await _set_suspended(names)
    if persisted is False:
        return {"ok": False, "error": "failed to persist unsuspended source", "source": source}
    if source in await _get_suspended():
        return {"ok": False, "error": "unsuspend verification failed", "source": source}
    return {"ok": True, "unsuspended": source, "changed": True}


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

    async def _guarded_ping(client: httpx.AsyncClient, s: dict) -> dict:
        async with sem:
            return await _ping_one(s, client=client)

    async with httpx.AsyncClient(  # no-breaker: uptime monitor must test live source reachability
        timeout=_PING_TIMEOUT, follow_redirects=True,
    ) as client:
        ping_results = await asyncio.gather(
            *[_guarded_ping(client, s) for s in sources],
            return_exceptions=False,
        )

    # R-F2225 — fold every ping into ONE running-state blob (read once here, written
    # once below) instead of ~400 sequential per-source lpush/lrange ops. reliability
    # is a real running EMA (was hardcoded 0.5, which made the <0.3 auto-suspend gate
    # dead); neutral 0.5 on thin history (<3 samples) so nothing suspends on thin data.
    state = await _get_source_state()
    already_suspended = await _get_suspended()
    newly_suspended: list[str] = []
    recovered: list[str] = []
    for src, ping in zip(sources, ping_results):
        name = src.get("name") or ""
        st = _update_source_state(state.get(name), bool(ping.get("ok")), ping.get("checked_at", ""))
        state[name] = st
        reliability = _suspend_reliability(st)
        fails = st["fails"]

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

    # R-F2225 — ONE write persists the updated running state for ALL sources.
    await _set_source_state(state)

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
        cls = p.get("classification") or ("up" if p.get("ok") else "error")
        per_source.append({
            "name": nm,
            "url": p.get("url"),
            "status": "ok" if p.get("ok") else "error",
            "classification": cls,   # R-F2266 — up | blocked | not_found | server_error | http_4xx | <exc>
            "http_status": p.get("status"),
            "latency_ms": p.get("latency_ms"),
            "last_success": p.get("checked_at") if p.get("ok") else None,
            "last_error": p.get("error"),
            "checked_at": p.get("checked_at"),
            "suspended": nm in suspended_set,
        })
    per_source.sort(key=lambda s: (s["status"] == "ok", s["name"]))  # down first
    # R-F2266 — 'blocked' means reachable-but-WAF-gated (counted UP); surface it
    # separately so the panel/brain can tell "live but gated" from "truly up".
    blocked_count = sum(1 for s in per_source if s["classification"] == "blocked")
    summary = {
        "ran_at": datetime.now(timezone.utc).isoformat(),
        "sources_checked": len(ping_results),
        "up": up_count,
        "down": down_count,
        "blocked": blocked_count,
        "suspended_now": sorted(newly_suspended),
        "recovered_now": sorted(recovered),
        "currently_suspended": sorted(suspended_set),
        "sources": per_source,
    }

    summary["persisted"] = True
    try:
        from . import redis_store as rs
        await rs.set_json(_K_LAST_RUN, summary)
    except Exception as e:
        summary["persisted"] = False
        summary["persist_error"] = f"{type(e).__name__}: {str(e)[:160]}"
        logger.warning("[uptime_monitor] failed to persist last_run: %s", summary["persist_error"])
        try:
            from .engine_wiring import wire_failure
            wire_failure(
                module="source_uptime_monitor",
                detail=f"last_run persistence failed: {summary['persist_error']}",
                gap_type="source_uptime_degraded",
                source="source_uptime_monitor:R-F2659",
            )
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
        freshness = _last_run_freshness(last)
        # R-F2022 — expose the per-source array at top level so the sources-page
        # panel (reads data.sources) renders the real up/down rows, not just the
        # aggregate counts it had no way to display before.
        return {
            "last_run": last or {"ran_at": None, "sources_checked": 0, "up": 0, "down": 0, "message": "No uptime sweep has run yet. Trigger via POST /api/aria/sources/uptime/run"},
            "sources": last.get("sources", []),
            "currently_suspended": suspended,
            "suspended_count": len(suspended),
            "freshness": freshness,
            "last_run_age_seconds": freshness["age_seconds"],
            "stale_after_seconds": freshness["stale_after_seconds"],
            "stale": freshness["stale"],
        }
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


def _last_run_freshness(last: dict | None, now_ts: float | None = None) -> dict:
    """Return freshness metadata for the cached source-uptime sweep."""
    now_ts = time.time() if now_ts is None else now_ts
    ran_at = (last or {}).get("ran_at") if isinstance(last, dict) else None
    if not ran_at:
        return {
            "stale": True,
            "age_seconds": None,
            "stale_after_seconds": _SOURCE_UPTIME_STALE_AFTER_S,
            "reason": "missing_last_run",
        }
    try:
        dt = datetime.fromisoformat(str(ran_at).replace("Z", "+00:00"))
        age_s = max(0, int(now_ts - dt.timestamp()))
    except Exception:
        return {
            "stale": True,
            "age_seconds": None,
            "stale_after_seconds": _SOURCE_UPTIME_STALE_AFTER_S,
            "reason": "invalid_last_run_timestamp",
        }
    return {
        "stale": age_s > _SOURCE_UPTIME_STALE_AFTER_S,
        "age_seconds": age_s,
        "stale_after_seconds": _SOURCE_UPTIME_STALE_AFTER_S,
        "reason": "stale_last_run" if age_s > _SOURCE_UPTIME_STALE_AFTER_S else "fresh",
    }
