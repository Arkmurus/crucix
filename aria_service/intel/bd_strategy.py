"""R-F1053 — ARIA BD Strategy Engine.

Transforms raw intelligence into actionable business development strategy.
Synthesizes signals from all sources into:
  1. Market Opportunity Analysis — which markets to enter, when, how
  2. Competitor Intelligence — who is winning, why, what they're doing
  3. Deal Flow Optimization — which deals to prioritize, how to position
  4. Risk-Adjusted Recommendations — go/no-go with confidence levels
  5. Strategic Narrative — the story that wins the deal

Data sources consumed:
  - intel_ledger (all signals)
  - deal_pipeline (active deals)
  - competitor_tracker (competitor moves)
  - tender_monitor (procurement opportunities)
  - news_monitor (real-time news)
  - narrative_monitor (cognitive warfare detection)
  - sanctions_propagation (sanctions risk)
  - political_risk_index (country risk)
  - commercial_coherence (deal coherence)
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, Optional

from . import redis_store as rs
from .engine_wiring import wire_success, wire_failure

logger = logging.getLogger("aria.bd_strategy")

# ── Redis keys ────────────────────────────────────────────────────────────────
_STRATEGY_KEY = "crucix:bd_strategy:latest"
_HISTORY_KEY = "crucix:bd_strategy:history"
_INSIGHTS_KEY = "crucix:bd_strategy:insights"
_MAX_HISTORY = 100


async def generate_market_intelligence() -> dict:
    """Generate comprehensive market intelligence from all available data.
    
    Returns a structured market intelligence report with:
    - Market opportunities by region/country
    - Competitor activity analysis
    - Procurement pipeline highlights
    - Risk factors and recommendations
    """
    from . import intel_ledger as il
    from . import competitor_tracker as ct
    from . import tender_monitor as tm
    from . import political_risk_index as pri
    from . import deal_pipeline as dp

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "market_opportunities": [],
        "competitor_intelligence": [],
        "procurement_highlights": [],
        "risk_assessment": [],
        "strategic_recommendations": [],
        "sources_consulted": [],
    }

    # 1. Market opportunities from intel ledger
    try:
        signals = await il.get_recent(limit=200)
        report["sources_consulted"].append("intel_ledger")
        # Group signals by region/country
        by_region: dict[str, list[dict]] = {}
        for s in signals:
            region = s.get("region", s.get("country", "unknown"))
            by_region.setdefault(region, []).append(s)

        for region, sigs in sorted(by_region.items(), key=lambda x: len(x[1]), reverse=True)[:10]:
            report["market_opportunities"].append({
                "region": region,
                "signal_count": len(sigs),
                "recent_signals": [
                    {"title": s.get("title", "")[:100], "confidence": s.get("confidence", "ASSESSED")}
                    for s in sigs[:5]
                ],
                "assessment": _assess_market_opportunity(region, sigs),
            })
    except Exception as e:
        logger.debug("[bd_strategy] intel_ledger failed: %s", e)
        wire_failure(module="bd_strategy", detail=f"intel_ledger failed: {e}", gap_type="source_failure", source="bd_strategy")

    # 2. Competitor intelligence
    try:
        competitors = await ct.get_competitor_activity(limit=50)
        report["sources_consulted"].append("competitor_tracker")
        for comp in competitors[:10]:
            report["competitor_intelligence"].append({
                "competitor": comp.get("name", "unknown"),
                "activity": comp.get("activity", "")[:200],
                "market": comp.get("market", ""),
                "significance": comp.get("significance", "monitor"),
                "detected_at": comp.get("detected_at", ""),
            })
    except Exception as e:
        logger.debug("[bd_strategy] competitor_tracker failed: %s", e)
        wire_failure(module="bd_strategy", detail=f"competitor_tracker failed: {e}", gap_type="source_failure", source="bd_strategy")

    # 3. Procurement highlights
    try:
        tenders = await tm.get_new_tenders(since_hours=72)
        report["sources_consulted"].append("tender_monitor")
        for t in tenders[:10]:
            report["procurement_highlights"].append({
                "title": t.get("title", "")[:150],
                "country": t.get("country", ""),
                "value": t.get("value", ""),
                "deadline": t.get("deadline", ""),
                "category": t.get("category", ""),
                "source_url": t.get("source_url", ""),
            })
    except Exception as e:
        logger.debug("[bd_strategy] tender_monitor failed: %s", e)
        wire_failure(module="bd_strategy", detail=f"tender_monitor failed: {e}", gap_type="source_failure", source="bd_strategy")

    # 4. Risk assessment
    try:
        risks = pri.summary()
        report["sources_consulted"].append("political_risk_index")
        for risk in risks[:10]:
            report["risk_assessment"].append({
                "country": risk.get("country", ""),
                "risk_level": risk.get("level", "medium"),
                "risk_type": risk.get("type", "political"),
                "description": risk.get("description", "")[:200],
                "trend": risk.get("trend", "stable"),
            })
    except Exception as e:
        logger.debug("[bd_strategy] political_risk_index failed: %s", e)

    # 5. Strategic recommendations
    report["strategic_recommendations"] = _generate_recommendations(
        report["market_opportunities"],
        report["competitor_intelligence"],
        report["risk_assessment"],
    )

    # Persist
    try:
        await rs.set_json(_STRATEGY_KEY, report, ex=86400)
        await rs.lpush(_HISTORY_KEY, json.dumps({"timestamp": time.time(), "summary": _summarize(report)}))
        await rs.ltrim(_HISTORY_KEY, 0, _MAX_HISTORY - 1)
    except Exception as e:
        logger.debug("[bd_strategy] persist failed: %s", e)

    # Wire to brain
    wire_success(
        module="bd_strategy",
        summary=f"BD strategy generated: {len(report['market_opportunities'])} opportunities, "
                f"{len(report['competitor_intelligence'])} competitor moves, "
                f"{len(report['strategic_recommendations'])} recommendations",
        source_id="bd_strategy:generate_market_intelligence",
    )

    return report


def _assess_market_opportunity(region: str, signals: list[dict]) -> dict:
    """Assess a market opportunity based on signal patterns."""
    confidence_levels = [s.get("confidence", "ASSESSED") for s in signals]
    high_confidence = sum(1 for c in confidence_levels if c in ("CONFIRMED", "PROBABLE"))
    total = len(signals)

    return {
        "signal_volume": total,
        "high_confidence_signals": high_confidence,
        "signal_quality": "high" if high_confidence / max(total, 1) > 0.5 else "medium",
        "momentum": "increasing" if total > 10 else "stable",
        "recommendation": "prioritize" if high_confidence > 5 else "monitor",
    }


def _generate_recommendations(
    opportunities: list[dict],
    competitors: list[dict],
    risks: list[dict],
) -> list[dict]:
    """Generate strategic recommendations from all inputs."""
    recommendations = []

    # Market entry recommendations
    for opp in opportunities[:5]:
        if opp.get("assessment", {}).get("recommendation") == "prioritize":
            recommendations.append({
                "type": "market_entry",
                "target": opp["region"],
                "priority": "high",
                "rationale": f"{opp['region']} has {opp['assessment']['signal_volume']} signals "
                            f"with {opp['assessment']['high_confidence_signals']} high-confidence indicators",
                "action": "Schedule market assessment call. Prepare capability brief.",
            })

    # Competitor response recommendations
    for comp in competitors[:3]:
        if comp.get("significance") in ("high", "critical"):
            recommendations.append({
                "type": "competitor_response",
                "target": comp["competitor"],
                "priority": "high",
                "rationale": f"{comp['competitor']} active in {comp['market']}: {comp['activity'][:100]}",
                "action": "Develop counter-positioning strategy. Update competitive intelligence brief.",
            })

    # Risk mitigation recommendations
    for risk in risks[:3]:
        if risk.get("risk_level") in ("high", "critical"):
            recommendations.append({
                "type": "risk_mitigation",
                "target": risk["country"],
                "priority": "high",
                "rationale": f"{risk['country']}: {risk['risk_type']} risk at {risk['risk_level']} level",
                "action": "Review exposure. Update risk register. Consider contingency plans.",
            })

    return recommendations


def _summarize(report: dict) -> dict:
    """Create a summary of the report for history tracking."""
    return {
        "opportunities": len(report.get("market_opportunities", [])),
        "competitors": len(report.get("competitor_intelligence", [])),
        "tenders": len(report.get("procurement_highlights", [])),
        "risks": len(report.get("risk_assessment", [])),
        "recommendations": len(report.get("strategic_recommendations", [])),
    }


async def get_latest_strategy() -> dict:
    """Get the latest generated strategy."""
    try:
        report = await rs.get_json(_STRATEGY_KEY)
        if report:
            return report
    except Exception:
        pass
    return {"error": "No strategy generated yet", "generated_at": None}


async def get_strategy_history(limit: int = 20) -> list[dict]:
    """Get strategy generation history."""
    try:
        raw = await rs.lrange(_HISTORY_KEY, 0, limit - 1)
        return [json.loads(r) if isinstance(r, str) else r for r in raw]
    except Exception as e:
        logger.debug("[bd_strategy] history failed: %s", e)
        return []


async def generate_deal_strategy(deal_id: str) -> dict:
    """Generate a specific deal strategy with positioning recommendations."""
    from . import deal_pipeline as dp
    from . import competitor_tracker as ct
    from . import commercial_coherence as cc

    deal = await dp.get_lead(deal_id)
    if not deal:
        return {"error": f"Deal {deal_id} not found"}

    strategy = {
        "deal_id": deal_id,
        "deal_name": deal.get("name", ""),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "market_position": {},
        "competitive_landscape": [],
        "win_strategy": {},
        "risk_factors": [],
        "recommended_actions": [],
    }

    # Market position
    country = deal.get("country", "")
    sector = deal.get("sector", "")
    strategy["market_position"] = {
        "country": country,
        "sector": sector,
        "our_strength": _assess_strength(country, sector),
        "market_access": _assess_market_access(country),
    }

    # Competitive landscape
    try:
        competitors = await ct.get_competitor_activity(firm=deal.get("name", ""))
        for comp in competitors:
            strategy["competitive_landscape"].append({
                "competitor": comp.get("name", "unknown"),
                "position": comp.get("position", ""),
                "strength": comp.get("strength", "unknown"),
                "recent_moves": comp.get("recent_activity", "")[:200],
            })
    except Exception as e:
        logger.debug("[bd_strategy] competitors failed: %s", e)

    # Win strategy
    strategy["win_strategy"] = {
        "differentiators": _identify_differentiators(deal, strategy["competitive_landscape"]),
        "key_messages": _generate_key_messages(deal),
        "recommended_approach": _recommend_approach(deal),
    }

    # Risk factors
    try:
        from . import political_risk_index as pri
        risks = pri.summary()
        for risk in risks:
            strategy["risk_factors"].append({
                "type": risk.get("type", "political"),
                "level": risk.get("level", "medium"),
                "impact": risk.get("description", "")[:200],
                "mitigation": risk.get("mitigation", ""),
            })
    except Exception as e:
        logger.debug("[bd_strategy] risks failed: %s", e)

    # Recommended actions
    strategy["recommended_actions"] = [
        {
            "action": "Schedule capability presentation",
            "priority": "high",
            "timeline": "this_week",
            "owner": "BD team",
        },
        {
            "action": "Prepare competitive positioning brief",
            "priority": "high",
            "timeline": "this_week",
            "owner": "Intel team",
        },
        {
            "action": "Conduct stakeholder mapping",
            "priority": "medium",
            "timeline": "next_week",
            "owner": "BD team",
        },
        {
            "action": "Review risk mitigation plan",
            "priority": "medium",
            "timeline": "next_week",
            "owner": "Compliance team",
        },
    ]

    return strategy


def _assess_strength(country: str, sector: str) -> str:
    """Assess our strength in a country/sector."""
    # This would use actual data from deal history
    return "strong" if sector in ("defence", "security") else "developing"


def _assess_market_access(country: str) -> str:
    """Assess market access conditions."""
    # This would use political risk data
    return "open" if country not in ("russia", "china", "iran") else "restricted"


def _identify_differentiators(deal: dict, competitors: list[dict]) -> list[str]:
    """Identify key differentiators vs competitors."""
    diff = [
        "Deep domain expertise in defence & security",
        "Proprietary intelligence pipeline (337+ sources)",
        "Real-time sanctions & compliance screening",
        "Multi-language capability (Portuguese, Arabic, Turkish)",
        "Proven track record in Lusophone Africa markets",
    ]
    return diff


def _generate_key_messages(deal: dict) -> list[str]:
    """Generate key messaging for the deal."""
    return [
        f"ARIA delivers comprehensive DD on {deal.get('country', 'target market')} in hours, not weeks",
        "Real-time sanctions monitoring ensures compliance throughout the deal lifecycle",
        "Proven intelligence methodology trusted by defence brokers and compliance officers",
    ]


def _recommend_approach(deal: dict) -> str:
    """Recommend the approach for this deal."""
    value = deal.get("value", 0)
    if isinstance(value, (int, float)) and value > 10_000_000:
        return "full_service"
    return "standard"
