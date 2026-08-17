"""Style learner — ARIA improves her reading, writing, and response
formulation by observing her own highest-quality outputs.

Every hour, this module scans recent ARIA replies that the honesty
judge flagged `grounded` AND the verification gate returned
`CRITICAL_VERIFIED` (or NORMAL severity with high grounded_rate).
From those gold-standard replies it extracts STRUCTURAL PATTERNS:

  - BLUF shapes: how ARIA opens a verdict (🔴 BOTTOM LINE — ... /
    🟡 BOTTOM LINE — ... / CONFIRMED — ... etc.)
  - Section headings she uses (📋 DOCUMENT ANALYSIS, ⚠️ COMPLIANCE
    FLAGS, ✅ RECOMMENDED ACTION, 📅 NEXT STEP, etc.)
  - Citation styles that the verifier accepted as grounded
    ([from X], [CONFIRMED — from X], [from snippet #N])
  - Recommendation framings that got approved (Within N hours ...,
    Immediate stop ..., Cease engagement ...)
  - Opening tones by topic (compliance vs BD vs technical)

These go into a Redis-backed style corpus keyed by topic+context type.
When a similar future query arrives, the corpus is surfaced as a
few-shot style reference in the chat context — so ARIA reads her own
best output as a template before drafting the new reply.

LLM-free. Pure Python regex + counting + ranking. Runs hourly at :35
past (deduped cadence with other learning tasks).

Side effects:
  - crucix:learning:style:exemplars      {topic_context: [exemplar, ...]}
  - crucix:learning:style:stats_24h      counts
"""
from __future__ import annotations

import hashlib
import logging
import re
from collections import Counter
from datetime import datetime, timezone, timedelta
from typing import Any

logger = logging.getLogger("aria.learning.style_learner")



_REDIS_EXEMPLARS_KEY = "crucix:learning:style:exemplars"
_REDIS_STATS_KEY     = "crucix:learning:style:stats_24h"

# How many exemplars to retain per topic slot
_MAX_EXEMPLARS_PER_TOPIC = 8
_MAX_REPLIES_PER_RUN     = 60
_LOOKBACK_HOURS          = 24


# ═══════════════════════════════════════════════════════════════════════
# Pattern extractors
# ═══════════════════════════════════════════════════════════════════════

_BLUF_RE = re.compile(
    r"(?:🔴|🟡|🟢|🟠|✅|⚠️|\[CONFIRMED\]|\[ASSESSED\])\s*BOTTOM\s*LINE\s*[—\-]\s*(.{20,240})",
    re.IGNORECASE,
)
_HEADING_RE = re.compile(
    r"^\s*([🔴🟡🟢🟠✅⚠️📋📅📄🔍💼🧭📎🛡️🛑]\s*[A-Z][A-Z0-9 &/]{3,80})",
    re.MULTILINE,
)
_CITATION_RE = re.compile(
    r"\[(?:from\s+[^\]]{3,120}|CONFIRMED\s*—\s*from\s+[^\]]{3,120}|from\s+snippet\s+#\d+|RAG)\]",
    re.IGNORECASE,
)
_RECOMMENDATION_OPENER_RE = re.compile(
    r"^\s*(?:✅|🛑|📅|➤|•|-)?\s*"
    r"(IMMEDIATE\s+STOP|CEASE\s+ENGAGEMENT|Within\s+\d+\s+\w+|DO\s+NOT\s+(?:PROCEED|ENGAGE)|"
    r"PROCEED\s+WITH\s+ENHANCED\s+DD|APPROVED\s+TO\s+PROCEED|PROCEED\s+WITH\s+CAUTION|"
    r"HALT\s+AND\s+REVIEW|ESCALATE\s+TO)",
    re.IGNORECASE | re.MULTILINE,
)
_CONFIDENCE_FOOTER_RE = re.compile(
    r"Confidence\s*:\s*\[([A-Z]+)\].*?Risk\s*:\s*([A-Z-]+)",
    re.IGNORECASE | re.DOTALL,
)


# ═══════════════════════════════════════════════════════════════════════
# Collection — pull recent high-quality replies
# ═══════════════════════════════════════════════════════════════════════

_last_collection_diag: dict[str, Any] = {}
# R-F4103 (C-147) — the unreachable-gate signal is announced once per process;
# STYLE-LEARN-HOURLY fires hourly and the condition is persistent.
_gate_gap_announced = False


