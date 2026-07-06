"""ARIA Team Engagement — proactive team interaction and self-improvement.

Three responsibilities:
1. SOURCE DISCOVERY — find new intelligence sources to expand coverage
2. TEAM ENGAGEMENT — track who interacts, nudge quiet members, pull everyone in
3. SELF-IMPROVEMENT — analyze gaps and ask the team for specific knowledge

ARIA is not passive. She pulls the team toward her, asks for what she needs,
and actively hunts for better data sources. Every team member has unique
knowledge that ARIA needs — her job is to extract it.
"""
from __future__ import annotations
from .engine_wiring import wire_failure

import json
import logging
import time
from datetime import datetime, timezone, timedelta
from typing import Any

from . import redis_store as rs

logger = logging.getLogger("aria.team_engagement")

# ── Redis keys ────────────────────────────────────────────────────────────────
_KEY_TEAM_STATS = "crucix:aria:team:interaction_stats"
_KEY_KNOWLEDGE_REQUESTS = "crucix:aria:team:knowledge_requests"
_KEY_SOURCE_RECOMMENDATIONS = "crucix:aria:team:source_recs"

# ── Team member profiles ──────────────────────────────────────────────────────
# Each member has expertise areas ARIA should tap into. When ARIA detects a
# gap in one of these areas, she knows WHO to ask.
TEAM_PROFILES = {
    "Antonio": {
        "role": "Managing Director",
        "expertise": ["angola", "mozambique", "cplp", "lusophone_africa", "naval", "strategic_relationships", "oem_partnerships"],
        "languages": ["portuguese", "english", "spanish"],
        "value_to_aria": "C-suite relationships, strategic market knowledge, deal history, OEM partner landscape",
    },
    "Andre": {
        "role": "Business Development",
        "expertise": ["west_africa", "nigeria", "ghana", "exhibitions", "oem_contacts", "dsei", "eurosatory"],
        "languages": ["english", "french"],
        "value_to_aria": "Exhibition contacts, OEM meeting notes, West Africa ground truth, competitor sightings",
    },
    # Add more team members as they join
}

# ── Intelligence source categories to discover ───────────────────────────────
SOURCE_CATEGORIES = {
    "government_portals": {
        "description": "Official defence ministry and procurement authority websites",
        "priority": "HIGH",
        "examples": ["sam.gov", "ted.europa.eu", "contractsfinder.service.gov.uk"],
        "search_queries": [
            "defence procurement portal {country}",
            "military tender announcement system {country}",
            "ministry of defence procurement website {country}",
        ],
    },
    "think_tanks": {
        "description": "Defence policy research institutions",
        "priority": "HIGH",
        "examples": ["IISS", "RUSI", "RAND", "CSIS", "Chatham House"],
        "search_queries": [
            "defence think tank {region} analysis",
            "military policy research institute {country}",
            "security studies centre {region}",
        ],
    },
    "trade_publications": {
        "description": "Defence industry news and analysis",
        "priority": "HIGH",
        "examples": ["Janes", "Defense News", "Shephard Media", "Defense One"],
        "search_queries": [
            "defence industry news {region}",
            "military equipment review publication",
            "arms trade magazine {region}",
        ],
    },
    "osint_databases": {
        "description": "Open-source intelligence databases",
        "priority": "MEDIUM",
        "examples": ["OpenSanctions", "OCCRP", "ICIJ", "Bellingcat"],
        "search_queries": [
            "open source intelligence database defence",
            "arms trade tracking database",
            "beneficial ownership registry {country}",
        ],
    },
    "financial_data": {
        "description": "Defence budget and spending data",
        "priority": "MEDIUM",
        "examples": ["SIPRI", "World Bank", "IMF", "Stockholm Forum"],
        "search_queries": [
            "military expenditure data {country} {year}",
            "defence budget allocation {country}",
        ],
    },
    "regional_media": {
        "description": "Local language news sources in target markets",
        "priority": "HIGH",
        "examples": ["Lusa (Portuguese), AFP (French), Al Jazeera (Arabic)"],
        "search_queries": [
            "defence news {country} {language}",
            "military procurement announcement {country}",
        ],
    },
}


