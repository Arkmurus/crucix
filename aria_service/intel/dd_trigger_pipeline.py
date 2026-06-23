"""R-F1140 — Automated DD trigger pipeline.

Monitors intelligence signals (new sanctions, adverse media, procurement awards,
conflict events) and automatically triggers a fresh DD run when a signal matches
a watched entity. Also identifies relevant websites/portals from the DD context
and attempts registration for data access.

Architecture:
1. SIGNAL MONITOR — watches intel_ledger, sanctions lists, news feeds for
   new signals matching watched entities
2. DD TRIGGER — when a match is found, calls dd_orchestrator.orchestrate_dd()
   with the entity and context
3. PORTAL SCOUT — extracts URLs from DD research context and checks if ARIA
   is registered; if not, attempts registration
4. DELIVERY — sends the DD report to the operator via WhatsApp/email

Usage:
    from aria_service.intel.dd_trigger_pipeline import (
        monitor_and_trigger,
        trigger_dd_for_entity,
        scout_portals_for_dd,
    )

    # Run the monitor (every 5 min):
    result = await monitor_and_trigger()

    # Manual trigger:
    result = await trigger_dd_for_entity("Acme Corp", "New sanctions hit")
"""
from __future__ import annotations

import hashlib
import logging
import time
from datetime import datetime, timezone
from typing import Any, Optional
from .wire import fail_wire  # R-F1789 §21 brain-wiring

logger = logging.getLogger("aria.dd_trigger_pipeline")

# Redis keys
_WATCHED_ENTITIES_KEY = "crucix:dd:watchlist"
_TRIGGER_LOG_KEY = "crucix:dd:trigger_log"
_DD_CACHE_KEY = "crucix:dd:recent_runs"

# Min interval between DD runs for the same entity (prevent spam)
MIN_RE_DD_INTERVAL_S = 86400 * 7  # 7 days

# Signal sources to monitor
MONITORED_SOURCES = [
    "sanctions",           # New sanctions designations
    "adverse_media",       # Negative news
    "procurement",         # New contract awards
    "conflict_events",     # Conflict zone changes
    "financial_crime",     # FCA/OFSI enforcement
    "corporate_change",    # Director/residency changes
]


# ── Entity watchlist ────────────────────────────────────────────────────────

async def _get_watchlist() -> list[dict[str, Any]]:
    """Get the watched entities list from Redis."""
    try:
        from . import redis_store as rs
        return await rs.get_json(_WATCHED_ENTITIES_KEY) or []
    except Exception:
        return []


async def _add_to_watchlist(
    entity_name: str,
    jurisdiction: str = "",
    reason: str = "Manual",
    source_url: str = "",
) -> dict[str, Any]:
    """Add an entity to the watchlist for automated DD triggering."""
    entry = {
        "entity_name": entity_name,
        "jurisdiction": jurisdiction,
        "reason": reason,
        "source_url": source_url,
        "added_at": datetime.now(timezone.utc).isoformat(),
        "last_dd_at": None,
        "dd_count": 0,
    }
    watchlist = await _get_watchlist()
    # Deduplicate by name
    watchlist = [e for e in watchlist if e.get("entity_name", "").lower() != entity_name.lower()]
    watchlist.append(entry)
    try:
        from . import redis_store as rs
        await rs.set_json(_WATCHED_ENTITIES_KEY, watchlist)
    except Exception:
        pass
    return entry


# ── Signal monitoring ───────────────────────────────────────────────────────

async def _check_for_new_signals(
    since_ts: float,
) -> list[dict[str, Any]]:
    """Check intel_ledger and other sources for new signals since timestamp.

    Returns list of signal dicts with keys: entity_name, source, summary, url.
    """
    signals = []

    # Check intel_ledger for recent entries
    try:
        from . import intel_ledger as _il
        entries = await _il.get_recent(limit=200)
        for entry in entries or []:
            ts = entry.get("timestamp") or entry.get("generated_at", "")
            if isinstance(ts, str):
                try:
                    from datetime import datetime
                    entry_ts = datetime.fromisoformat(ts).timestamp()
                except Exception:
                    continue
            else:
                entry_ts = float(ts) if ts else 0

            if entry_ts < since_ts:
                continue

            source = (entry.get("source") or "").lower()
            if not any(s in source for s in MONITORED_SOURCES):
                continue

            signals.append({
                "entity_name": entry.get("entity_name", entry.get("summary", ""))[:100],
                "source": source,
                "summary": (entry.get("summary") or "")[:200],
                "url": entry.get("source_url", entry.get("url", "")),
                "timestamp": entry.get("timestamp", ""),
                "signal_type": "intel_ledger",
            })
    except Exception as e:
        logger.debug("[dd_trigger] intel_ledger check failed: %s", e)

    return signals


