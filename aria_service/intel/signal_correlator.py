"""ARIA Signal Correlato

r — connects the dots across intelligence sources.

The difference between a news ticker and an intelligence analyst is
CORRELATION: seeing that "Angola budget increase" + "new defence minister"
+ "SIMPORTEX tender" + "competitor Baykar absent" = HIGH PRIORITY window.

This module reads all signal sources (ledger, pipeline, contacts, knowledge,
brain hook) and produces CORRELATED INSIGHTS — compound assessments that
no single source could provide alone.

Correlation types:
  OPPORTUNITY_WINDOW  — budget + procurement + political alignment
  COMPETITIVE_VACUUM  — competitor absent/weak + active requirement
  RELATIONSHIP_LEVERAGE — warm contact + active opportunity in their org
  URGENCY_SIGNAL      — deadline approaching + lead stale + competitor active
  MARKET_HEATING      — multiple signals in same country within 7 days
  RISK_CONVERGENCE    — sanctions + compliance + political instability overlap"""
from __future__ import annotations
from .engine_wiring import wire_success, wire_failure

import hashlib
import logging
import os
import time
from datetime import datetime, timezone, timedelta
from typing import Any

logger = logging.getLogger("aria.signal_correlator")

# ── Correlation weights ───────────────────────────────────────────────────────
# Each signal type contributes to an opportunity score. When multiple
# signals converge on the same country/entity within a time window,
# the correlation engine fires a compound insight.

SIGNAL_WEIGHTS = {
    "budget_increase": 3.0,
    "new_minister": 2.5,
    "active_tender": 4.0,
    "competitor_win": -2.0,  # negative — competitor took the space
    "competitor_absent": 2.0,
    "warm_contact": 2.0,
    "pipeline_lead": 1.5,
    "procurement_signal": 3.0,
    "conflict_escalation": 1.0,  # drives urgency but also risk
    "sanctions_change": 2.0,
    "election_transition": 1.5,
    "fms_notification": 2.5,
    "defence_news": 1.0,
    "osint_signal": 0.5,
}

CORRELATION_WINDOW_DAYS = 14  # signals within 14 days are "correlated"
MIN_CORRELATION_SCORE = 5.0   # minimum score to generate an insight
MIN_SIGNALS_FOR_INSIGHT = 2   # need at least 2 different signal types


