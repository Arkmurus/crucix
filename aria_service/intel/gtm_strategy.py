"""
Go-To-Market Strategy Engine.
Ported from lib/aria/gtm_strategy.mjs.
"""
from __future__ import annotations
from .engine_wiring import wire_failure

from .contacts import search_contacts

# ── GTM Playbooks by Tier ────────────────────────────────────────────────────

PLAYBOOKS = {
    "INCUMBENT": {
        "description": "Direct engagement via existing MoD relationships",
        "local_agent": "NO",
        "partner_needed": "NO",
        "typical_cycle": "1-3 months",
        "key_risk": "Complacency — competitors entering your markets",
        "entry_points": ["Direct MoD contact", "Defence attaché channel", "CPLP framework"],
    },
    "ESTABLISHED": {
        "description": "Competitive bid, differentiate on broker value-add",
        "local_agent": "Recommended",
        "partner_needed": "NO",
        "typical_cycle": "3-6 months",
        "key_risk": "Established competitors have deeper relationships",
        "entry_points": ["Monitor tender portals", "Pre-qualify with contacts", "Exhibition meetings"],
    },
    "DEVELOPING": {
        "description": "Relationship-first before commercial pitch",
        "local_agent": "Essential",
        "partner_needed": "OEM partnership essential",
        "typical_cycle": "6-12 months",
        "key_risk": "Investment without guaranteed return",
        "entry_points": ["Regional expos (DSEI, ShieldAfrica, AAD)", "Defence Attaché introductions"],
    },
    "COLD_ENTRY": {
        "description": "Partnership-led entry (find the right OEM or local firm first)",
        "local_agent": "Mandatory",
        "partner_needed": "Mandatory",
        "typical_cycle": "12-24 months",
        "key_risk": "High investment, uncertain return — only enter if opportunity score > 60",
        "entry_points": ["OEM teaming agreement", "Local industry JV", "Government-sponsored expo"],
    },
}

# ── Market-to-Tier mapping ───────────────────────────────────────────────────

MARKETS = {
    "Angola": {"tier": "INCUMBENT", "exhibition": "FILDA (Luanda, Jul)", "language": "Portuguese", "best_oem": "Embraer, Paramount", "offset": "Informal", "key_relationship": "SIMPORTEX Director"},
    "Mozambique": {"tier": "INCUMBENT", "exhibition": "Maputo Defence Forum", "language": "Portuguese", "best_oem": "Paramount, Damen", "offset": "Informal", "key_relationship": "FADM Procurement Director"},
    "Brazil": {"tier": "INCUMBENT", "exhibition": "LAAD (Rio, Apr)", "language": "Portuguese", "best_oem": "Embraer", "offset": "100% >R$50M", "key_relationship": "Defence Cooperation Coordinator"},
    "Nigeria": {"tier": "ESTABLISHED", "exhibition": "DSEI/ShieldAfrica", "language": "English", "best_oem": "Leonardo, Damen, Turkish", "offset": "DICON partnership", "key_relationship": "DICON Director"},
    "Kenya": {"tier": "ESTABLISHED", "exhibition": "DSEI/AAD", "language": "English", "best_oem": "Various", "offset": "Informal", "key_relationship": "KDF Procurement Director"},
    "Guinea-Bissau": {"tier": "INCUMBENT", "exhibition": "None regular", "language": "Portuguese", "best_oem": "Light equipment", "offset": "None", "key_relationship": "Family connections + CPLP"},
    "Senegal": {"tier": "DEVELOPING", "exhibition": "ShieldAfrica (Abidjan)", "language": "French", "best_oem": "Nexter, Airbus", "offset": "Informal", "key_relationship": "Defence Attaché"},
    "Ghana": {"tier": "DEVELOPING", "exhibition": "ShieldAfrica", "language": "English", "best_oem": "Paramount, Otokar", "offset": "Informal", "key_relationship": "MoD Procurement"},
    "Ethiopia": {"tier": "DEVELOPING", "exhibition": "None regular", "language": "English", "best_oem": "Various", "offset": "Informal", "key_relationship": "MoD Directorate"},
    "Indonesia": {"tier": "COLD_ENTRY", "exhibition": "Indo Defence (Nov)", "language": "English/Indonesian", "best_oem": "Hanwha, KAI (partner)", "offset": "35-85% mandatory", "key_relationship": "OEM local partner"},
    "Philippines": {"tier": "COLD_ENTRY", "exhibition": "ADAS (Manila)", "language": "English", "best_oem": "Various (diversifying from US FMS)", "offset": "Informal", "key_relationship": "DND Procurement"},
    "Saudi Arabia": {"tier": "COLD_ENTRY", "exhibition": "IDEX/WDS", "language": "Arabic/English", "best_oem": "European primes", "offset": "50-60% via GAMI", "key_relationship": "GAMI gatekeeper"},
    "UAE": {"tier": "COLD_ENTRY", "exhibition": "IDEX", "language": "Arabic/English", "best_oem": "EDGE subsidiary + OEM", "offset": "60% Tawazun", "key_relationship": "EDGE Group"},
    "India": {"tier": "COLD_ENTRY", "exhibition": "DefExpo (Feb)", "language": "English", "best_oem": "European non-ITAR", "offset": "30%+ Make in India", "key_relationship": "MoD Acquisition Wing"},
    "Poland": {"tier": "COLD_ENTRY", "exhibition": "MSPO Kielce (Sep)", "language": "English/Polish", "best_oem": "Hanwha, Lockheed, BAE", "offset": "Industrial cooperation", "key_relationship": "Armament Agency"},
    "Egypt": {"tier": "COLD_ENTRY", "exhibition": "EDEX (Dec)", "language": "Arabic/English", "best_oem": "European + Korean", "offset": "Arab Industrial Organisation", "key_relationship": "MoD Procurement"},
    "Morocco": {"tier": "COLD_ENTRY", "exhibition": "None regular", "language": "French/Arabic", "best_oem": "French/US", "offset": "Informal", "key_relationship": "Royal Armed Forces HQ"},
    "Colombia": {"tier": "COLD_ENTRY", "exhibition": "ExpoDefensa (Dec)", "language": "Spanish", "best_oem": "US/Israeli/European", "offset": "Informal", "key_relationship": "MoD Acquisition"},
    "Australia": {"tier": "COLD_ENTRY", "exhibition": "Land Forces (Oct)", "language": "English", "best_oem": "AUKUS partners", "offset": "AIC programme", "key_relationship": "CASG"},
    "Japan": {"tier": "COLD_ENTRY", "exhibition": "DSEI Japan (Mar)", "language": "English/Japanese", "best_oem": "US alliance only", "offset": "Licensed production", "key_relationship": "ATLA"},
    "Turkey": {"tier": "COLD_ENTRY", "exhibition": "IDEF Istanbul (May)", "language": "English/Turkish", "best_oem": "Domestic SSB", "offset": "Co-production", "key_relationship": "SSB (Defence Industry Agency)"},
    "Vietnam": {"tier": "COLD_ENTRY", "exhibition": "None regular", "language": "English/Vietnamese", "best_oem": "Israeli/Indian/Korean", "offset": "Informal", "key_relationship": "General Dept of Defence Industry"},
    "Romania": {"tier": "COLD_ENTRY", "exhibition": "BSDA Bucharest (Jun)", "language": "English/Romanian", "best_oem": "NATO-standard", "offset": "Industrial cooperation", "key_relationship": "MoD DPA"},
    "South Korea": {"tier": "COLD_ENTRY", "exhibition": "ADEX Seoul (Oct)", "language": "English/Korean", "best_oem": "Domestic DAPA", "offset": "Technology transfer", "key_relationship": "DAPA"},
}