# ── Team interaction tracking ─────────────────────────────────────────────────

async def record_interaction(sender_name: str, message_type: str = "chat") -> None:
    """Record a team member's interaction with ARIA."""
    stats = await rs.get_json(_KEY_TEAM_STATS) or {}
    name = sender_name.strip()
    if not name:
        return

    if name not in stats:
        stats[name] = {
            "total_messages": 0,
            "last_interaction": "",
            "first_seen": datetime.now(timezone.utc).isoformat(),
            "message_types": {},
            "weekly_count": 0,
            "weekly_reset": datetime.now(timezone.utc).isoformat(),
        }

    entry = stats[name]
    entry["total_messages"] = entry.get("total_messages", 0) + 1
    entry["last_interaction"] = datetime.now(timezone.utc).isoformat()
    entry["message_types"][message_type] = entry.get("message_types", {}).get(message_type, 0) + 1

    # Weekly counter
    reset = entry.get("weekly_reset", "")
    try:
        reset_dt = datetime.fromisoformat(reset.replace("Z", "+00:00"))
        if (datetime.now(timezone.utc) - reset_dt).days >= 7:
            entry["weekly_count"] = 0
            entry["weekly_reset"] = datetime.now(timezone.utc).isoformat()
    except Exception:
        pass
    entry["weekly_count"] = entry.get("weekly_count", 0) + 1

    stats[name] = entry
    await rs.set_json(_KEY_TEAM_STATS, stats)


async def get_team_stats() -> dict:
    """Get interaction stats for all team members."""
    stats = await rs.get_json(_KEY_TEAM_STATS) or {}
    now = datetime.now(timezone.utc)
    result = {}

    for name, data in stats.items():
        last = data.get("last_interaction", "")
        days_since = 999
        if last:
            try:
                last_dt = datetime.fromisoformat(last.replace("Z", "+00:00"))
                days_since = (now - last_dt).days
            except Exception:
                pass

        result[name] = {
            **data,
            "_days_since_last": days_since,
            "_engagement_level": (
                "ACTIVE" if days_since <= 2
                else "REGULAR" if days_since <= 7
                else "QUIET" if days_since <= 14
                else "DISENGAGED"
            ),
        }

    # R-F996 — wire to brain
    from .engine_wiring import wire_success, wire_failure
    wire_success(
        module="team_engagement",
        summary="Get Team Stats",
        source_id="team_engagement:R-F996",
    )

    return result


async def get_quiet_members(days_threshold: int = 7) -> list[dict]:
    """Return team members who haven't interacted recently."""
    stats = await get_team_stats()
    quiet = []
    for name, data in stats.items():
        if data.get("_days_since_last", 0) >= days_threshold:
            quiet.append({
                "name": name,
                "days_since_last": data["_days_since_last"],
                "engagement_level": data["_engagement_level"],
                "total_messages": data.get("total_messages", 0),
                "profile": TEAM_PROFILES.get(name, {}),
            })
    # Also add known team members who've never interacted
    for name, profile in TEAM_PROFILES.items():
        if name not in stats:
            quiet.append({
                "name": name,
                "days_since_last": 999,
                "engagement_level": "NEVER_SEEN",
                "total_messages": 0,
                "profile": profile,
            })
    return sorted(quiet, key=lambda x: x["days_since_last"], reverse=True)


# ── Self-improvement: ARIA asks for what she needs ────────────────────────────