async def correlate_signals() -> list[dict]:
    """Run full cross-source correlation and return compound insights.

    Reads from:
      - intel_ledger (30-day rolling signals)
      - deal_pipeline (active leads)
      - contact_intelligence (warm contacts)
      - brain_hook stats (module activity)

    Returns a list of correlated insights, each with:
      - country, score, signals (what contributed), insight_type, recommendation
    """
    from . import intel_ledger, deal_pipeline, contact_intelligence

    insights: list[dict] = []
    now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=CORRELATION_WINDOW_DAYS)).isoformat()

    # ── Gather signals by country ─────────────────────────────────────────
    country_signals: dict[str, list[dict]] = {}

    # 1. Intel ledger signals
    try:
        ledger = await intel_ledger._load()
        for sig in ledger.get("signals", []):
            ts = sig.get("ts", "")
            if ts < cutoff:
                continue
            for country in sig.get("countries", []):
                country_lower = country.lower()
                country_signals.setdefault(country_lower, [])

                sig_type = _classify_signal(sig)
                # R-F3487 — carry PROVENANCE, not just a source label. Without
                # the url the independence check can only compare source strings,
                # and without a story fingerprint it cannot detect syndication at
                # all (same text, four domains). intel_ledger already persists
                # the url on every signal, so this costs nothing.
                _text = sig.get("text", "")
                country_signals[country_lower].append({
                    "type": sig_type,
                    "text": _text[:200],
                    "source": sig.get("source", ""),
                    "url": sig.get("url", ""),
                    "story": _story_fingerprint(_text),
                    "ts": ts,
                    "weight": SIGNAL_WEIGHTS.get(sig_type, 0.5),
                })
    except Exception as e:
        logger.debug("Ledger correlation failed: %s", e)
        # R-F2008/§21a — the intel_ledger is the PRIMARY correlation source; if it
        # can't be read the chain is degraded, so the brain must know (success is
        # already wired below). Fire-and-forget; never breaks correlation.
        try:
            wire_failure(module="signal_correlator",
                         detail=f"ledger read failed during correlation: {str(e)[:200]}",
                         gap_type="agent_cycle_failure", source="signal_correlator:correlate")
        except Exception:
            pass

    # 2. Pipeline leads
    try:
        leads = await deal_pipeline.get_pipeline()
        for lead in leads:
            country = lead.get("country", "").lower()
            if not country:
                continue
            country_signals.setdefault(country, [])
            country_signals[country].append({
                "type": "pipeline_lead",
                "text": f"[{lead.get('stage', '?')}] {lead.get('requirement', '')[:100]}",
                "source": f"pipeline:{lead.get('id', '')}",
                "ts": lead.get("created_at", ""),
                "weight": SIGNAL_WEIGHTS["pipeline_lead"],
            })
    except Exception as e:
        logger.debug("Pipeline correlation failed: %s", e)

    # 3. Warm contacts
    try:
        contacts = await contact_intelligence.get_contacts()
        for c in contacts:
            country = c.get("country", "").lower()
            status = c.get("_status", "UNKNOWN")
            if not country or status not in ("ACTIVE", "COOLING"):
                continue
            country_signals.setdefault(country, [])
            country_signals[country].append({
                "type": "warm_contact",
                "text": f"{c.get('name', '?')} at {c.get('org', '?')} ({c.get('role', '')})",
                "source": f"contact:{c.get('id', '')}",
                "ts": c.get("last_contact_date", ""),
                "weight": SIGNAL_WEIGHTS["warm_contact"],
            })
    except Exception as e:
        logger.debug("Contact correlation failed: %s", e)

    # ── Score and generate insights ───────────────────────────────────────
    for country, signals in country_signals.items():
        if len(signals) < MIN_SIGNALS_FOR_INSIGHT:
            continue

        # Unique signal types
        signal_types = set(s["type"] for s in signals)
        if len(signal_types) < MIN_SIGNALS_FOR_INSIGHT:
            continue

        total_score = sum(s["weight"] for s in signals)
        if total_score < MIN_CORRELATION_SCORE:
            continue

        insight = _generate_insight(country, signals, signal_types, total_score)
        if insight:
            insights.append(insight)

    # Sort by score descending
    insights.sort(key=lambda x: x.get("score", 0), reverse=True)

    # Brain hook
    if insights:
        try:
            from . import brain_hook
            await brain_hook.absorb(
                module="signal_generator",
                summary=f"Signal correlation: {len(insights)} compound insights across {len(country_signals)} countries. Top: {insights[0]['country']} (score {insights[0]['score']:.1f})",
                success=True,
                confidence="ASSESSED",
            )
        except Exception:
            pass

    logger.info("[correlator] %d insights from %d countries", len(insights), len(country_signals))
    return insights


def _story_fingerprint(text: str) -> str:
    """R-F3487 — stable id for the underlying STORY, so syndicated copies of one
    wire report collapse to a single origin.

    Whitespace-normalised and case-folded, so trivial reformatting between
    syndication partners does not read as a different story. Mirrors
    news_archive.content_hash; empty text yields "" so an unfingerprintable
    signal falls back to publisher-family grouping rather than inventing a
    unique origin for itself.
    """
    norm = " ".join((text or "").split()).casefold()
    if not norm:
        return ""
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()[:32]


def _classify_signal(sig: dict) -> str:
    """Classify a ledger signal into a correlation category."""
    text = (sig.get("text", "") + " " + sig.get("type", "")).lower()

    if any(kw in text for kw in ["budget", "spending", "expenditure", "allocation", "appropriation"]):
        if any(kw in text for kw in ["increase", "rise", "grow", "boost", "expand", "approve"]):
            return "budget_increase"
    if any(kw in text for kw in ["minister", "appointed", "cabinet", "reshuffle", "inaugurated"]):
        return "new_minister"
    if any(kw in text for kw in ["tender", "rfp", "rfq", "procurement", "bid", "contract award"]):
        return "active_tender"
    if any(kw in text for kw in ["baykar", "aselsan", "elbit", "norinco", "catic", "rosoboronexport"]):
        if any(kw in text for kw in ["won", "signed", "awarded", "delivered", "contract"]):
            return "competitor_win"
    if any(kw in text for kw in ["fms", "dsca", "foreign military sale"]):
        return "fms_notification"
    if any(kw in text for kw in ["sanctions", "embargo", "blacklist", "designated"]):
        return "sanctions_change"
    if any(kw in text for kw in ["election", "transition", "inaugurat", "sworn in"]):
        return "election_transition"
    if any(kw in text for kw in ["conflict", "attack", "insurgent", "military operation", "bombing"]):
        return "conflict_escalation"
    if sig.get("type") == "tender" or sig.get("type") == "bd_tender":
        return "active_tender"
    if sig.get("type") == "osint":
        return "osint_signal"
    return "defence_news"


