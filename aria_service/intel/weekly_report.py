"""
Weekly Learning Report — aggregates what ARIA learned over the past 7 days.

Pulls data from:
  - knowledge.py       (new facts by source type)
  - student.py         (mastery score deltas)
  - capability_gaps.py (unresolved + newly resolved gaps)
  - rag_store.py       (documents/standards ingested)
  - reasoning_library  (case health)
  - correction_learner (corrections received)

Can optionally produce a 200-word executive summary via LLM.

Phase 3 of the ARIA learning infrastructure.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone, timedelta
from typing import Any, Optional

from . import redis_store as rs

logger = logging.getLogger("aria.intel.weekly_report")

REPORT_KEY = "crucix:aria:weekly_report:latest"
SNAPSHOT_KEY = "crucix:aria:student:mastery_snapshots"
SEVEN_DAYS = 7 * 86400


async def generate_weekly_report(llm: Any = None) -> dict:
    """Produce the weekly intelligence learning report.

    Aggregates data from all learning subsystems over the past 7 days.
    If an LLM provider is supplied, generates a 200-word executive summary.

    Returns:
        Dict with all report sections + optional summary text.
    """
    now = time.time()
    cutoff = now - SEVEN_DAYS
    cutoff_iso = datetime.now(timezone.utc) - timedelta(days=7)

    report: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "period_start": cutoff_iso.isoformat(),
        "period_end": datetime.now(timezone.utc).isoformat(),
    }

    # ── 1. New facts learned ─────────────────────────────────────────────
    report["new_facts"] = await _count_new_facts(cutoff_iso)

    # ── 2. Mastery changes ───────────────────────────────────────────────
    report["mastery_changes"] = await _compute_mastery_deltas()

    # ── 3. Capability gaps ───────────────────────────────────────────────
    report["capability_gaps"] = await _gather_capability_gaps()

    # ── 4. Standards / documents ingested ────────────────────────────────
    report["rag_ingestion"] = await _count_rag_entries()

    # ── 5. Reasoning library health ──────────────────────────────────────
    report["reasoning_library"] = await _reasoning_library_health()

    # ── 6. Correction learning ───────────────────────────────────────────
    report["corrections"] = await _count_corrections(cutoff_iso)

    # ── 7. Knowledge freshness audit ───────────────────────────────────
    report["knowledge_freshness"] = await _audit_knowledge_freshness()

    # ── 8. LLM executive summary ────────────────────────────────────────
    summary_text = None
    if llm is not None:
        summary_text = await _generate_summary(llm, report)
    report["executive_summary"] = summary_text

    # Save as the latest report + take a mastery snapshot for next week
    await rs.set_json(REPORT_KEY, report)
    await schedule_weekly_snapshot()

    logger.info("Weekly learning report generated — %d new facts, %d gaps",
                report["new_facts"].get("total", 0),
                report["capability_gaps"].get("unresolved", 0))
    return report


async def get_last_report() -> Optional[dict]:
    """Retrieve the most recent weekly report from Redis."""
    return await rs.get_json(REPORT_KEY)


async def schedule_weekly_snapshot() -> None:
    """Save current mastery scores as the weekly baseline for the next
    report's delta calculation."""
    try:
        from . import student
        report = await student.get_mastery_report()
        snapshot = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "scores": {
                topic: info.get("score", 0.5)
                for topic, info in report.get("topics", {}).items()
            },
        }
        await rs.set_json(SNAPSHOT_KEY, snapshot)
        logger.info("Mastery snapshot saved — %d topics", len(snapshot["scores"]))
    except Exception as e:
        logger.warning("Failed to save mastery snapshot: %s", e)


# ── Internal data gatherers ───────────────────────────────────────────────────

async def _count_new_facts(cutoff: datetime) -> dict:
    """Count facts created in the last 7 days, grouped by source type."""
    try:
        from . import knowledge as kb
        facts = await kb.get_all_facts()

        by_source: dict[str, int] = {}
        total = 0

        for fact in facts:
            ts = fact.get("ts_created") or fact.get("timestamp") or ""
            try:
                fact_dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                if fact_dt < cutoff:
                    continue
            except (ValueError, TypeError):
                continue  # skip unparseable timestamps
            total += 1
            # Normalise source to a bucket
            src = fact.get("source", "unknown") or "unknown"
            if src.startswith("user_correction"):
                bucket = "user_correction"
            elif src.startswith("auto_extract"):
                bucket = "auto_extract"
            elif "dd_case" in src or "case_library" in src:
                bucket = "dd_case_library"
            elif "autonomous" in src or "research" in src:
                bucket = "autonomous_research"
            else:
                bucket = src
            by_source[bucket] = by_source.get(bucket, 0) + 1

        return {"total": total, "by_source": by_source}
    except Exception as e:
        logger.warning("Failed to count new facts: %s", e)
        return {"total": 0, "by_source": {}, "error": str(e)}