async def generate_knowledge_requests() -> list[dict]:
    """Analyze ARIA's gaps and generate specific requests for team members.

    Cross-references:
    - capability_gaps (what ARIA failed at)
    - coverage confidence (thin markets)
    - team profiles (who knows what)
    """
    requests = []

    # 1. Check coverage gaps
    try:
        from . import signal_correlator
        thin_markets = []
        for country in ["angola", "mozambique", "nigeria", "ghana", "kenya",
                        "turkey", "brazil", "indonesia", "senegal", "saudi arabia"]:
            cov = await signal_correlator.assess_coverage_confidence(country)
            if cov.get("verdict") in ("THIN", "ZERO"):
                thin_markets.append(country)

        for market in thin_markets:
            # Find who knows this market
            for name, profile in TEAM_PROFILES.items():
                if market.lower() in [e.lower() for e in profile.get("expertise", [])]:
                    requests.append({
                        "target": name,
                        "type": "market_coverage",
                        "priority": "HIGH",
                        "request": (
                            f"{name}, ARIA's coverage of *{market.title()}* is thin. "
                            f"Can you share: (1) any recent contacts or meetings there, "
                            f"(2) key procurement programmes you know about, "
                            f"(3) any documents or reports you have? "
                            f"Use /teach to share facts or forward emails to aria@arkmurus.com."
                        ),
                    })
    except Exception as e:
        logger.debug("Coverage gap analysis failed: %s", e)

    # 2. Check for missing contact data
    try:
        from . import deal_pipeline
        leads = await deal_pipeline.get_pipeline()
        for lead in leads:
            if not lead.get("owner"):
                requests.append({
                    "target": "team",
                    "type": "lead_ownership",
                    "priority": "MEDIUM",
                    "request": (
                        f"The *{lead.get('country', '?')}* lead ({lead.get('requirement', '?')[:60]}) "
                        f"has no owner assigned. Who should drive this? Reply: "
                        f"/pipeline update {lead.get('id', '?')} owner=[your name]"
                    ),
                })
    except Exception:
        pass

    # 3. Ask for exhibition/meeting debrief
    requests.append({
        "target": "team",
        "type": "knowledge_share",
        "priority": "LOW",
        "request": (
            "Has anyone attended a defence exhibition, meeting, or call this week? "
            "Share what you learned — names, companies, requirements, competitor sightings. "
            "Use /teach [topic]: [what you learned] and ARIA will remember it forever."
        ),
    })

    await rs.set_json(_KEY_KNOWLEDGE_REQUESTS, requests)
    return requests


# ── Source discovery ──────────────────────────────────────────────────────────

async def generate_source_recommendations() -> list[dict]:
    """Analyze current source coverage and recommend new sources to ingest.

    Checks RAG store for source diversity, identifies gaps, and suggests
    specific URLs/databases to add.
    """
    recommendations = []

    # Check which source types we have
    try:
        from . import rag_store
        stats = await rag_store.get_stats()
        total = stats.get("total_chunks", 0)

        # General recommendations based on gaps
        recommendations.append({
            "category": "regional_media",
            "priority": "HIGH",
            "recommendation": (
                "Add Portuguese-language news feeds for Lusophone Africa: "
                "Lusa.pt, DW Africa (Portuguese), VOA Portuguese, "
                "Club-K Angola, O País Mozambique. "
                "These break procurement news 2-3 weeks before English sources."
            ),
            "action": "Use /teach with each URL to ingest",
        })

        recommendations.append({
            "category": "government_portals",
            "priority": "HIGH",
            "recommendation": (
                "Add procurement portals not yet monitored: "
                "SEACE (Peru), MERX (Canada), e-licitatie (Romania), "
                "SIGMAP (Senegal), ARMP (Cameroon), "
                "PPDA (Uganda), PPOA (Kenya). "
                "Each could surface tenders ARIA currently misses."
            ),
            "action": "Wire into tender_monitor.py or use /teach",
        })

        recommendations.append({
            "category": "think_tanks",
            "priority": "MEDIUM",
            "recommendation": (
                "Ingest analysis from: ISS Africa (issafrica.org), "
                "DCAF Geneva (dcaf.ch), BICC Bonn (bicc.de), "
                "SIPRI blog (sipri.org/commentary), "
                "Defence Web South Africa (defenceweb.co.za). "
                "These provide regional context ARIA needs for briefings."
            ),
            "action": "Use /teach with each URL",
        })

        recommendations.append({
            "category": "osint_databases",
            "priority": "MEDIUM",
            "recommendation": (
                "Consider adding: OCCRP Aleph (beneficial ownership), "
                "ICIJ Offshore Leaks (shell company networks), "
                "Global Firepower API (force comparison data), "
                "Trading Economics (defence budget forecasts). "
                "These deepen DD and market intelligence."
            ),
            "action": "API integration or /teach with reports",
        })

        recommendations.append({
            "category": "team_knowledge",
            "priority": "CRITICAL",
            "recommendation": (
                "The team's PERSONAL KNOWLEDGE is ARIA's biggest untapped source. "
                "Meeting notes, exhibition contacts, competitor sightings, "
                "client feedback, deal postmortems — none of this is in ARIA "
                "unless someone shares it. Make /teach a daily habit."
            ),
            "action": "Daily team habit: /teach [what I learned today]",
        })

    except Exception as e:
        logger.debug("Source recommendation failed: %s", e)

    await rs.set_json(_KEY_SOURCE_RECOMMENDATIONS, recommendations)
    return recommendations


