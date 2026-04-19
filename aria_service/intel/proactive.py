"""
ARIA Proactive Watch — anticipates needs instead of waiting to be asked.

The default ARIA pattern is REACTIVE: she answers when asked. This module
makes her PROACTIVE: she watches the live data stream, identifies things
the team would want to know about before they ask, and prepares briefings.

Five behaviours, all running on background loops:

1. ANOMALY WATCH
   Scans the latest sweep for unusual signals — sudden spike in mentions
   of a country, a new contract value above a threshold, an entity that
   appears in 3+ sources within 24h. Flags them as "worth a look".

2. KNOWLEDGE GAP DETECTION
   Watches conversation patterns. If users keep asking about a topic ARIA
   doesn't have strong knowledge on, she autonomously triggers a research
   cycle to fill the gap.

3. MASTERY-DRIVEN PREPARATION
   Looks at her own student mastery scores. For weak topics, she triggers
   reading sessions BEFORE the next conversation lands on those topics.

4. RELATIONSHIP WINDOW ALERTS
   Tracks contact tenure (when a minister was appointed). When a window
   closes (e.g. 90 days into a new role = peak influenceability period
   ending), pushes an alert.

5. DAILY INTELLIGENCE BRIEFING
   At a scheduled time each morning, generates and pushes a digest of
   the most important intel from the past 24h.

These behaviours run as asyncio tasks in main.py and post their findings
to a Redis queue that the WhatsApp listener can poll and surface.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any

from . import redis_store as rs
from . import knowledge as kb
from . import intel_ledger
from . import neural_memory
from . import student
from . import reasoning_library

logger = logging.getLogger("aria.proactive")

ALERT_QUEUE_KEY = "crucix:aria:proactive:alerts"
ANOMALY_BASELINE_KEY = "crucix:aria:proactive:anomaly_baseline"
GAP_TRACKER_KEY = "crucix:aria:proactive:gap_tracker"
LAST_BRIEFING_KEY = "crucix:aria:proactive:last_briefing"
LAST_MASTERY_PREP_KEY = "crucix:aria:proactive:last_mastery_prep"
MASTERY_PREP_INTERVAL_S = 6 * 3600


# ── Alert queue ─────────────────────────────────────────────────────────────

async def push_alert(alert: dict) -> None:
    """Push a proactive alert into the queue. The WhatsApp listener polls
    this queue every minute and surfaces unseen alerts to the team.
    """
    try:
        queue = await rs.get_json(ALERT_QUEUE_KEY) or []
        alert.setdefault("ts", time.time())
        alert.setdefault("seen", False)
        alert["id"] = f"alert_{int(time.time() * 1000)}_{len(queue)}"
        queue.append(alert)
        queue = queue[-100:]  # cap at 100 unsent alerts
        await rs.set_json(ALERT_QUEUE_KEY, queue, ex=7 * 86400)
        logger.info("Proactive alert queued: [%s] %s",
                    alert.get("severity", "info"), alert.get("title", "untitled")[:80])
    except Exception as e:
        logger.warning("push_alert failed: %s", e)


async def get_unseen_alerts(mark_seen: bool = True) -> list[dict]:
    """Drain the alert queue. Returns unseen alerts; marks them seen if requested."""
    try:
        queue = await rs.get_json(ALERT_QUEUE_KEY) or []
        unseen = [a for a in queue if not a.get("seen")]
        if mark_seen and unseen:
            for a in queue:
                a["seen"] = True
            await rs.set_json(ALERT_QUEUE_KEY, queue, ex=7 * 86400)
        return unseen
    except Exception as e:
        logger.warning("get_unseen_alerts failed: %s", e)
        return []


# ── Behaviour 1: Anomaly watch ──────────────────────────────────────────────

async def anomaly_watch(intel_data: dict | None) -> int:
    """Scan the latest sweep for anomalies vs the baseline. Push alerts.

    Looks for:
    - Country mentions spike (3x+ over baseline)
    - Brand-new entities appearing in 3+ sources
    - Tender values above significant thresholds
    """
    if not intel_data:
        return 0

    alerts_pushed = 0

    # Load baseline (rolling avg of country mentions over last 14 days)
    baseline = await rs.get_json(ANOMALY_BASELINE_KEY) or {}
    today_counts: dict[str, int] = {}

    # Count country mentions in this sweep's signals
    sources = []
    for key in ("urgentSignals", "defenseNews", "news"):
        v = intel_data.get(key)
        if isinstance(v, list):
            sources.extend(v)

    COUNTRIES = [
        "Angola", "Mozambique", "Nigeria", "Kenya", "Mali", "Niger", "Burkina Faso",
        "Sudan", "Ethiopia", "Somalia", "Libya", "Syria", "Iraq", "Yemen",
        "Saudi Arabia", "UAE", "Türkiye", "Turkey", "Indonesia", "Philippines",
        "Brazil", "Colombia", "Venezuela", "Ukraine", "Russia",
    ]
    for src in sources:
        if not isinstance(src, dict):
            continue
        text = (src.get("title", "") + " " + src.get("text", "") + " " + src.get("content", "")).lower()
        for country in COUNTRIES:
            if country.lower() in text:
                today_counts[country] = today_counts.get(country, 0) + 1

    # Compare with baseline + push alerts for spikes
    for country, count in today_counts.items():
        baseline_count = baseline.get(country, 0)
        # Spike = 3x baseline AND at least 5 mentions
        if count >= 5 and count >= baseline_count * 3:
            await push_alert({
                "type": "country_mention_spike",
                "title": f"📈 Mention spike: {country}",
                "severity": "high",
                "body": (
                    f"{country} mentioned {count}× in the last sweep "
                    f"(baseline {baseline_count}×). Worth a /research {country} cycle."
                ),
                "metadata": {"country": country, "count": count, "baseline": baseline_count},
            })
            alerts_pushed += 1

    # Update baseline (EWMA)
    for country, count in today_counts.items():
        old = baseline.get(country, 0)
        baseline[country] = round(old * 0.85 + count * 0.15, 1)
    await rs.set_json(ANOMALY_BASELINE_KEY, baseline, ex=30 * 86400)

    return alerts_pushed


# ── Behaviour 2: Knowledge gap detection ───────────────────────────────────

async def detect_knowledge_gaps(question: str) -> None:
    """Track which topics get asked about repeatedly with weak local answers.

    Called from aria_chat after every conversation. If the same topic
    surfaces 3+ times with low confidence, flag it as a research priority.
    """
    if not question or len(question) < 5:
        return
    try:
        topics = student.detect_topics(question)
        if not topics or topics == ["general"]:
            return

        gaps = await rs.get_json(GAP_TRACKER_KEY) or {}
        now = time.time()
        for topic in topics:
            entry = gaps.get(topic) or {"count": 0, "last_seen": 0, "research_triggered": 0}
            entry["count"] = entry.get("count", 0) + 1
            entry["last_seen"] = now
            gaps[topic] = entry

            # If same topic asked 3+ times in 24h and we haven't triggered
            # research yet, push an alert telling ARIA to study it
            if (entry["count"] >= 3
                    and now - entry.get("research_triggered", 0) > 86400):
                mastery_report = await student.get_mastery_report()
                topic_score = mastery_report.get("topics", {}).get(topic, {}).get("score", 0.5)
                if topic_score < 0.65:
                    await push_alert({
                        "type": "knowledge_gap",
                        "title": f"📚 Knowledge gap: {topic}",
                        "severity": "medium",
                        "body": (
                            f"The team has asked about *{topic}* {entry['count']} times "
                            f"recently and ARIA's mastery is only {int(topic_score * 100)}%. "
                            f"Triggering a focused reading session."
                        ),
                        "metadata": {"topic": topic, "asked_count": entry["count"], "mastery": topic_score},
                    })
                    entry["research_triggered"] = now
                    gaps[topic] = entry

        # Prune old entries
        gaps = {k: v for k, v in gaps.items() if now - v.get("last_seen", 0) < 7 * 86400}
        await rs.set_json(GAP_TRACKER_KEY, gaps, ex=14 * 86400)
    except Exception as e:
        logger.debug("detect_knowledge_gaps failed: %s", e)


# ── Behaviour 3: Mastery-driven preparation ────────────────────────────────

async def prepare_weak_topics(llm=None) -> int:
    """Look at student mastery, find the weakest topics, trigger reading.

    Caller (`_proactive_loop`) ticks hourly so `daily_briefing_check` can fire
    near 06:00 UTC, but the mastery-prep alert claims a 6h cadence. Without an
    internal guard we'd push it every hour — which is what was spamming WA at
    01:42→08:42. Self-rate-limit using Redis so the cadence survives restarts.
    """
    try:
        now = time.time()
        last = await rs.get_json(LAST_MASTERY_PREP_KEY) or {}
        last_ts = last.get("ts", 0)
        if now - last_ts < MASTERY_PREP_INTERVAL_S:
            return 0

        mastery_report = await student.get_mastery_report()
        weak = mastery_report.get("weak_topics", [])
        if not weak:
            return 0

        # Push an alert summarising the prep work
        await push_alert({
            "type": "mastery_prep",
            "title": f"🎓 Studying weak topics: {', '.join(weak[:3])}",
            "severity": "info",
            "body": (
                f"ARIA is auto-prepping reading sessions on her weakest topics: "
                f"{', '.join(weak)}. This happens autonomously every 6h."
            ),
            "metadata": {"weak_topics": weak},
        })

        await rs.set_json(
            LAST_MASTERY_PREP_KEY,
            {"ts": now, "weak": weak},
            ex=14 * 86400,
        )

        # Actual reading session is triggered separately by the student loop
        return len(weak)
    except Exception as e:
        logger.warning("prepare_weak_topics failed: %s", e)
        return 0


# ── Behaviour 4: Daily intelligence briefing ───────────────────────────────

async def daily_briefing_check(intel_data: dict | None) -> bool:
    """Push a daily morning briefing if today's hasn't been sent yet.

    Runs every hour but only fires once per day, around 07:00 UTC.
    """
    try:
        last = await rs.get_json(LAST_BRIEFING_KEY) or {}
        now = datetime.now(timezone.utc)
        last_date = last.get("date")
        today = now.strftime("%Y-%m-%d")
        if last_date == today:
            return False
        # Only fire after 06:00 UTC (rough morning window)
        if now.hour < 6:
            return False

        # Build a compact briefing from the latest sweep + ledger
        briefing_parts = []
        briefing_parts.append(f"☀️ *Morning briefing — {today}*")
        briefing_parts.append("")

        if intel_data:
            opps = intel_data.get("opportunities") or []
            if isinstance(opps, list) and opps:
                briefing_parts.append("*Top opportunities*:")
                for o in opps[:5]:
                    if isinstance(o, dict):
                        briefing_parts.append(
                            f"  • {o.get('market', '?')} (score {o.get('score', 0)}/100)"
                        )
                briefing_parts.append("")

            corrs = intel_data.get("correlations") or []
            if isinstance(corrs, list) and corrs:
                criticals = [c for c in corrs if isinstance(c, dict) and c.get("severity") == "critical"]
                if criticals:
                    briefing_parts.append(f"*Critical correlations* ({len(criticals)}):")
                    for c in criticals[:5]:
                        briefing_parts.append(f"  • {c.get('region', '?')}")
                    briefing_parts.append("")

        # Add student state
        try:
            mastery = await student.get_mastery_report()
            briefing_parts.append(f"*ARIA mastery*: {int(mastery.get('overall_mastery', 0) * 100)}% overall")
        except Exception:
            pass

        # Library size
        try:
            lib_stats = await reasoning_library.get_stats()
            briefing_parts.append(f"*Reasoning library*: {lib_stats.get('total_cases', 0)} cases stored")
        except Exception:
            pass

        await push_alert({
            "type": "daily_briefing",
            "title": f"☀️ Morning briefing — {today}",
            "severity": "info",
            "body": "\n".join(briefing_parts),
            "metadata": {"date": today},
        })

        await rs.set_json(LAST_BRIEFING_KEY, {"date": today, "ts": time.time()}, ex=14 * 86400)
        return True
    except Exception as e:
        logger.warning("daily_briefing_check failed: %s", e)
        return False


# ── Public stats ────────────────────────────────────────────────────────────

async def get_proactive_stats() -> dict:
    queue = await rs.get_json(ALERT_QUEUE_KEY) or []
    baseline = await rs.get_json(ANOMALY_BASELINE_KEY) or {}
    gaps = await rs.get_json(GAP_TRACKER_KEY) or {}
    last_briefing = await rs.get_json(LAST_BRIEFING_KEY) or {}

    by_type: dict[str, int] = {}
    for a in queue:
        t = a.get("type", "unknown")
        by_type[t] = by_type.get(t, 0) + 1

    return {
        "alerts_total": len(queue),
        "alerts_unseen": sum(1 for a in queue if not a.get("seen")),
        "alerts_by_type": by_type,
        "anomaly_baseline_size": len(baseline),
        "knowledge_gaps_tracked": len(gaps),
        "last_briefing": last_briefing.get("date"),
    }
