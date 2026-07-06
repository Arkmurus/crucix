"""
R-F2066 — Autonomous Portal Registration Scheduler.

Continuous, self-improving registration loop that works through all 43
portals, learns from every attempt, and escalates only when truly stuck.

Architecture:
  PortalScheduler — manages the queue, priorities, cooldowns, retries
  autonomous_registration_loop — background task that runs continuously

The scheduler:
  - Loads all portals from portal_registry.PORTALS
  - Checks knowledge base for past attempts
  - Calculates priority based on tier + failure count
  - Applies exponential backoff after repeated failures
  - Generates email aliases for each portal
  - Stores obtained API keys in the credentials vault
  - Wires success/failure to the brain
  - Runs every N hours (configurable)

Usage:
    from aria_service.intel.portal_scheduler import PortalScheduler

    scheduler = PortalScheduler()
    results = await scheduler.run_once()
    # {"attempted": 5, "successful": 2, "failed": 1, "deferred": 2, ...}
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import secrets
import time
from datetime import datetime, timezone
from typing import Any, Optional

from .portal_knowledge import RegistrationKnowledge
from .portal_agent import AdaptivePortalAgent

logger = logging.getLogger("aria.portal_scheduler")

# How long to wait between registration attempts (avoid rate limiting)
_ATTEMPT_DELAY_S = (3, 8)  # random seconds

# How long to wait between full scheduler runs
_RUN_INTERVAL_S = 3600  # 1 hour

# Cooldown after N failures: base * 2^failures, capped at this
_MAX_COOLDOWN_S = 86400  # 24 hours

# Portals that need no registration (open APIs)
_OPEN_API_IDS = {
    "usaspending", "companies_house", "sec_edgar", "openalex",
    "gdelt", "world_bank_api", "ofac_sdn_download", "eu_sanctions_map",
    "uk_ofsi", "un_sc_sanctions", "bis_entity_list",
    "usaspending_profile", "uk_companies_house",
}

# Portals that are deferred (need operator action)
_DEFERRED_IDS = {
    "pitchbook", "crunchbase", "duns_bradstreet", "acled",
}


class PortalScheduler:
    """Manages the autonomous portal registration loop."""

    def __init__(self):
        self._knowledge = RegistrationKnowledge()
        self._stats: dict[str, Any] = {
            "total_runs": 0,
            "total_attempts": 0,
            "total_successes": 0,
            "total_failures": 0,
            "total_deferred": 0,
            "last_run_at": None,
            "last_run_duration_s": 0,
        }

    async def run_once(self) -> dict[str, Any]:
        """Run one full cycle — try all pending portals.

        Returns:
            Summary dict with counts of attempted, successful, failed, deferred.
        """
        from .portal_registry import PORTALS

        results: dict[str, Any] = {
            "attempted": 0,
            "successful": 0,
            "failed": 0,
            "deferred": 0,
            "skipped_open": 0,
            "details": [],
            "started_at": datetime.now(timezone.utc).isoformat(),
        }
        t0 = time.time()

        # Build priority queue
        queue = []
        for portal in PORTALS:
            # Skip open APIs (no registration needed)
            if portal.id in _OPEN_API_IDS:
                results["skipped_open"] += 1
                continue

            # Skip deferred portals
            if portal.id in _DEFERRED_IDS:
                results["deferred"] += 1
                continue

            # Skip portals with registration_type="none" (already open)
            if portal.registration_type == "none":
                results["skipped_open"] += 1
                continue

            # Check knowledge base for past attempts
            site_info = self._knowledge.get_site(portal.id)
            priority = self._calculate_priority(portal, site_info)

            # Check cooldown
            if site_info and site_info.get("fail_count", 0) > 0:
                cooldown = self._get_cooldown(site_info)
                if cooldown > 0:
                    logger.info("[scheduler] %s in cooldown (%.0fs remaining)", portal.id, cooldown)
                    results["deferred"] += 1
                    continue

            queue.append((priority, portal))

        # Sort by priority (highest first)
        queue.sort(key=lambda x: -x[0])
        logger.info("[scheduler] Processing %d portals (priority order)", len(queue))

        # Process each portal
        for priority, portal in queue:
            logger.info("[scheduler] Registering %s (priority=%d, tier=%s)",
                       portal.id, priority, portal.registration_type)

            attempt_result = await self._register_single(portal)
            results["attempted"] += 1

            if attempt_result.get("success"):
                results["successful"] += 1
                results["details"].append({
                    "id": portal.id,
                    "status": "success",
                    "message": attempt_result.get("message", "")[:200],
                    "api_key": bool(attempt_result.get("api_key")),
                })
                logger.info("[scheduler] ✅ %s: registered successfully", portal.id)
            else:
                results["failed"] += 1
                results["details"].append({
                    "id": portal.id,
                    "status": "failed",
                    "message": attempt_result.get("message", "")[:200],
                    "error": attempt_result.get("error", ""),
                })
                logger.info("[scheduler] ❌ %s: %s", portal.id,
                           attempt_result.get("message", "unknown error")[:100])

            # Brief pause between attempts
            await asyncio.sleep(random.uniform(*_ATTEMPT_DELAY_S))  # nosec B311

        # Update stats
        self._stats["total_runs"] += 1
        self._stats["total_attempts"] += results["attempted"]
        self._stats["total_successes"] += results["successful"]
        self._stats["total_failures"] += results["failed"]
        self._stats["total_deferred"] += results["deferred"]
        self._stats["last_run_at"] = datetime.now(timezone.utc).isoformat()
        self._stats["last_run_duration_s"] = time.time() - t0

        results["duration_s"] = time.time() - t0
        results["finished_at"] = datetime.now(timezone.utc).isoformat()

        # Wire to brain
        try:
            from .engine_wiring import wire_success, wire_failure
            if results["successful"] > 0:
                wire_success(
                    module="portal_scheduler",
                    summary=f"Registration cycle: {results['successful']} new, "
                            f"{results['failed']} failed, {results['deferred']} deferred",
                    source_id="portal_scheduler:R-F2066",
                )
            elif results["failed"] > 0:
                wire_failure(
                    module="portal_scheduler",
                    detail=f"Registration cycle: 0 new, "
                           f"{results['failed']} failed, {results['deferred']} deferred",
                    gap_type="source_failure",
                    source="portal_scheduler:R-F2066",
                )
        except Exception:
            pass

        return results

    async def _register_single(self, portal) -> dict[str, Any]:
        """Register for a single portal using the adaptive agent."""
        # Generate email alias
        local, at, domain = "aria", "@", "arkmurus.com"
        base_email = os.getenv("ARIA_PORTAL_EMAIL", "aria@arkmurus.com")
        if "@" in base_email:
            local, domain = base_email.split("@", 1)
        alias_email = f"{local}+{portal.id}@{domain}"

        try:
            async with AdaptivePortalAgent() as agent:
                result = await agent.register(portal.id)
                return result
        except Exception as e:
            logger.error("[scheduler] Exception registering %s: %s", portal.id, e)
            return {"success": False, "message": str(e), "error": str(e)}

    def _calculate_priority(self, portal, site_info: dict | None) -> int:
        """Calculate priority based on portal type and past attempts.

        Returns 1-10, higher = more urgent.
        """
        # Base priority by registration type
        type_priority = {
            "api_key": 10,       # API key portals = highest value
            "email_form": 7,     # Email registration = medium
            "none": 0,           # Open APIs = skip
        }
        base = type_priority.get(portal.registration_type, 5)

        # Adjust based on past attempts
        if site_info:
            attempts = site_info.get("total_attempts", 0)
            successes = site_info.get("success_count", 0)

            # Never succeeded — boost priority
            if attempts > 0 and successes == 0:
                base = min(10, base + min(attempts, 3))

            # Recently succeeded — lower priority
            last_success = site_info.get("last_success")
            if last_success:
                try:
                    last_dt = datetime.fromisoformat(last_success)
                    days_since = (datetime.now(timezone.utc) - last_dt).days
                    if days_since < 7:
                        base = max(1, base - 3)
                except Exception:
                    pass

        return base

    def _get_cooldown(self, site_info: dict) -> float:
        """Calculate remaining cooldown in seconds.

        Exponential backoff: base 5min * 2^failures, capped at 24h.
        """
        failures = site_info.get("fail_count", 0)
        if failures == 0:
            return 0

        cooldown_s = min(300 * (2 ** min(failures, 8)), _MAX_COOLDOWN_S)

        last_attempt_str = site_info.get("last_attempt") or site_info.get("last_error")
        if last_attempt_str:
            try:
                last_attempt = datetime.fromisoformat(last_attempt_str)
                # Handle naive datetimes (from mock tests or legacy data)
                if last_attempt.tzinfo is None:
                    last_attempt = last_attempt.replace(tzinfo=timezone.utc)
                elapsed = (datetime.now(timezone.utc) - last_attempt).total_seconds()
                remaining = cooldown_s - elapsed
                return max(0, remaining)
            except Exception:
                pass

        return 0

    def get_status(self) -> dict[str, Any]:
        """Get scheduler status for monitoring."""
        from .portal_registry import PORTALS

        total = len(PORTALS)
        open_apis = len([p for p in PORTALS if p.id in _OPEN_API_IDS])
        deferred = len([p for p in PORTALS if p.id in _DEFERRED_IDS])
        need_reg = total - open_apis - deferred

        return {
            "total_portals": total,
            "open_apis": open_apis,
            "deferred": deferred,
            "need_registration": need_reg,
            "stats": self._stats,
            "knowledge_stats": self._knowledge.get_stats(),
        }


async def _cleanup_temp_files():
    """Remove stale .tmp files from /data that are older than 1 hour.

    The knowledge module creates .aria_knowledge.*.json.tmp files during
    disk flushes. These accumulate and fill the disk if not cleaned.
    """
    import glob
    import os
    import time

    data_dir = "/data"
    pattern = os.path.join(data_dir, ".aria_knowledge.*.json.tmp")
    now = time.time()
    deleted = 0
    freed = 0

    for f in glob.glob(pattern):
        try:
            age = now - os.path.getmtime(f)
            if age > 3600:  # older than 1 hour
                size = os.path.getsize(f)
                os.remove(f)
                deleted += 1
                freed += size
        except Exception:
            pass

    if deleted:
        logger.info("[cleanup] Deleted %d stale temp files (%.0f MB)", deleted, freed / 1024 / 1024)


async def autonomous_registration_loop(interval_s: int = _RUN_INTERVAL_S):
    """Background task that runs the registration scheduler continuously.

    Args:
        interval_s: Seconds between full scheduler runs (default 1 hour).
    """
    scheduler = PortalScheduler()
    logger.info("[scheduler] Autonomous registration loop started (interval=%ds)", interval_s)

    # Initial delay to let the app boot
    await asyncio.sleep(120)

    while True:
        try:
            # Disk cleanup before each cycle
            await _cleanup_temp_files()

            # Check engine pause flag
            try:
                from aria_service.autonomous.safety import is_engine_paused
                if await is_engine_paused():
                    logger.debug("[scheduler] Engine paused — skipping cycle")
                    await asyncio.sleep(interval_s)
                    continue
            except Exception:
                pass

            logger.info("[scheduler] Starting registration cycle...")
            results = await scheduler.run_once()

            logger.info(
                "[scheduler] Cycle complete: %d attempted, %d success, %d failed, %d deferred (%.1fs)",
                results.get("attempted", 0),
                results.get("successful", 0),
                results.get("failed", 0),
                results.get("deferred", 0),
                results.get("duration_s", 0),
            )

        except asyncio.CancelledError:
            logger.info("[scheduler] Loop cancelled")
            break
        except Exception as e:
            logger.error("[scheduler] Loop error: %s", e)

        await asyncio.sleep(interval_s)
