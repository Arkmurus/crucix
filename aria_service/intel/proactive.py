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
READING_QUEUE_KEY = "crucix:aria:proactive:reading_queue"
READING_DEDUPE_KEY = "crucix:aria:proactive:reading_dedupe:{topic}"
READING_DEDUPE_TTL_S = 6 * 3600
# R-F163 (2026-05-11): de-dup alert text so we don't show the same
# 'studying weak topics' three times across 14h. The same sorted set
# of weak tags will hash to the same key; we only fire when the set
# (or its top-3 head) actually changes.
LAST_MASTERY_PREP_HASH_KEY = "crucix:aria:proactive:last_mastery_prep_hash"


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
    """Look at student mastery, find the weakest topics, queue + announce.

    R-F163 (2026-05-11): the previous implementation only pushed an alert
    naming the weak topics, while `student.reading_session()` separately
    picked from `TOPICS + CORE_MASTERY_TAGS` and ignored anything else.
    Result: region-specific tags like `angola_procurement`, `uk_compliance`,
    `uk_export_control` were announced as 'studying' every 6h but never
    actually studied, so they stayed stuck at 47% forever and the same
    alert text repeated indefinitely.

    Three corrections:
      1. Split weak tags into 'studyable' (in TOPICS + CORE_MASTERY_TAGS,
         so reading_session can lift them) vs 'starved' (everything else).
      2. Bias the ordering by RECENT WORK — recent DD jurisdictions / chat
         topics get pushed up so today's queue reflects what the operator
         actually touched, not just the global mastery floor.
      3. QUEUE the topics into READING_QUEUE_KEY (which reading_session
         now drains via R-F163's consumer side). Announce the queued list
         in the alert, NOT the un-actionable starved list.
      4. De-dup the alert: if the studyable top-3 hasn't changed since
         the last fire, skip the push (we still do the queueing work).

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
        weak = mastery_report.get("weak_topics", []) or []
        if not weak:
            return 0

        # Split by studyability. reading_session() picks from this pool:
        studyable_pool = set(student.TOPICS) | set(student.CORE_MASTERY_TAGS)
        studyable = [t for t in weak if t in studyable_pool]
        starved = [t for t in weak if t not in studyable_pool]

        # ── Recent-activity boost ──
        # Pull last-48h DD entities + chat-audit topics so today's queue
        # reflects what the operator actually worked on, not the global
        # mastery floor. Each priority tag found in recent work is moved
        # to the front of the studyable list.
        recent_signals = await _collect_recent_activity_signals()
        boosted = [t for t in studyable if t in recent_signals]
        rest = [t for t in studyable if t not in recent_signals]
        studyable = boosted + rest

        # Queue up to 5 studyable tags so reading_session has work to do.
        queued = []
        for t in studyable[:5]:
            try:
                r = await queue_reading_session(
                    t, reason="mastery_prep_R-F163", severity="MEDIUM"
                )
                if r.get("queued"):
                    queued.append(t)
            except Exception as _qe:
                logger.debug("queue_reading_session failed for %s: %s", t, _qe)

        # De-dup the alert: hash the announced set (top-3 studyable).
        import hashlib as _hl
        announce_list = studyable[:3] if studyable else (starved[:3] if starved else [])
        ann_hash = _hl.sha1(
            ",".join(sorted(announce_list)).encode("utf-8")
        ).hexdigest()[:12]
        last_hash = (await rs.get(LAST_MASTERY_PREP_HASH_KEY) or "").strip()
        if ann_hash == last_hash:
            logger.info(
                "[proactive] R-F163 mastery_prep alert suppressed (same as last fire); "
                "queued=%d studyable=%d starved=%d",
                len(queued), len(studyable), len(starved),
            )
            # Still rate-limit so we don't recompute hourly.
            await rs.set_json(
                LAST_MASTERY_PREP_KEY,
                {"ts": now, "weak": weak, "suppressed": True},
                ex=14 * 86400,
            )
            return len(queued)

        # Build alert text that ONLY names tags reading_session can lift.
        body_parts = []
        if studyable:
            body_parts.append(
                "Studying " + ", ".join(studyable[:5])
                + (f" (+{len(studyable) - 5} more)" if len(studyable) > 5 else "")
                + "."
            )
        if starved:
            body_parts.append(
                "Tags reading_session can't lift directly (region-specific): "
                + ", ".join(starved[:5])
                + (f" (+{len(starved) - 5} more)" if len(starved) > 5 else "")
                + ". A targeted web_search branch (R-F163) ingests these."
            )
        body_parts.append("Cadence: every 6h.")

        title = (
            f"🎓 Studying: {', '.join(announce_list)}"
            if announce_list else "🎓 Mastery prep tick"
        )

        await push_alert({
            "type": "mastery_prep",
            "title": title,
            "severity": "info",
            "body": " ".join(body_parts),
            "metadata": {
                "weak_topics": weak,
                "studyable": studyable,
                "starved": starved,
                "queued": queued,
                "recent_signals": list(recent_signals),
            },
        })

        await rs.set_json(
            LAST_MASTERY_PREP_KEY,
            {"ts": now, "weak": weak, "queued": queued},
            ex=14 * 86400,
        )
        try:
            await rs.set(LAST_MASTERY_PREP_HASH_KEY, ann_hash, ex=14 * 86400)
        except Exception:
            pass

        return len(queued) or len(weak)
    except Exception as e:
        logger.warning("prepare_weak_topics failed: %s", e)


async def _collect_recent_activity_signals() -> set[str]:
    """R-F163: scan last-48h DD reports + chat audit for topic/tag signals
    we can use to bias the weak-topic queue. Returns a set of tag strings
    that intersect with student.TOPICS + CORE_MASTERY_TAGS.

    Cheap & best-effort — never raises. If both lookups fail, returns
    empty and the caller falls back to mastery-only ordering.
    """
    signals: set[str] = set()
    try:
        from . import dd_orchestrator
        reports = await dd_orchestrator.list_reports(limit=20)
        # Tag jurisdictions onto market_intel / geopolitics — these are
        # the studyable tags whose RSS pool already covers the regions
        # named in DD jurisdictions.
        for r in reports or []:
            j = (r.get("jurisdiction") or "").lower()
            if not j:
                continue
            if any(k in j for k in ("angola", "mozambique", "brazil", "portugal", "cape verde", "são tomé", "guinea-bissau", "lusophone")):
                signals.update({"market_intel", "geopolitics", "lang:pt"})
            if any(k in j for k in ("russia", "ukraine", "belarus")):
                signals.update({"geopolitics", "lang:ru", "sanctions"})
            if any(k in j for k in ("china", "taiwan", "hong kong")):
                signals.update({"geopolitics", "lang:zh", "strategic_geography"})
            if any(k in j for k in ("turkey", "türkiye", "syria")):
                signals.update({"geopolitics", "nato_standards"})
            if any(k in j for k in ("saudi", "uae", "gulf", "iran", "qatar", "oman", "egypt")):
                signals.update({"geopolitics", "lang:ar", "strategic_geography"})
    except Exception as e:
        logger.debug("_collect_recent_activity_signals: DD scan failed: %s", e)

    try:
        from . import chat_audit_log
        # Last-100 audit entries — chat_audit_log.get_recent doesn't take
        # an hours filter, so we cap at 100 entries (typical 24-48h window
        # at current chat volume). Tag patterns from there indicate what
        # the operator actually queried recently.
        recent = await chat_audit_log.get_recent(limit=100)
        cutoff = time.time() - 48 * 3600
        for ent in recent or []:
            ts = ent.get("ts") or ent.get("timestamp") or 0
            try:
                if isinstance(ts, str):
                    ts = datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
                if ts and ts < cutoff:
                    continue
            except Exception:
                pass
            tags = ent.get("tags") or ent.get("topics") or []
            if isinstance(tags, list):
                for t in tags:
                    if isinstance(t, str):
                        signals.add(t)
    except Exception as e:
        logger.debug("_collect_recent_activity_signals: chat audit failed: %s", e)

    return signals
        return 0


# ── Reading-session queue (called by ecosystem_reassess) ──────────────────
#
# ecosystem_reassess's mastery-drift branch expects `queue_reading_session`
# to enqueue a topic for the student/research loops to pick up. Before
# 2026-04-20 this function didn't exist; the `hasattr()` gate in
# core_develop silently skipped the whole drift-reading pipeline, so
# regressing topics were flagged but never actually studied.
#
# Implementation:
#   - dedupe per topic for 6h (prevents ecosystem_reassess from enqueueing
#     the same topic every hour it runs)
#   - push onto a Redis list that the existing student/reading loops
#     already consume (they look for topics to study)
#   - also record a pending_action so the operator briefing surfaces
#     that ARIA is auto-studying X

async def queue_reading_session(
    topic: str,
    *,
    reason: str = "mastery_drift",
    severity: str = "MEDIUM",
) -> dict:
    """Enqueue a reading session for `topic`. Dedup'd per-topic for 6h
    so ecosystem_reassess's hourly runs don't flood the queue. Returns
    {"queued": bool, "reason": str, "topic": str}."""
    topic = (topic or "").strip()
    if not topic:
        return {"queued": False, "reason": "empty_topic", "topic": ""}
    # Dedup gate
    dkey = READING_DEDUPE_KEY.format(topic=topic.lower().replace(" ", "_")[:80])
    try:
        if await rs.get(dkey):
            return {"queued": False, "reason": "dedup_6h", "topic": topic}
    except Exception:
        pass
    entry = {
        "topic": topic,
        "reason": reason,
        "severity": severity,
        "queued_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        import json as _json
        await rs.lpush(READING_QUEUE_KEY, _json.dumps(entry, default=str))
        await rs.ltrim(READING_QUEUE_KEY, 0, 199)
        await rs.set(dkey, "1", ex=READING_DEDUPE_TTL_S)
    except Exception as e:
        logger.warning("queue_reading_session persist failed: %s", e)
        return {"queued": False, "reason": f"persist_failed:{e}", "topic": topic}
    # Record to pending_actions so operator briefing surfaces it
    try:
        from . import pending_actions
        await pending_actions.record(
            promise=f"Study weak topic: {topic}",
            reason=f"Queued by {reason} — ARIA will fold this into the next reading cycle.",
            resolver_kind="autonomous_task",
            resolver_ref="STUDENT-READING-CYCLE",
            severity=severity,
            source="ecosystem_reassess",
        )
    except Exception:
        pass  # pending_actions is advisory; don't block the queue write
    return {"queued": True, "reason": "ok", "topic": topic}


async def get_reading_queue(limit: int = 50) -> list[dict]:
    """Return pending reading sessions, newest first. Used by the student
    loop to pick up topics flagged by ecosystem_reassess."""
    import json as _json
    try:
        raw = await rs.lrange(READING_QUEUE_KEY, 0, max(0, limit - 1))
    except Exception as e:
        logger.debug("get_reading_queue read failed: %s", e)
        return []
    out: list[dict] = []
    for r in raw or []:
        try:
            out.append(_json.loads(r) if isinstance(r, str) else r)
        except Exception:
            continue
    return out


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
            headline = mastery.get('headline_mastery', mastery.get('overall_mastery', 0))
            briefing_parts.append(f"*ARIA mastery*: {int(headline * 100)}% (min of weighted + core)")
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
