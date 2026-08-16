"""Research engine — the 24/7 self-directed learner.

Every hour, ARIA reads her own mastery heatmap, picks the weakest
(topic, region) cell, generates 3-5 research queries, dispatches the
existing web_search + web_crawl + corpus_ingest infrastructure, and
measures mastery lift over 24h.

Key invariant: the loop is ALWAYS attacking the LOWEST-mastery cell,
so mastery converges upward over time. If lift stalls after N
attempts, escalate as "I can't learn this on my own, need operator
to point me at a source" — surfaces as a capability gap.

Zero marginal cost: reuses web_search (free fallback tier), existing
corpus_ingest, and rule-based extraction. If every provider charges
for search, the loop gracefully degrades to crawling known free
sources (Wikipedia, government registries, public RSS).

Scheduled hourly via RESEARCH-WEAK-CELL-HOURLY. Wall-clock 3-minute
budget per run; work beyond that waits for next run.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("aria.learning.research_engine")



_REDIS_ATTEMPT_KEY = "crucix:learning:research:attempts"
_REDIS_STATS_KEY   = "crucix:learning:research:stats_24h"

_WALL_BUDGET_SEC   = 180.0
_QUERIES_PER_CELL  = 3
_MAX_CELLS_PER_RUN = 2    # don't over-rotate; focus time per cell


# ═══════════════════════════════════════════════════════════════════════
# Query generation templates
# ═══════════════════════════════════════════════════════════════════════
#
# Simple rule-based synthesis. No LLM call. Takes (topic, region) and
# returns a list of queries that would plausibly improve mastery in
# that cell. Templates are defence/BD-appropriate.

_TOPIC_TEMPLATES: dict[str, list[str]] = {
    "procurement": [
        "{region} defence procurement 2026 tender",
        "{region} MoD equipment modernisation programme",
        "{region} national defence budget allocation",
    ],
    "compliance": [
        "{region} export control licensing",
        "{region} sanctions regime 2026",
        "{region} anti-corruption defence contracting",
    ],
    "geopolitics": [
        "{region} strategic partnership alliance",
        "{region} regional security dynamics 2026",
        "{region} bilateral defence agreement",
    ],
    "relationships": [
        "{region} defence industry BD contacts",
        "{region} OEM representatives defence",
    ],
    "market_intel": [
        "{region} defence market size 2026",
        "{region} defence export growth",
        "{region} domestic arms industry",
    ],
    "competitor_intel": [
        "{region} defence primes winning contracts 2026",
        "{region} foreign OEM market share",
    ],
    "technical": [
        "{region} armed forces equipment standards",
    ],
    "osint": [
        "{region} defence news 2026",
        "{region} military operations update",
    ],
    "legal": [
        "{region} defence acquisition law",
        "{region} end-user certificate requirements",
    ],
    "finance": [
        "{region} defence financing mechanisms",
    ],
    "general": [
        "{region} defence sector overview 2026",
    ],
}


def _generate_queries(topic: str, region: str) -> list[str]:
    """Build queries for a (topic, region) pair. Lower-cases region
    where appropriate for natural query wording."""
    templates = _TOPIC_TEMPLATES.get(topic.lower(), _TOPIC_TEMPLATES["general"])
    region_display = _region_display_name(region)
    queries = []
    for t in templates[:_QUERIES_PER_CELL]:
        queries.append(t.format(region=region_display))
    return queries


def _region_display_name(region_slug: str) -> str:
    """Turn 'west_africa' → 'West Africa', 'latam_non_lusophone' → 'Latin America'."""
    mapping = {
        "lusophone":           "Lusophone Africa",
        "west_africa":         "West Africa",
        "east_africa":         "East Africa",
        "central_africa":      "Central Africa",
        "north_africa":        "North Africa",
        "southern_africa":     "Southern Africa",
        "mena":                "Middle East",
        "gulf":                "Gulf GCC",
        "turkey":              "Türkiye",
        "south_asia":          "South Asia",
        "southeast_asia":      "Southeast Asia",
        # F33 fix 2026-04-27: corpus_registry.yaml tags some sources
        # `region: latam` (not the more specific latam_non_lusophone),
        # so the heatmap surfaces "latam" as a weak-cell slug. Without
        # this entry the fallback Title-cased it to "Latam" — Google News
        # returned 0 results on every Latam-prefixed query in the live log.
        "latam":               "Latin America",
        "latam_lusophone":     "Brazil",
        "latam_non_lusophone": "Latin America",
        "spain_latam":         "Spain Latin America",
        "europe":              "Europe",
        "balkans":             "Balkans",
        "nato":                "NATO",
        "global":              "global",
    }
    return mapping.get(region_slug, region_slug.replace("_", " ").title())


# ═══════════════════════════════════════════════════════════════════════
# Weak-cell selection
# ═══════════════════════════════════════════════════════════════════════

async def _pick_weakest_cells(limit: int) -> list[tuple[str, str, float]]:
    """Read mastery heatmap, return weakest cells as (topic, region, score)."""
    try:
        from ..intel import student
        heatmap = await student.get_regional_heatmap()
    except Exception as exc:
        logger.debug("heatmap read failed: %s", exc)
        return []
    weak = heatmap.get("weak_cells") or []
    # weak_cells already sorted ascending by score
    out: list[tuple[str, str, float]] = []
    for entry in weak[:limit * 3]:
        if not isinstance(entry, dict):
            continue
        out.append((entry.get("topic", "general"), entry.get("region", "global"), entry.get("score", 0.5)))
        if len(out) >= limit:
            break
    return out


# ═══════════════════════════════════════════════════════════════════════
# Attempt tracking — detect stalled cells
# ═══════════════════════════════════════════════════════════════════════

async def _record_attempt(topic: str, region: str, queries_dispatched: int, ingests: int) -> None:
    from ..intel import redis_store as rs
    try:
        data = await rs.get_json(_REDIS_ATTEMPT_KEY) or {}
        if not isinstance(data, dict):
            data = {}
        key = f"{topic}::{region}"
        slot = data.setdefault(key, {"attempts": 0, "ingests": 0, "last_at": ""})
        slot["attempts"] = slot.get("attempts", 0) + 1
        slot["ingests"] = slot.get("ingests", 0) + ingests
        slot["last_at"] = datetime.now(timezone.utc).isoformat()
        slot["queries_dispatched"] = slot.get("queries_dispatched", 0) + queries_dispatched
        await rs.set_json(_REDIS_ATTEMPT_KEY, data, ex=30 * 86400)
    except Exception as exc:
        logger.debug("attempt record failed: %s", exc)


async def _is_stalled(topic: str, region: str, threshold: int = 5) -> bool:
    """Return True if we've attempted this cell >= threshold times with
    no ingestion success — operator needs to intervene."""
    from ..intel import redis_store as rs
    try:
        data = await rs.get_json(_REDIS_ATTEMPT_KEY) or {}
    except Exception:
        return False
    slot = (data or {}).get(f"{topic}::{region}", {})
    return slot.get("attempts", 0) >= threshold and slot.get("ingests", 0) == 0


# ═══════════════════════════════════════════════════════════════════════
# Dispatcher — reuses existing ARIA infrastructure
# ═══════════════════════════════════════════════════════════════════════

async def _dispatch_query(query: str) -> dict[str, Any]:
    """Run a single query through web_search → find URLs → ingest top results.
    Returns a summary dict."""
    result = {"query": query, "searched": False, "ingested": 0, "urls_found": 0}
    try:
        from ..intel import web_search
        hits = await web_search.search_multilingual(query, max_results=6)
        result["searched"] = True
        result["urls_found"] = len(hits or [])
        if not hits:
            # R-F177 (2026-05-11): empty results aren't an error per se,
            # but persistent emptiness across a tick means web_search is
            # degraded. Surface to DLQ at debug level so operator triage
            # has a trail when ticks come back productive=0.
            try:
                from ..intel import dead_letter_queue as _dlq
                await _dlq.enqueue_pipeline_failure(
                    pipeline="research_engine.dispatch_query",
                    error="web_search returned no hits",
                    context={"query": query[:200]},
                )
            except Exception:
                pass
            return result
    except Exception as exc:
        logger.debug("web_search failed for %r: %s", query, exc)
        # R-F177: route exception cases to DLQ so the operator sees WHY
        # a research tick produced zero queued URLs.
        try:
            from ..intel import dead_letter_queue as _dlq
            await _dlq.enqueue_pipeline_failure(
                pipeline="research_engine.dispatch_query",
                error=f"web_search exception: {type(exc).__name__}: {str(exc)[:200]}",
                context={"query": query[:200]},
            )
        except Exception:
            pass
        return result

    # Push top URLs to the knowledge spider queue for later processing.
    # The spider handles fetch + ingest on its own cadence.
    try:
        from ..intel import redis_store as rs
        queue = await rs.get_json("crucix:learning:spider:queue") or []
        if not isinstance(queue, list):
            queue = []
        added = 0
        for h in hits[:5]:
            url = getattr(h, "url", None) or (h.get("url") if isinstance(h, dict) else None)
            if not url:
                continue
            queue.append({"url": url, "depth": 0, "source": f"research:{query[:50]}"})
            added += 1
        await rs.set_json("crucix:learning:spider:queue", queue[-5000:])
        result["ingested"] = added  # pending ingest — actual chars happen in spider
    except Exception as exc:
        logger.debug("spider queue add failed: %s", exc)

    return result


# ═══════════════════════════════════════════════════════════════════════
# Public runner
# ═══════════════════════════════════════════════════════════════════════

async def run_research_tick() -> dict[str, Any]:
    """One research tick — pick weakest cells, generate queries, dispatch.

    Returns a summary of what was attempted.
    """
    t_start = time.monotonic()

    # R-F175 (2026-05-11): record the tick BEFORE the early-return so
    # the dashboard reflects scheduler activity, not just productive
    # ticks. Pre-R-F175, `research_ticks_24h` rendered 0 even when the
    # engine fired hourly because every fire hit the `no weak cells`
    # early-return path without incrementing the counter. Honest count
    # now distinguishes "scheduler fired" from "work done".
    try:
        from ..intel import redis_store as rs
        _e = await rs.get_json(_REDIS_STATS_KEY)
        _stats = _e if isinstance(_e, dict) else {"ticks": 0, "queries": 0, "urls_queued": 0}
        _stats["ticks"] = _stats.get("ticks", 0) + 1
        _stats["last_fired_at"] = datetime.now(timezone.utc).isoformat()
        if not isinstance(_e, dict):
            await rs.set_json(_REDIS_STATS_KEY, _stats, ex=86400)
        else:
            await rs.set_json(_REDIS_STATS_KEY, _stats, keepttl=True)
    except Exception:
        pass

    cells = await _pick_weakest_cells(_MAX_CELLS_PER_RUN)
    if not cells:
        # R-F175: surface the empty result via brain_hook so operator can
        # see WHY the engine produced nothing rather than just seeing 0.
        try:
            from ..intel import brain_hook
            await brain_hook.absorb(
                module="research_engine",
                summary=(
                    "Research tick — 0 weak cells found in regional mastery heatmap. "
                    "Either regional mastery store is empty OR every cell is above "
                    "WEAK_THRESHOLD (0.55). Operator: check student.regional_mastery "
                    "state."
                ),
                success=True,  # not a failure — just nothing to do
                gap_type=None,
            )
        except Exception:
            pass
        return {"cells_attacked": 0, "duration_s": 0, "reason": "no weak cells"}

    results: list[dict[str, Any]] = []
    stalled_cells: list[dict[str, Any]] = []

    for topic, region, score in cells:
        if (time.monotonic() - t_start) > _WALL_BUDGET_SEC:
            break
        # Check stall state — don't waste cycles on a cell we keep failing
        if await _is_stalled(topic, region):
            stalled_cells.append({
                "topic": topic, "region": region, "score": score,
                "reason": "stalled — operator must provide a source",
            })
            continue

        queries = _generate_queries(topic, region)
        total_urls = 0
        total_queued = 0
        for q in queries:
            if (time.monotonic() - t_start) > _WALL_BUDGET_SEC:
                break
            r = await _dispatch_query(q)
            total_urls += r.get("urls_found", 0)
            total_queued += r.get("ingested", 0)
            # Gentle pacing between queries
            await asyncio.sleep(1.0)

        await _record_attempt(topic, region, queries_dispatched=len(queries), ingests=total_queued)
        results.append({
            "topic": topic,
            "region": region,
            "current_score": score,
            "queries_dispatched": len(queries),
            "urls_found_total": total_urls,
            "queued_for_spider": total_queued,
        })

    # 24h stats — keepttl on subsequent writes (same family fix as f981c0a).
    # R-F175 (2026-05-11): ticks counter is incremented at the top of
    # run_research_tick now, NOT here, so it reflects scheduler fires
    # (not just productive ticks). Only query/url counts increment here.
    try:
        from ..intel import redis_store as rs
        existing = await rs.get_json(_REDIS_STATS_KEY)
        is_fresh = not isinstance(existing, dict)
        stats = existing if isinstance(existing, dict) else {"ticks": 0, "queries": 0, "urls_queued": 0}
        stats["queries"] = stats.get("queries", 0) + sum(r["queries_dispatched"] for r in results)
        stats["urls_queued"] = stats.get("urls_queued", 0) + sum(r["queued_for_spider"] for r in results)
        if is_fresh:
            await rs.set_json(_REDIS_STATS_KEY, stats, ex=86400)
        else:
            await rs.set_json(_REDIS_STATS_KEY, stats, keepttl=True)
    except Exception:
        pass

    # brain_hook
    try:
        from ..intel import brain_hook
        queued = sum(r["queued_for_spider"] for r in results)
        await brain_hook.absorb(
            module="research_engine",
            summary=(
                f"Research tick — attacked {len(results)} weak cells, "
                f"queued {queued} URLs for spider"
            ),
            success=queued > 0,
            gap_type="stalled_cell" if stalled_cells else None,
            gap_detail=(
                ", ".join(f"{c['topic']}:{c['region']}" for c in stalled_cells[:3])
                if stalled_cells else None
            ),
        )
    except Exception:
        pass

    summary_out = {
        "cells_attacked": len(results),
        "cells_stalled": len(stalled_cells),
        "stalled_cells": stalled_cells,
        "results": results,
        "duration_s": round(time.monotonic() - t_start, 2),
    }
    logger.info("[research_engine] %s", summary_out)
    return summary_out


async def get_stats() -> dict[str, Any]:
    from ..intel import redis_store as rs
    try:
        stats = await rs.get_json(_REDIS_STATS_KEY) or {}
        attempts = await rs.get_json(_REDIS_ATTEMPT_KEY) or {}
    except Exception:
        stats = {}
        attempts = {}
    stalled = []
    for key, slot in (attempts or {}).items():
        if isinstance(slot, dict) and slot.get("attempts", 0) >= 5 and slot.get("ingests", 0) == 0:
            stalled.append({"cell": key, "attempts": slot["attempts"]})
    return {
        "ticks_24h": stats.get("ticks", 0),
        "queries_24h": stats.get("queries", 0),
        "urls_queued_24h": stats.get("urls_queued", 0),
        "stalled_cells": stalled[:20],
    }


def summary() -> dict[str, Any]:
    return {
        "max_cells_per_run": _MAX_CELLS_PER_RUN,
        "queries_per_cell": _QUERIES_PER_CELL,
        "wall_budget_sec": _WALL_BUDGET_SEC,
        "topics_with_templates": list(_TOPIC_TEMPLATES.keys()),
    }