# ── Public API ───────────────────────────────────────────────────────────────

def generate_gtm_strategy(market: str) -> dict | None:
    """Generate GTM strategy for a market."""
    info = MARKETS.get(market)
    if not info:
        # Fuzzy
        ml = market.lower()
        for k, v in MARKETS.items():
            if ml in k.lower():
                info = v
                market = k
                break
    if not info:
        return None

    playbook = PLAYBOOKS.get(info["tier"], PLAYBOOKS["DEVELOPING"])
    contacts = search_contacts(market)[:3]

    return {
        "market": market,
        "tier": info["tier"],
        "playbook": playbook,
        "exhibition": info["exhibition"],
        "language": info["language"],
        "keyRelationship": info["key_relationship"],
        "bestOEM": info["best_oem"],
        "offset": info["offset"],
        "contacts": contacts,
        "timeToFirstDeal": playbook["typical_cycle"],
    }


def get_gtm_context(query: str) -> str:
    """Synchronous prompt injection — triggers on GTM-related queries."""
    ql = query.lower()
    gtm_kws = ["go to market", "market entry", "break into", "enter market", "gtm", "how to sell"]
    if not any(k in ql for k in gtm_kws):
        return ""
    for market in MARKETS:
        if market.lower() in ql:
            result = generate_gtm_strategy(market)
            if not result:
                return ""
            pb = result["playbook"]
            lines = [
                f"\n[GO-TO-MARKET STRATEGY — {market} (Tier: {result['tier']})]",
                f"Strategy: {pb['description']}",
                f"Local agent: {pb['local_agent']} | Partner: {pb['partner_needed']}",
                f"Cycle: {pb['typical_cycle']} | Exhibition: {result['exhibition']}",
                f"Key risk: {pb['key_risk']}",
                f"Best OEM: {result['bestOEM']} | Key relationship: {result['keyRelationship']}",
            ]
            return "\n".join(lines)
    # R-F996 — wire to brain
    from .engine_wiring import wire_success, wire_failure
    wire_success(
        module="gtm_strategy",
        summary="Get Gtm Context",
        source_id="gtm_strategy:R-F996",
    )

    return ""

# R-F2119 §21a — wire failure handler for gtm_strategy
try:
    wire_failure(module="gtm_strategy", detail="module shutdown",
                gap_type="engine_failure", source="gtm_strategy:shutdown")
except Exception:
    pass