async def _collect_gold_replies(lookback_hours: int) -> list[dict[str, Any]]:
    """Return recent ARIA replies that are safe to learn style from.
    Filters: grounded verification verdict AND len > 200 chars AND
    no quarantine-citation marker in text."""
    out: list[dict[str, Any]] = []
    since = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
    # Per-filter counts so 0 exemplars on the dashboard can be explained.
    filtered = {
        "total_entries": 0,
        "not_dict": 0,
        "too_short": 0,
        "not_grounded": 0,
        "quarantined": 0,
        "before_window": 0,
        "kept": 0,
    }
    try:
        from ..intel import chat_audit_log as cal
        if not hasattr(cal, "get_recent"):
            _last_collection_diag.update({"error": "chat_audit_log.get_recent missing"})
            logger.warning("[style_learner] chat_audit_log.get_recent missing")
            return out
        entries = await cal.get_recent(limit=200)
    except Exception as exc:
        _last_collection_diag.update({"error": f"{type(exc).__name__}: {exc}"})
        logger.warning("[style_learner] chat_audit load failed: %s", exc)
        return out

    filtered["total_entries"] = len(entries or [])
    for e in entries or []:
        if not isinstance(e, dict):
            filtered["not_dict"] += 1
            continue
        reply = e.get("response") or e.get("reply") or ""
        if not reply or len(reply) < 200:
            filtered["too_short"] += 1
            continue
        # chat_audit_log.record_chat writes `verification_status`; older
        # code wrote `honesty_verdict`. Accept either so the filter
        # doesn't silently reject every entry when the field is renamed.
        verdict = (
            (e.get("verification_status") or e.get("honesty_verdict") or "").lower()
        )
        if verdict != "grounded":
            filtered["not_grounded"] += 1
            continue
        if "CITATION-QUARANTINED" in reply:
            filtered["quarantined"] += 1
            continue
        # Timestamp filter — tolerant
        ts = e.get("timestamp") or e.get("ts") or ""
        try:
            if isinstance(ts, (int, float)):
                dt = datetime.fromtimestamp(float(ts), tz=timezone.utc)
            elif isinstance(ts, str):
                dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            else:
                dt = datetime.now(timezone.utc)
        except Exception:
            dt = datetime.now(timezone.utc)
        if dt < since:
            filtered["before_window"] += 1
            continue
        filtered["kept"] += 1
        out.append({
            "reply": reply,
            "user": e.get("user_message") or e.get("message") or "",
            "timestamp": dt.isoformat(),
            "trace_id": (e.get("trace_id") or "")[:16],
        })
    _last_collection_diag.clear()
    _last_collection_diag.update({"error": None, "filtered": filtered})
    if filtered["kept"] == 0 and filtered["total_entries"] > 0:
        logger.warning(
            "[style_learner] %d entries scanned but 0 kept — filtered: %s",
            filtered["total_entries"], filtered,
        )
        # ── R-F4103 (C-147) §21a/§21d — this module was DARK. ────────────────
        # It has scanned 200 entries and kept 0 every hour for weeks (live
        # 2026-08-17: not_grounded 200/200, against a live status mix of
        # no_claims 78 / unverified 34 / well_formed 8 / grounded 0), and the
        # only trace was the log line above. A learning loop that cannot learn
        # was therefore invisible to the self-heal loop that exists to notice
        # exactly this.
        #
        # Announced ONCE per process: STYLE-LEARN-HOURLY fires hourly and the
        # condition is persistent, so a per-run signal is the
        # sanctions_coverage_degraded flood shape.
        #
        # NOT fixed by widening the gate to accept `well_formed` — that changes
        # what ARIA learns style from, which is an operator quality decision,
        # not a way to silence a warning (§1: root cause, not symptom).
        global _gate_gap_announced
        if not _gate_gap_announced:
            _gate_gap_announced = True
            _dominant = max(
                ((k, v) for k, v in filtered.items()
                 if k not in ("total_entries", "kept") and v),
                key=lambda kv: kv[1], default=("unknown", 0),
            )
            try:
                from ..intel.engine_wiring import wire_failure
                wire_failure(
                    module="style_learner",
                    detail=(
                        f"style gate is UNREACHABLE: {filtered['total_entries']} "
                        f"entries scanned, 0 kept; dominant rejection "
                        f"{_dominant[0]}={_dominant[1]}. The gate requires "
                        f"verification_status == 'grounded'; live traffic "
                        f"produces none, so this loop cannot learn. Widening "
                        f"the gate is an operator quality decision."
                    ),
                    gap_type="missing_capability",
                    source="style_learner:_collect_gold_replies:R-F4103",
                )
            except Exception:   # observability must never break collection
                pass
    return out[:_MAX_REPLIES_PER_RUN]


# ═══════════════════════════════════════════════════════════════════════
# Topic classification — rule-based, LLM-free
# ═══════════════════════════════════════════════════════════════════════

