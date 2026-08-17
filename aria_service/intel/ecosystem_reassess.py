"""ARIA Ecosystem Reassessment — hourly self-audit of her own capabilities.

Fires every 6 hours (HOURLY-ECOSYSTEM-REASSESS — the id is historical, the
cron is `0 */6 * * *`; R-F4085) and writes a priority queue that
DAILY-CORE-DEVELOP drains the following morning.

What this module does (read-only):
  1. Mastery drift — per-topic score changes from self_metrics
  2. Source freshness — Web Atlas entries with last_fetch > 30 days
  3. Mistake-ledger patterns — unresolved recurring corrections
  4. Capability gaps — unhandled queries, parse failures, API outages
  5. Coverage map — (region × topic) cells at HIGH / CRITICAL gap level

Output: `aria:core:reassess:queue` — sorted set scored by urgency.
Consumers: core_develop.py (drains top 3), the 05:45 team briefing,
           the /atlas/gaps endpoint.
"""
from __future__ import annotations
from .engine_wiring import wire_success, wire_failure

import logging
import time
from datetime import datetime, timezone
from typing import Any

from . import redis_store as rs

logger = logging.getLogger("aria.intel.ecosystem_reassess")

_QUEUE_KEY = "aria:core:reassess:queue"
_LAST_RUN_KEY = "aria:core:reassess:last_run"

# Urgency scoring — higher = sooner
_URGENCY = {
    "critical_gap": 100,
    "source_stale": 70,
    "mastery_drift": 60,
    "capability_gap": 55,
    "high_gap": 40,
    "mistake_pattern": 30,
    "medium_gap": 15,
}