# ── Engagement briefing for daily team message ────────────────────────────────

async def generate_engagement_briefing() -> str:
    """Generate the team engagement section for the daily briefing.

    Combines: quiet member nudges, knowledge requests, source recommendations.
    """
    lines = []

    # 1. Quiet members
    quiet = await get_quiet_members(days_threshold=5)
    if quiet:
        lines.append("")
        lines.append("*🤝 TEAM ENGAGEMENT*")
        for q in quiet[:3]:
            name = q["name"]
            days = q["days_since_last"]
            profile = q.get("profile", {})
            expertise = ", ".join(profile.get("expertise", [])[:3])
            if days >= 14 or q["engagement_level"] == "NEVER_SEEN":
                lines.append(
                    f"👋 *{name}* — haven't heard from you in {days}d! "
                    f"Your expertise in _{expertise}_ is valuable. "
                    f"What's new in your markets?"
                )
            elif days >= 5:
                lines.append(
                    f"📡 *{name}* — {days}d since last check-in. "
                    f"Any intel from the field?"
                )

    # 2. Knowledge requests (top 2)
    try:
        requests = await generate_knowledge_requests()
        high_priority = [r for r in requests if r.get("priority") in ("HIGH", "CRITICAL")][:2]
        if high_priority:
            lines.append("")
            lines.append("*📚 ARIA NEEDS YOUR HELP*")
            for r in high_priority:
                lines.append(f"• {r['request']}")
    except Exception:
        pass

    # 3. Source recommendation (just one per day)
    try:
        recs = await generate_source_recommendations()
        if recs:
            top = recs[0]
            lines.append("")
            lines.append("*💡 INTELLIGENCE UPGRADE SUGGESTION*")
            lines.append(f"• {top['recommendation'][:200]}")
    except Exception:
        pass

    return "\n".join(lines) if lines else ""


# ── API helpers ───────────────────────────────────────────────────────────────

async def get_stats() -> dict:
    """Full team engagement stats."""
    team = await get_team_stats()
    quiet = await get_quiet_members()
    return {
        "team_members_tracked": len(team),
        "team_stats": team,
        "quiet_members": len(quiet),
        "quiet_list": [q["name"] for q in quiet],
        "profiles_configured": len(TEAM_PROFILES),
    }

# R-F2119 §21a — wire failure handler for team_engagement
try:
    wire_failure(module="team_engagement", detail="module shutdown",
                gap_type="engine_failure", source="team_engagement:shutdown")
except Exception:
    pass