_MIN_INDEPENDENT_ORIGINS = int(os.getenv("ARIA_CORRELATION_MIN_ORIGINS", "2"))


def _independent_origins(signals: list[dict]) -> int:
    """R-F3487 — how many INDEPENDENT sources are behind these signals.

    MARKET_HEATING fired on ``len(signals) >= 4`` alone. Combined with URL-only
    dedup upstream, four syndicated copies of one wire report — same story, four
    domains — read as a heating market. Reporting VOLUME is not evidence of
    real-world CHANGE, and telling a customer a market is heating because Reuters
    was republished four times is the exact fabrication this product exists to
    prevent (memory: "single-source = fabrication"; "one false positive destroys
    the USP").

    This delegates to dd_independent_verifier.count_independent_origins, which is
    already hardened by live evals: it is a union-find over publisher UNION story,
    so same-story/different-publisher collapses to one origin (syndication) and
    same-publisher/different-story also collapses to one (a publisher is not N
    witnesses). It is conservative by construction — the merge can only REDUCE
    the count, never inflate it.

    NOT built on intel/corroboration.py: that module has zero production callers
    and its fixtures were green while it scored 0/20 on real data. The USP gate
    must not rest on an unproven engine.

    On any failure this returns 1 (single origin), never a higher number:
    R-F3388's rule is that the false-positive rate on independence MUST be 0,
    while a conservative undercount is acceptable.
    """
    if not signals:
        return 0
    try:
        from .dd_independent_verifier import count_independent_origins
        sources = []
        for s in signals:
            loc = (s.get("url") or "").strip() or (s.get("source") or "").strip()
            story = (s.get("story") or s.get("content_hash") or "").strip()
            sources.append({"url": loc, "story": story} if story else {"url": loc})
        return int(count_independent_origins(sources))
    except Exception as exc:
        logger.warning(
            "[R-F3487] independence count failed — treating as SINGLE origin "
            "(never inflate): %s", exc)
        try:
            wire_failure(
                module="signal_correlator",
                detail=f"independent-origin count failed, collapsed to 1: {exc}"[:400],
                gap_type="engine_failure",
                source="signal_correlator:_independent_origins")
        except Exception:
            pass
        return 1