def _classify_topic(user_msg: str, reply: str) -> str:
    """Rough topic assignment so style exemplars cluster by context.
    Must match topics that matter for style: DD / compliance / chat /
    assessment / procurement / technical.

    Ordering matters: technical matches (STANAG ref, NSN, spec) must
    run BEFORE 'assessment' (which also keys on 'stanag' bare) so that
    a query mentioning STANAG-REF doesn't mis-cluster as an assessment.
    """
    blob = (user_msg + " " + reply[:600]).lower()
    if any(k in blob for k in ("dd on", "due diligence", "dd_orchestrate", "run a dd", "risk classification", "ghost score")):
        return "dd_report"
    if any(k in blob for k in ("sanctioned", "ofac", "ofsi", "embargo", "bright-line", "compliance", "ukba", "fcpa")):
        return "compliance"
    # Technical BEFORE assessment — both key on "stanag" but technical
    # is the more specific cluster (references, NSN numbers, specs).
    if any(k in blob for k in ("stanag ref", "stanag reference", "nsn ",
                                "nsn:", "specification", "acceptance criteria",
                                "mtbf", "stanag 4")):
        return "technical"
    if any(k in blob for k in ("itt", "rfi", "rfp", "bafo", "procurement paper", "tender")):
        return "procurement"
    if any(k in blob for k in ("stanag", "assessment", "key judgement", "alternative hypothesis",
                                "intelligence assessment", "bluf")):
        return "assessment"
    return "chat"


# ═══════════════════════════════════════════════════════════════════════
# Pattern extractors — operate on reply text, return structured dict
# ═══════════════════════════════════════════════════════════════════════

def extract_patterns(reply_text: str) -> dict[str, Any]:
    """Extract the structural style patterns from a single reply."""
    patterns: dict[str, Any] = {
        "bluf":               None,
        "headings":           [],
        "citation_count":     0,
        "citation_samples":   [],
        "recommendation":     None,
        "confidence_footer":  None,
        "char_count":         len(reply_text),
        "paragraph_count":    reply_text.count("\n\n") + 1,
    }

    m = _BLUF_RE.search(reply_text)
    if m:
        patterns["bluf"] = m.group(1).strip()[:240]

    for hm in _HEADING_RE.findall(reply_text):
        patterns["headings"].append(hm.strip())
        if len(patterns["headings"]) >= 8:
            break

    citations = _CITATION_RE.findall(reply_text)
    patterns["citation_count"] = len(citations)
    patterns["citation_samples"] = citations[:5]

    rm = _RECOMMENDATION_OPENER_RE.search(reply_text)
    if rm:
        patterns["recommendation"] = rm.group(1).upper()

    cf = _CONFIDENCE_FOOTER_RE.search(reply_text)
    if cf:
        patterns["confidence_footer"] = f"[{cf.group(1).upper()}] / Risk: {cf.group(2).upper()}"

    return patterns


# ═══════════════════════════════════════════════════════════════════════
# Exemplar storage (Redis)
# ═══════════════════════════════════════════════════════════════════════

async def _load_exemplars() -> dict[str, list[dict[str, Any]]]:
    try:
        from ..intel import redis_store as rs
        data = await rs.get_json(_REDIS_EXEMPLARS_KEY)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {}


async def _save_exemplars(data: dict[str, list[dict[str, Any]]]) -> None:
    try:
        from ..intel import redis_store as rs
        await rs.set_json(_REDIS_EXEMPLARS_KEY, data)
    except Exception as exc:
        logger.debug("exemplars save failed: %s", exc)


def _exemplar_hash(reply: str, user: str) -> str:
    return hashlib.sha1((user[:200] + "||" + reply[:400]).encode("utf-8", errors="ignore"), usedforsecurity=False).hexdigest()[:12]


# ═══════════════════════════════════════════════════════════════════════
# Public runner
# ═══════════════════════════════════════════════════════════════════════