async def _compute_mastery_deltas() -> dict:
    """Compare current mastery to the last weekly snapshot."""
    try:
        from . import student

        current_report = await student.get_mastery_report()
        current_scores = {
            t: info.get("score", 0.5) for t, info in current_report.get("topics", {}).items()
        }

        snapshot = await rs.get_json(SNAPSHOT_KEY)
        if not snapshot or "scores" not in snapshot:
            return {
                "snapshot_available": False,
                "current_scores": current_scores,
                "note": "No previous snapshot — first report, deltas unavailable.",
            }

        prev_scores = snapshot.get("scores", {})
        if not isinstance(prev_scores, dict):
            prev_scores = {}
        deltas: dict[str, float] = {}
        for topic, score in current_scores.items():
            prev = prev_scores.get(topic, 0.5)
            deltas[topic] = round(score - prev, 4)

        sorted_deltas = sorted(deltas.items(), key=lambda x: x[1])
        biggest_decline = sorted_deltas[:3] if sorted_deltas else []
        biggest_improvement = sorted_deltas[-3:][::-1] if sorted_deltas else []

        return {
            "snapshot_available": True,
            "snapshot_timestamp": snapshot.get("timestamp"),
            "deltas": deltas,
            "biggest_improvement": biggest_improvement,
            "biggest_decline": biggest_decline,
            "current_scores": current_scores,
        }
    except Exception as e:
        logger.warning("Failed to compute mastery deltas: %s", e)
        return {"snapshot_available": False, "error": str(e)}


async def _gather_capability_gaps() -> dict:
    """Pull gap data from the capability_gaps module."""
    try:
        from . import capability_gaps
        summary = await capability_gaps.get_gap_summary()
        return summary
    except Exception as e:
        logger.warning("Failed to gather capability gaps: %s", e)
        return {"error": str(e)}


async def _count_rag_entries() -> dict:
    """Count RAG store entries (proxy for standards/documents ingested)."""
    try:
        from . import rag_store
        stats = await rag_store.get_stats()
        return {
            "available": stats.get("available", False),
            "documents_indexed": stats.get("documents_indexed", 0),
            "facts_indexed": stats.get("facts_indexed", 0),
            "total_chunks": stats.get("total_chunks", 0),
        }
    except Exception as e:
        logger.warning("Failed to count RAG entries: %s", e)
        return {"available": False, "error": str(e)}


async def _reasoning_library_health() -> dict:
    """Pull reasoning library stats."""
    try:
        from . import reasoning_library
        stats = await reasoning_library.get_stats()
        return {
            "total_cases": stats.get("total_cases", 0),
            "total_hits": stats.get("total_hits", 0),
            "total_lookups": stats.get("total_lookups", 0),
            "hit_rate": stats.get("hit_rate", 0.0),
            "total_writes": stats.get("total_writes", 0),
            "age_days": stats.get("age_days", 0),
        }
    except Exception as e:
        logger.warning("Failed to get reasoning library health: %s", e)
        return {"error": str(e)}


async def _count_corrections(cutoff: datetime) -> dict:
    """Count corrections received in the last 7 days."""
    try:
        from . import knowledge as kb
        facts = await kb.get_all_facts()

        corrections = 0
        facts_from_corrections = 0

        for fact in facts:
            src = fact.get("source", "") or ""
            ts = fact.get("ts_created") or fact.get("timestamp") or ""
            try:
                fact_dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                if fact_dt < cutoff:
                    continue
            except (ValueError, TypeError):
                continue
            if src.startswith("user_correction"):
                corrections += 1
                facts_from_corrections += 1

        return {
            "corrections_received": corrections,
            "facts_extracted": facts_from_corrections,
        }
    except Exception as e:
        logger.warning("Failed to count corrections: %s", e)
        return {"corrections_received": 0, "facts_extracted": 0, "error": str(e)}