def _generate_insight(country: str, signals: list[dict], signal_types: set, score: float) -> dict | None:
    """Generate a compound insight from correlated signals."""
    # R-F3487 — computed once; every branch below reports it so a reader can see
    # WHY the insight was emitted, not just that it was.
    _origins = _independent_origins(signals)
    has_budget = "budget_increase" in signal_types
    has_tender = "active_tender" in signal_types or "procurement_signal" in signal_types
    has_minister = "new_minister" in signal_types or "election_transition" in signal_types
    has_contact = "warm_contact" in signal_types
    has_pipeline = "pipeline_lead" in signal_types
    has_competitor = "competitor_win" in signal_types
    has_fms = "fms_notification" in signal_types

    # Determine insight type
    if has_budget and has_tender and (has_minister or has_contact):
        insight_type = "OPPORTUNITY_WINDOW"
        emoji = "🟢"
        recommendation = (
            f"HIGH-PRIORITY: {country.title()} has budget momentum, active procurement, "
            f"{'and a new decision-maker creating a fresh engagement window' if has_minister else 'and a warm contact for direct approach'}. "
            f"ACT NOW — this window typically lasts 90-120 days."
        )
    elif has_tender and not has_competitor and has_contact:
        insight_type = "COMPETITIVE_VACUUM"
        emoji = "🔵"
        recommendation = (
            f"{country.title()} has active procurement with no detected competitor presence "
            f"and a warm relationship. First-mover advantage available — prepare proposal."
        )
    elif has_contact and has_pipeline:
        insight_type = "RELATIONSHIP_LEVERAGE"
        emoji = "🟡"
        recommendation = (
            f"Warm contact in {country.title()} aligns with an active pipeline lead. "
            f"Leverage the relationship to qualify and advance the deal."
        )
    elif has_competitor and has_tender:
        insight_type = "URGENCY_SIGNAL"
        emoji = "🔴"
        recommendation = (
            f"Competitor active in {country.title()} alongside live procurement. "
            f"If we don't engage now, we lose this cycle. Assess our differentiation."
        )
    elif len(signals) >= 4 and _origins >= _MIN_INDEPENDENT_ORIGINS:
        # R-F3487 — volume ALONE cannot claim a heating market. This now requires
        # the signals to span >= _MIN_INDEPENDENT_ORIGINS independent publishers,
        # so syndicated copies of one wire report can no longer clear the bar.
        insight_type = "MARKET_HEATING"
        emoji = "🟠"
        recommendation = (
            f"{country.title()} has {len(signals)} signals from {_origins} independent "
            f"sources in {CORRELATION_WINDOW_DAYS} days — market is heating. "
            f"Review pipeline coverage and contact freshness."
        )
    elif len(signals) >= 4:
        # Enough volume, but it traces back to a single origin. Report it
        # HONESTLY rather than suppressing it or inflating it: the operator still
        # wants to know the story exists, and must not be told it is corroborated.
        insight_type = "SINGLE_ORIGIN_REPORTS"
        emoji = "⚪"
        recommendation = (
            f"{country.title()}: multiple reports trace to {_origins or 1} independent "
            f"source. This is reporting volume, NOT corroborated change — treat as "
            f"a lead to verify, not as evidence. Seek a second independent source "
            f"before acting."
        )
    elif "sanctions_change" in signal_types and ("conflict_escalation" in signal_types or has_tender):
        insight_type = "RISK_CONVERGENCE"
        emoji = "⚠️"
        recommendation = (
            f"Compliance + instability signals in {country.title()}. "
            f"Review export control implications before advancing any deals."
        )
    else:
        # R-F2008 — this cluster ALREADY passed the score >= MIN_CORRELATION_SCORE
        # and >= 2-signal-type gate in correlate_signals(). The old `return None`
        # here silently DROPPED genuine opportunities that didn't match a specific
        # multi-dimensional pattern — e.g. a budget increase + active tender with
        # no warm contact yet (a textbook live window). The end-to-end chain test
        # caught exactly this: news -> ledger -> correlate produced 0 insights for
        # Angola despite budget+tender scoring 7.0. Never drop a gate-passing
        # cluster; the missing relationship is the action item, not a reason to hide.
        if has_budget and has_tender:
            insight_type, emoji = "OPPORTUNITY_WINDOW", "🟢"
            recommendation = (
                f"{country.title()} shows budget momentum + active procurement "
                f"({len(signals)} signals) but no warm contact detected yet — "
                f"qualify the buyer and open a channel NOW; this is a live window."
            )
        else:
            insight_type, emoji = "MARKET_SIGNAL", "🟠"
            recommendation = (
                f"{country.title()}: {len(signals)} correlated signals across "
                f"{len(signal_types)} types (score {round(score, 1)}) — review for opportunity."
            )

    return {
        "country": country.title(),
        "score": round(score, 1),
        "insight_type": insight_type,
        "emoji": emoji,
        "recommendation": recommendation,
        "signal_count": len(signals),
        # R-F3487 — the independence evidence, on EVERY insight. signal_count is
        # how many reports; independent_origins is how many witnesses. Publishing
        # only the first is what let volume masquerade as corroboration.
        "independent_origins": _origins,
        "independently_corroborated": _origins >= _MIN_INDEPENDENT_ORIGINS,
        "signal_types": sorted(signal_types),
        "signals": [{"type": s["type"], "text": s["text"][:150], "source": s["source"]} for s in signals[:10]],
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


# ── Format for injection into chat context ────────────────────────────────────

async def get_correlation_context(query: str, max_insights: int = 3) -> str:
    """Generate correlation context for a chat query.

    Called by aria_engine before the LLM call. Returns formatted
    correlation insights relevant to the query's country/entity.
    """
    insights = await correlate_signals()
    if not insights:
        return ""

    # Filter to relevant country if mentioned in query
    query_lower = query.lower()
    relevant = [i for i in insights if i["country"].lower() in query_lower]
    if not relevant:
        # Fall back to top insights
        relevant = insights[:max_insights]
    else:
        relevant = relevant[:max_insights]

    if not relevant:
        return ""

    lines = ["CORRELATED INTELLIGENCE — signals connected across sources:"]
    for insight in relevant:
        lines.append(
            f"  {insight['emoji']} [{insight['insight_type']}] {insight['country']} "
            f"(score {insight['score']}, {insight['signal_count']} signals): "
            f"{insight['recommendation']}"
        )
    return "\n".join(lines)


# ── Format for daily briefing ─────────────────────────────────────────────────

async def assess_coverage_confidence(country: str) -> dict:
    """Assess how much intelligence ARIA has on a given country.

    Returns a confidence score (0-1) and a breakdown showing:
    - RAG document count
    - Ledger signal count
    - Knowledge fact count
    - Contact count
    - Source freshness
    - Coverage verdict: DEEP / ADEQUATE / THIN / ZERO

    This is injected into chat responses so ARIA never presents thin
    coverage as deep intelligence without warning.
    """
    from . import rag_store, intel_ledger, knowledge, contact_intelligence

    country_lower = country.lower().strip()
    score = 0.0
    breakdown = {}

    # 1. RAG documents mentioning this country
    try:
        results = await rag_store.search(country, top_k=20)
        doc_count = len(results)
        breakdown["rag_documents"] = doc_count
        score += min(doc_count / 20, 1.0) * 0.3  # max 0.3 from RAG
    except Exception:
        breakdown["rag_documents"] = 0

    # 2. Ledger signals for this country
    try:
        ledger = await intel_ledger._load()
        signals = [s for s in ledger.get("signals", [])
                   if country_lower in " ".join(s.get("countries", [])).lower()]
        breakdown["ledger_signals"] = len(signals)
        score += min(len(signals) / 30, 1.0) * 0.25  # max 0.25 from ledger
    except Exception:
        breakdown["ledger_signals"] = 0

    # 3. Knowledge facts
    try:
        facts = knowledge.search_knowledge(country)
        fact_count = len(facts) if isinstance(facts, list) else 0
        breakdown["knowledge_facts"] = fact_count
        score += min(fact_count / 10, 1.0) * 0.2  # max 0.2 from KB
    except Exception:
        breakdown["knowledge_facts"] = 0

    # 4. Contacts in country
    try:
        contacts = await contact_intelligence.get_contacts(country=country)
        breakdown["contacts"] = len(contacts)
        score += min(len(contacts) / 5, 1.0) * 0.15  # max 0.15 from contacts
    except Exception:
        breakdown["contacts"] = 0

    # 5. Pipeline leads
    try:
        from . import deal_pipeline
        leads = await deal_pipeline.get_pipeline(country=country)
        breakdown["pipeline_leads"] = len(leads)
        score += min(len(leads) / 3, 1.0) * 0.1  # max 0.1 from pipeline
    except Exception:
        breakdown["pipeline_leads"] = 0

    # Determine verdict
    if score >= 0.7:
        verdict = "DEEP"
    elif score >= 0.4:
        verdict = "ADEQUATE"
    elif score >= 0.15:
        verdict = "THIN"
    else:
        verdict = "ZERO"

    return {
        "country": country,
        "score": round(score, 2),
        "verdict": verdict,
        "breakdown": breakdown,
        "warning": (
            f"⚠️ THIN COVERAGE: ARIA has limited intelligence on {country}. "
            f"Data quality may be insufficient for client-facing use. "
            f"Recommend fresh research before briefing."
        ) if verdict in ("THIN", "ZERO") else "",
    }


async def generate_correlation_briefing() -> str:
    """Generate correlation section for the daily team briefing."""
    insights = await correlate_signals()
    if not insights:
        return ""

    lines = [
        "",
        "*🔗 CORRELATED INTELLIGENCE*",
        f"_{len(insights)} compound insight(s) detected:_",
    ]
    for i in insights[:5]:
        lines.append(f"{i['emoji']} *{i['country']}* — {i['insight_type']} (score {i['score']})")
        lines.append(f"  {i['recommendation'][:200]}")
        lines.append("")


    # R-F996 — wire to brain
    wire_success(
        module="signal_correlator",
        summary="Signal correlation",
        source_id="signal_correlator:R-F996",
    )
    return "\n".join(lines)