async def run() -> dict:
    """Run a full reassessment pass. Returns summary stats."""
    now = datetime.now(timezone.utc).isoformat()
    queue: list[dict] = []

    # 1a. Coverage gaps from Web Atlas (cell-level: region × topic)
    try:
        from . import web_atlas
        gaps = await web_atlas.surface_gaps(min_level="MEDIUM", limit=50)
        for g in gaps:
            level = g.get("gap_level", "MEDIUM")
            # Skip gaps touched in the last 24h — source_scout already attempted them
            last_fetch = g.get("last_fetch") or g.get("last_fetch_ts", 0)
            if isinstance(last_fetch, str):
                try:
                    last_fetch = datetime.fromisoformat(
                        last_fetch.replace("Z", "+00:00")
                    ).timestamp()
                except Exception:
                    last_fetch = 0
            if last_fetch and (time.time() - last_fetch) < 86400:
                continue  # Recently attempted — don't re-queue
            key = f"gap:{g.get('region')}:{g.get('topic')}"
            urgency_kind = {
                "CRITICAL": "critical_gap",
                "HIGH": "high_gap",
                "MEDIUM": "medium_gap",
            }.get(level, "medium_gap")
            queue.append({
                "key": key,
                "kind": urgency_kind,
                "urgency": _URGENCY[urgency_kind],
                "payload": g,
                "added_at": now,
            })
    except Exception as e:
        logger.debug("web_atlas gap scan skipped: %s", e)

    # 1b. Coverage-domain gaps (the 23 named domains from source_validator).
    # This is richer than 1a because it carries priority tier and deficit
    # explicitly — CRITICAL/HIGH/MEDIUM/LOW per domain requirements.
    try:
        from . import source_validator
        domain_gaps = await source_validator.coverage_gaps_by_domain()
        for g in domain_gaps:
            priority = g.get("priority", "MEDIUM")
            urgency_kind = {
                "CRITICAL": "critical_gap",
                "HIGH": "high_gap",
                "MEDIUM": "medium_gap",
                "LOW": "medium_gap",
            }.get(priority, "medium_gap")
            # Prefer this richer payload when it maps to the same domain
            queue.append({
                "key": f"coverage_domain:{g.get('domain')}",
                "kind": urgency_kind,
                "urgency": _URGENCY[urgency_kind] + g.get("deficit", 0) * 3,
                "payload": g,
                "added_at": now,
            })
    except Exception as e:
        logger.debug("coverage-domain gap scan skipped: %s", e)

    # 2. Mastery drift — topics whose score fell >10 points in 7 days
    try:
        from . import self_metrics
        if hasattr(self_metrics, "recent_drift"):
            drift = await self_metrics.recent_drift(days=7, threshold_pp=10.0)
            for topic, delta in (drift or {}).items():
                queue.append({
                    "key": f"mastery:{topic}",
                    "kind": "mastery_drift",
                    "urgency": _URGENCY["mastery_drift"] + int(abs(delta)),
                    "payload": {"topic": topic, "delta_pp": delta},
                    "added_at": now,
                })
    except Exception as e:
        logger.debug("mastery drift scan skipped: %s", e)

    # 3. Mistake-ledger patterns — repeated corrections on the same topic
    try:
        from . import mistake_ledger
        if hasattr(mistake_ledger, "recent"):
            recent = await mistake_ledger.recent(limit=200)
            counts: dict[str, int] = {}
            for m in recent or []:
                # mistake_ledger.record() persists `what_class` (e.g.
                # "tool_fabrication", "propaganda_elevation", "gtg_jurisdiction")
                # and `domain` (e.g. "clause_20"). It does NOT persist `topic`.
                # Reading `topic` silently rejected every record, so pattern
                # detection never fired. Alias to the real fields; fall back
                # to `topic` for any legacy or externally-populated rows.
                t = (
                    (m.get("what_class") or m.get("domain") or m.get("topic") or "")
                    .strip().lower()
                )
                if not t:
                    continue
                counts[t] = counts.get(t, 0) + 1
            for topic, count in counts.items():
                if count >= 3:
                    queue.append({
                        "key": f"mistake:{topic}",
                        "kind": "mistake_pattern",
                        "urgency": _URGENCY["mistake_pattern"] + count * 2,
                        "payload": {"topic": topic, "count": count},
                        "added_at": now,
                    })
    except Exception as e:
        logger.debug("mistake-ledger scan skipped: %s", e)

    # 4. Capability gaps — unhandled file types, missing APIs
    try:
        from . import capability_gaps
        if hasattr(capability_gaps, "recent"):
            gaps = await capability_gaps.recent(limit=50)
            for g in gaps or []:
                # capability_gaps.record_gap() persists `type`, not `kind`.
                # Reading `kind` silently fell back to the default so every
                # gap was re-keyed as the generic "capability_gap", losing
                # the specific type signal.
                kind = (
                    (g.get("type") or g.get("kind") or "").strip()
                    or "capability_gap"
                )
                queue.append({
                    "key": f"capability:{kind}:{g.get('detail','')[:40]}",
                    "kind": "capability_gap",
                    "urgency": _URGENCY["capability_gap"],
                    "payload": g,
                    "added_at": now,
                })
    except Exception as e:
        logger.debug("capability-gap scan skipped: %s", e)

    # Dedup on key — keep the highest urgency.
    by_key: dict[str, dict] = {}
    for item in queue:
        k = item["key"]
        if k not in by_key or item["urgency"] > by_key[k]["urgency"]:
            by_key[k] = item
    queue = sorted(by_key.values(), key=lambda x: x["urgency"], reverse=True)

    # Persist top 200 — CORE-DEVELOP drains from the top.
    await rs.set_json(_QUEUE_KEY, queue[:200], ex=7 * 86400)
    await rs.set(_LAST_RUN_KEY, now, ex=7 * 86400)

    result = {
        "queued": len(queue[:200]),
        "breakdown": {
            k: sum(1 for i in queue if i["kind"] == k) for k in set(i["kind"] for i in queue)
        },
        "top_3": queue[:3],
        "at": now,
    }

    # Brain signal — so the 05:45 briefing surfaces gap pressure
    # ("12 CRITICAL gaps pending, 34 HIGH") before the operator asks.
    try:
        from . import brain_hook as _bh
        critical = sum(1 for i in queue if i.get("kind") == "critical_gap")
        high = sum(1 for i in queue if i.get("kind") == "high_gap")
        await _bh.absorb(
            module="ecosystem_reassess",
            summary=f"Reassess: {len(queue)} items queued ({critical} critical, {high} high)",
            detail=str(result["breakdown"])[:400],
            success=True,
        )
    except Exception:
        pass
    return result


async def get_queue(limit: int = 50) -> list[dict]:
    """Return the current reassess queue (read-only)."""
    queue = await rs.get_json(_QUEUE_KEY) or []
    # R-F2118/R-F2119 §21a — wire module active
    try:
        wire_success(module="ecosystem_reassess",
                     summary="ecosystem_reassess module active",
                     source_id="ecosystem_reassess:init")
    except Exception:
        try:
            wire_failure(module="ecosystem_reassess", detail="module init failed",
                        gap_type="engine_failure", source="ecosystem_reassess:init")
        except Exception:
            pass

    return queue[:limit]