async def _match_signals_to_watchlist(
    signals: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Match incoming signals against the watchlist.

    Returns list of (signal, watch_entry) tuples where a match was found.
    """
    watchlist = await _get_watchlist()
    if not watchlist:
        return []

    matches = []
    for signal in signals:
        entity_name = signal.get("entity_name", "").lower()
        for entry in watchlist:
            watched = entry.get("entity_name", "").lower()
            # Simple substring match — can be improved with fuzzy matching
            if watched and (watched in entity_name or entity_name in watched):
                matches.append({
                    "signal": signal,
                    "watch_entry": entry,
                    "match_type": "name_substring",
                })
                break

    return matches


# ── DD triggering ───────────────────────────────────────────────────────────

@fail_wire(module="dd_trigger_pipeline", gap_type="engine_failure")
async def trigger_dd_for_entity(
    entity_name: str,
    reason: str,
    jurisdiction: str = "",
    source_url: str = "",
) -> dict[str, Any]:
    """Trigger a DD run for an entity.

    Checks the DD cache to avoid re-running the same entity too frequently.
    Calls dd_orchestrator.orchestrate_dd() and logs the result.

    Args:
        entity_name: Name of the entity to run DD on.
        reason: Why the DD was triggered.
        jurisdiction: ISO-2 jurisdiction code (optional).
        source_url: Source URL that triggered this (optional).

    Returns:
        Dict with trigger result.
    """
    # Check cache to prevent spam
    try:
        from . import redis_store as rs
        cache = await rs.get_json(_DD_CACHE_KEY) or {}
        last_run = cache.get(entity_name.lower())
        if last_run:
            elapsed = time.time() - last_run
            if elapsed < MIN_RE_DD_INTERVAL_S:
                remaining_h = (MIN_RE_DD_INTERVAL_S - elapsed) / 3600
                return {
                    "triggered": False,
                    "reason": f"Last DD was {elapsed/3600:.1f}h ago "
                              f"(min interval {MIN_RE_DD_INTERVAL_S/3600:.0f}h, "
                              f"{remaining_h:.1f}h remaining)",
                    "entity": entity_name,
                }
    except Exception:
        pass

    # Run DD
    try:
        from . import dd_orchestrator as _dd
        target = {"name": entity_name}
        if jurisdiction:
            target["jurisdiction_iso2"] = jurisdiction

        report = await _dd.orchestrate_dd(
            target=target,
            mode="quick",  # Quick mode for automated triggers
            trace_id=f"auto_dd_{hashlib.md5(entity_name.encode()).hexdigest()[:8]}",
        )

        # Update cache
        try:
            from . import redis_store as rs
            cache = await rs.get_json(_DD_CACHE_KEY) or {}
            cache[entity_name.lower()] = time.time()
            await rs.set_json(_DD_CACHE_KEY, cache, ex=86400 * 30)
        except Exception:
            pass

        # Log trigger
        await _log_trigger(entity_name, reason, report.run_id)

        # Scout portals mentioned in the DD
        await scout_portals_for_dd(target, report)

        return {
            "triggered": True,
            "run_id": report.run_id,
            "entity": entity_name,
            "risk": report.risk_classification,
            "duration_ms": report.total_duration_ms,
        }

    except Exception as e:
        logger.error("[dd_trigger] DD failed for %s: %s", entity_name, e)
        return {
            "triggered": False,
            "reason": str(e)[:200],
            "entity": entity_name,
        }


async def _log_trigger(
    entity_name: str,
    reason: str,
    run_id: str,
) -> None:
    """Log a DD trigger event."""
    try:
        from . import redis_store as rs
        log = await rs.get_json(_TRIGGER_LOG_KEY) or []
        log.insert(0, {
            "entity": entity_name,
            "reason": reason,
            "run_id": run_id,
            "triggered_at": datetime.now(timezone.utc).isoformat(),
        })
        await rs.set_json(_TRIGGER_LOG_KEY, log[:500], ex=86400 * 90)
    except Exception:
        pass


# ── Portal scouting ─────────────────────────────────────────────────────────

@fail_wire(module="dd_trigger_pipeline", gap_type="engine_failure")
async def scout_portals_for_dd(
    target: dict,
    report: Any,
) -> list[dict[str, Any]]:
    """Extract URLs from DD research context and check registration status.

    When a DD is run, the research process touches many websites (tender portals,
    company registries, news sites). This function extracts those URLs and checks
    if ARIA is registered. For unregistered portals that don't require CAPTCHA,
    it attempts registration.

    Args:
        target: The DD target dict.
        report: The ARKDDReport from the DD run.

    Returns:
        List of portal scouting results.
    """
    results = []

    # Collect URLs from the report's digital section
    urls_to_check: set[str] = set()
    try:
        digital = getattr(report, "digital", None)
        if digital:
            for finding in getattr(digital, "findings", []) or []:
                url = getattr(finding, "source_url", "") or getattr(finding, "url", "")
                if url:
                    urls_to_check.add(url)
    except Exception:
        pass

    if not urls_to_check:
        return results

    # Check each URL against the portal registry
    try:
        from . import portal_registry as _pr

        for url in urls_to_check:
            # Find matching portal by URL
            portal = None
            for p in _pr.PORTALS:
                if p.url in url or url.startswith(p.url):
                    portal = p
                    break

            if not portal:
                continue

            # Check if already registered
            is_registered = await _pr.is_registered(portal.id)
            if is_registered:
                results.append({
                    "portal_id": portal.id,
                    "name": portal.name,
                    "status": "already_registered",
                })
                continue

            # Attempt registration if no CAPTCHA
            if not portal.requires_captcha:
                try:
                    reg_result = await _pr.register_for_portal(portal.id)
                    results.append({
                        "portal_id": portal.id,
                        "name": portal.name,
                        "status": "registered" if reg_result.get("success") else "failed",
                        "message": reg_result.get("message", reg_result.get("error", "")),
                    })
                except Exception as e:
                    results.append({
                        "portal_id": portal.id,
                        "name": portal.name,
                        "status": "error",
                        "message": str(e)[:100],
                    })
            else:
                results.append({
                    "portal_id": portal.id,
                    "name": portal.name,
                    "status": "requires_captcha",
                })

    except Exception as e:
        logger.debug("[dd_trigger] Portal scouting failed: %s", e)

    return results


# ── Main monitor ────────────────────────────────────────────────────────────

@fail_wire(module="dd_trigger_pipeline", gap_type="engine_failure")
async def monitor_and_trigger() -> dict[str, Any]:
    """Main monitor loop — check for new signals and trigger DD.

    Called by autonomous_scheduler every 5 minutes.

    Returns:
        Dict with monitoring results.
    """
    t0 = time.time()
    now = time.time()

    # Check for new signals since last check (stored in Redis)
    try:
        from . import redis_store as rs
        last_check = await rs.get_json("crucix:dd:last_signal_check") or (now - 300)
    except Exception:
        last_check = now - 300

    signals = await _check_for_new_signals(last_check)
    matches = await _match_signals_to_watchlist(signals)

    triggers = []
    for match in matches:
        signal = match["signal"]
        entry = match["watch_entry"]
        result = await trigger_dd_for_entity(
            entity_name=signal.get("entity_name", entry.get("entity_name", "")),
            reason=f"Signal match: {signal.get('summary', '')[:100]}",
            jurisdiction=entry.get("jurisdiction", ""),
            source_url=signal.get("url", ""),
        )
        triggers.append(result)

    # Update last check timestamp
    try:
        from . import redis_store as rs
        await rs.set_json("crucix:dd:last_signal_check", now)
    except Exception:
        pass

    result = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "signals_found": len(signals),
        "matches_found": len(matches),
        "triggers_fired": sum(1 for t in triggers if t.get("triggered")),
        "triggers_skipped": sum(1 for t in triggers if not t.get("triggered")),
        "triggers": triggers,
        "duration_ms": int((time.time() - t0) * 1000),
    }

    # Wire to brain
    try:
        from .engine_wiring import wire_success
        wire_success(
            module="dd_trigger_pipeline",
            summary=(
                f"DD monitor: {result['signals_found']} signals, "
                f"{result['triggers_fired']} DD triggered"
            ),
            detail=(
                f"Signals: {result['signals_found']}, "
                f"Matches: {result['matches_found']}, "
                f"Triggered: {result['triggers_fired']}, "
                f"Skipped: {result['triggers_skipped']}"
            ),
            confidence="CONFIRMED",
            source_id="dd_trigger_pipeline:R-F1140",
        )
    except Exception:
        logger.debug("[dd_trigger] brain wiring failed", exc_info=True)

    return result


@fail_wire(module="dd_trigger_pipeline", gap_type="engine_failure")
async def get_trigger_log(limit: int = 20) -> list[dict[str, Any]]:
    """Get recent DD trigger log entries."""
    try:
        from . import redis_store as rs
        log = await rs.get_json(_TRIGGER_LOG_KEY) or []
        return log[:limit]
    except Exception:
        return []