async def _audit_knowledge_freshness() -> dict:
    """Proactive knowledge freshness audit.

    Checks:
    1. Stale-knowledge alerts registry — how many countries have unresolved events
    2. Knowledge base facts by age — how many facts are >30/60/90 days old
    3. RAG document recency — when were documents last ingested
    4. Brain hook signal activity — any modules gone silent
    """
    result: dict = {"stale_events": [], "fact_age": {}, "silent_modules": [], "recommendations": []}

    # 1. Stale events from the registry
    try:
        from . import stale_knowledge_alerts as ska
        events = ska._STALE_EVENTS
        result["stale_events"] = [
            {"country": e["country"], "event_date": e["event_date"], "event": e["event"]}
            for e in events
        ]
        result["stale_event_count"] = len(events)
        if events:
            result["recommendations"].append(
                f"{len(events)} countries have stale-knowledge alerts. "
                "Run officeholder verification searches for each."
            )
    except Exception as e:
        logger.debug("Stale events check failed: %s", e)

    # 2. Knowledge fact age distribution
    try:
        from . import knowledge
        import time as _time
        facts = await knowledge.get_all_facts()
        now = _time.time()
        age_buckets = {"<7d": 0, "7-30d": 0, "30-60d": 0, "60-90d": 0, ">90d": 0}
        for f in facts:
            ts = f.get("timestamp") or f.get("created_at") or f.get("ts", 0)
            if isinstance(ts, str):
                try:
                    from datetime import datetime as _dt, timezone as _tz
                    ts = _dt.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
                except Exception:
                    ts = 0
            age_days = (now - ts) / 86400 if ts else 999
            if age_days < 7:
                age_buckets["<7d"] += 1
            elif age_days < 30:
                age_buckets["7-30d"] += 1
            elif age_days < 60:
                age_buckets["30-60d"] += 1
            elif age_days < 90:
                age_buckets["60-90d"] += 1
            else:
                age_buckets[">90d"] += 1
        result["fact_age"] = age_buckets
        result["total_facts"] = len(facts)
        old_pct = (age_buckets[">90d"] / max(len(facts), 1)) * 100
        if old_pct > 30:
            result["recommendations"].append(
                f"{old_pct:.0f}% of facts are >90 days old. Consider re-ingesting "
                "knowledge modules or running reading sessions on stale topics."
            )
    except Exception as e:
        logger.debug("Fact age audit failed: %s", e)

    # 3. Brain hook module activity
    try:
        from . import brain_hook
        stats = await brain_hook.get_stats()
        stale = stats.get("stale_modules", [])
        never = stats.get("never_seen", [])
        result["silent_modules"] = stale + never
        if stale:
            result["recommendations"].append(
                f"{len(stale)} modules haven't sent brain signals in 24h: {', '.join(stale[:5])}"
            )
    except Exception as e:
        logger.debug("Brain hook audit failed: %s", e)

    # Feed brain hook with audit result
    try:
        from . import brain_hook
        await brain_hook.absorb(
            module="knowledge_ingestor",
            summary=f"Weekly knowledge freshness audit: {result.get('stale_event_count', 0)} stale events, "
                    f"{result.get('total_facts', 0)} facts, {len(result.get('silent_modules', []))} silent modules, "
                    f"{len(result.get('recommendations', []))} recommendations",
            success=True,
            confidence="CONFIRMED",
        )
    except Exception:
        pass

    return result


async def _generate_summary(llm: Any, report: dict) -> Optional[str]:
    """Use the LLM to produce a 200-word executive summary."""
    try:
        import json as _json

        # Build a compact data block for the LLM
        data_block = _json.dumps({
            "period": f"{report.get('period_start', '?')} to {report.get('period_end', '?')}",
            "new_facts": report.get("new_facts", {}),
            "mastery": report.get("mastery_changes", {}),
            "gaps_unresolved": report.get("capability_gaps", {}).get("unresolved", 0),
            "gaps_by_type": report.get("capability_gaps", {}).get("by_type", {}),
            "rag": report.get("rag_ingestion", {}),
            "reasoning_library": report.get("reasoning_library", {}),
            "corrections": report.get("corrections", {}),
        }, indent=2, default=str)

        prompt = (
            "You are ARIA, a defence procurement intelligence agent. "
            "Write a 200-word executive summary of your weekly learning report. "
            "Be concise, factual, and highlight the most important changes. "
            "Cover: new knowledge acquired, mastery score trends, capability gaps, "
            "and reasoning library health. Use bullet points.\n\n"
            f"DATA:\n{data_block}"
        )

        response = await llm(prompt)
        if isinstance(response, dict):
            return response.get("text") or response.get("content") or str(response)
        return str(response) if response else None
    except Exception as e:
        logger.warning("LLM summary generation failed: %s", e)
        return None