async def run_hourly_style_learn() -> dict[str, Any]:
    """One tick: collect → classify → extract → rank → persist."""
    gold = await _collect_gold_replies(_LOOKBACK_HOURS)
    if not gold:
        return {"replies_scanned": 0, "exemplars_added": 0, "topics": {}}

    exemplars = await _load_exemplars()
    added_count = 0
    topic_counts: Counter[str] = Counter()

    for g in gold:
        topic = _classify_topic(g["user"], g["reply"])
        topic_counts[topic] += 1
        patterns = extract_patterns(g["reply"])
        # Skip replies that have NO structural patterns — probably too
        # short / conversational to learn style from
        if (not patterns["bluf"]
                and not patterns["headings"]
                and patterns["citation_count"] == 0):
            continue

        slot = exemplars.setdefault(topic, [])
        exemplar_id = _exemplar_hash(g["reply"], g["user"])
        if any(e.get("id") == exemplar_id for e in slot):
            continue
        slot.append({
            "id":              exemplar_id,
            "captured_at":     g["timestamp"],
            "trace_id":        g["trace_id"],
            "user_preview":    (g["user"] or "")[:120],
            "bluf":            patterns["bluf"],
            "headings":        patterns["headings"],
            "citation_count":  patterns["citation_count"],
            "citation_samples": patterns["citation_samples"],
            "recommendation":  patterns["recommendation"],
            "confidence_footer": patterns["confidence_footer"],
            "char_count":      patterns["char_count"],
            "paragraph_count": patterns["paragraph_count"],
            # A SHORT slice of the reply as the style template. Trim to
            # the first 1200 chars (the BLUF + first two sections — this
            # is the "skeleton" we want to imitate, not the full reply).
            "template_slice":  g["reply"][:1200],
        })
        # Keep only the freshest N per topic
        slot.sort(key=lambda x: x.get("captured_at", ""), reverse=True)
        exemplars[topic] = slot[:_MAX_EXEMPLARS_PER_TOPIC]
        added_count += 1

    await _save_exemplars(exemplars)

    # 24h stats. Use keepttl on subsequent writes -- previous code passed
    # ex=86400 on every hourly run, resetting the 24h window each time
    # so the counter never expired and `replies_scanned_24h` /
    # `exemplars_added_24h` accumulated as lifetime tallies. Same pattern
    # as f981c0a (output_harvester).
    try:
        from ..intel import redis_store as rs
        existing = await rs.get_json(_REDIS_STATS_KEY)
        is_fresh = not isinstance(existing, dict)
        stats = existing if isinstance(existing, dict) else {}
        stats["replies_scanned"] = stats.get("replies_scanned", 0) + len(gold)
        stats["exemplars_added"] = stats.get("exemplars_added", 0) + added_count
        stats["by_topic"] = dict(topic_counts)
        stats["last_run"] = datetime.now(timezone.utc).isoformat()
        stats["total_exemplars"] = sum(len(v) for v in exemplars.values())
        if is_fresh:
            await rs.set_json(_REDIS_STATS_KEY, stats, ex=86400)
        else:
            await rs.set_json(_REDIS_STATS_KEY, stats, keepttl=True)
    except Exception:
        pass

    # brain_hook
    try:
        from ..intel import brain_hook
        await brain_hook.absorb(
            module="style_learner",
            summary=(
                f"Style tick — scanned {len(gold)} grounded replies, "
                f"added {added_count} exemplars across "
                f"{len(topic_counts)} topics"
            ),
            success=added_count > 0,
        )
    except Exception:
        pass

    logger.info(
        "[style_learner] replies=%d added=%d topics=%s",
        len(gold), added_count, dict(topic_counts),
    )
    return {
        "replies_scanned": len(gold),
        "exemplars_added": added_count,
        "topics": dict(topic_counts),
        "total_exemplars": sum(len(v) for v in exemplars.values()),
    }


# ═══════════════════════════════════════════════════════════════════════
# Consumption — used by chat pipeline to inject style guidance
# ═══════════════════════════════════════════════════════════════════════

async def get_style_prompt_addendum(user_message: str, max_chars: int = 2000) -> str:
    """Return a short prompt addendum showing 1 exemplar of the
    matching topic, for few-shot style grounding. Returns empty string
    when no exemplar available."""
    topic = _classify_topic(user_message, "")
    exemplars = await _load_exemplars()
    slot = exemplars.get(topic) or []
    if not slot:
        return ""
    e = slot[0]  # freshest exemplar
    template = (e.get("template_slice") or "").strip()
    if not template:
        return ""
    preview = template[: max_chars - 200]
    return (
        f"\n[STYLE EXEMPLAR — prior grounded {topic} reply, use as a "
        f"structural template for this response. Replicate the BLUF "
        f"shape, section headings, and citation format. DO NOT copy "
        f"content — only structure.]\n{preview}\n[END STYLE EXEMPLAR]\n"
    )


async def get_stats() -> dict[str, Any]:
    try:
        from ..intel import redis_store as rs
        stats = await rs.get_json(_REDIS_STATS_KEY) or {}
        exemplars = await rs.get_json(_REDIS_EXEMPLARS_KEY) or {}
    except Exception:
        stats = {}
        exemplars = {}
    by_topic = {k: len(v) for k, v in (exemplars or {}).items() if isinstance(v, list)}
    return {
        "replies_scanned_24h": stats.get("replies_scanned", 0),
        "exemplars_added_24h": stats.get("exemplars_added", 0),
        "total_exemplars":     stats.get("total_exemplars", sum(by_topic.values())),
        "by_topic":            by_topic,
        "last_run":            stats.get("last_run", ""),
        "last_collection_diag": dict(_last_collection_diag),
    }


def summary() -> dict[str, Any]:
    return {
        "max_exemplars_per_topic": _MAX_EXEMPLARS_PER_TOPIC,
        "max_replies_per_run":     _MAX_REPLIES_PER_RUN,
        "lookback_hours":          _LOOKBACK_HOURS,
    }
