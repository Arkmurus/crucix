"""
Market Approach Strategy Generator.
Ported from lib/aria/approach.mjs.
"""
from __future__ import annotations

import re
from typing import Any

from .contacts import search_contacts

# ── Market Profiles ──────────────────────────────────────────────────────────

MARKET_PROFILES: dict[str, dict] = {
    "Angola": {"language": "Portuguese", "formality": "HIGH", "greeting": "Exmo. Senhor", "tz": "WAT (UTC+1)", "currency": "AOA/USD", "notes": "Formal letter via Defence Attaché, CPLP framework", "cycle": "3-12 months"},
    "Mozambique": {"language": "Portuguese", "formality": "HIGH", "greeting": "Exmo. Senhor", "tz": "CAT (UTC+2)", "currency": "MZN/USD", "notes": "SADC/EU SAMIM cooperation, Cabo Delgado urgency", "cycle": "3-12 months"},
    "Nigeria": {"language": "English", "formality": "HIGH", "greeting": "Dear Sir/Madam", "tz": "WAT (UTC+1)", "currency": "NGN/USD", "notes": "Formal tender, local content (DICON partnership)", "cycle": "3-12 months"},
    "Kenya": {"language": "English", "formality": "MEDIUM", "greeting": "Dear Sir/Madam", "tz": "EAT (UTC+3)", "currency": "KES/USD", "notes": "Competitive tender, AMISOM/ATMIS reference", "cycle": "3-12 months"},
    "Saudi Arabia": {"language": "English/Arabic", "formality": "VERY HIGH", "greeting": "Your Royal Highness", "tz": "AST (UTC+3)", "currency": "SAR/USD", "notes": "Vision 2030, GAMI is gatekeeper, agent mandatory, 50-60% offset", "cycle": "2-5 years"},
    "UAE": {"language": "English/Arabic", "formality": "HIGH", "greeting": "Your Excellency", "tz": "GST (UTC+4)", "currency": "AED/USD", "notes": "EDGE Group partnership, agent essential, IDEX meeting point, 60% Tawazun", "cycle": "6-18 months"},
    "Senegal": {"language": "French", "formality": "HIGH", "greeting": "Excellence", "tz": "GMT", "currency": "XOF", "notes": "Nexter/Airbus historical preference, ECOWAS contributor", "cycle": "6-18 months"},
    "Indonesia": {"language": "English/Indonesian", "formality": "HIGH", "greeting": "Yang Terhormat", "tz": "WIB (UTC+7)", "currency": "IDR/USD", "notes": "MEF programme, 35-85% offset mandatory, Korean competitors strong", "cycle": "12-24 months"},
    "Philippines": {"language": "English", "formality": "MEDIUM", "greeting": "Dear Sir/Madam", "tz": "PHT (UTC+8)", "currency": "PHP/USD", "notes": "Horizon 3 modernisation, US FMS dominant, diversifying", "cycle": "6-18 months"},
    "Brazil": {"language": "Portuguese", "formality": "HIGH", "greeting": "Exmo. Senhor", "tz": "BRT (UTC-3)", "currency": "BRL/USD", "notes": "Embraer domestic champion, 100% offset >R$50M", "cycle": "6-18 months"},
    "Ghana": {"language": "English", "formality": "MEDIUM", "greeting": "Dear Sir/Madam", "tz": "GMT", "currency": "GHS/USD", "notes": "Competitive tender, ECOWAS contributor", "cycle": "6-12 months"},
    "Ethiopia": {"language": "English", "formality": "HIGH", "greeting": "Your Excellency", "tz": "EAT (UTC+3)", "currency": "ETB/USD", "notes": "Post-conflict reconstruction, relationship-first", "cycle": "6-18 months"},
    "Guinea-Bissau": {"language": "Portuguese", "formality": "HIGH", "greeting": "Exmo. Senhor", "tz": "GMT", "currency": "XOF", "notes": "ECOWAS donor-dependent, Arkmurus family connections", "cycle": "6-18 months"},
    "Cape Verde": {"language": "Portuguese", "formality": "HIGH", "greeting": "Exmo. Senhor", "tz": "CVT (UTC-1)", "currency": "CVE/USD", "notes": "Small force, maritime focus, CPLP framework", "cycle": "6-18 months"},
    "India": {"language": "English", "formality": "HIGH", "greeting": "Dear Sir/Madam", "tz": "IST (UTC+5:30)", "currency": "INR/USD", "notes": "Make in India policy, DPP offset 30%+, DRDO preference for indigenous", "cycle": "12-24 months"},
    "Poland": {"language": "English/Polish", "formality": "HIGH", "greeting": "Dear Sir/Madam", "tz": "CET (UTC+1)", "currency": "PLN/EUR", "notes": "NATO Article 5, 4% GDP target, Korean/US platforms preferred, massive modernisation", "cycle": "6-18 months"},
    "Egypt": {"language": "English/Arabic", "formality": "HIGH", "greeting": "Your Excellency", "tz": "EET (UTC+2)", "currency": "EGP/USD", "notes": "Diversifying from US FMS, Arab Industrial Organisation local content", "cycle": "12-24 months"},
    "Morocco": {"language": "French/Arabic", "formality": "HIGH", "greeting": "Excellence", "tz": "WET (UTC+1)", "currency": "MAD/USD", "notes": "Modernising rapidly, US/French primary suppliers, ITAR-free preferred", "cycle": "6-18 months"},
    "Colombia": {"language": "Spanish", "formality": "HIGH", "greeting": "Señor Ministro", "tz": "COT (UTC-5)", "currency": "COP/USD", "notes": "Post-conflict modernisation, counter-narcotics focus, US FMS primary", "cycle": "6-18 months"},
    "Australia": {"language": "English", "formality": "MEDIUM", "greeting": "Dear Sir/Madam", "tz": "AEST (UTC+10)", "currency": "AUD/USD", "notes": "AUKUS, massive submarine programme, high-end Western systems only", "cycle": "12-36 months"},
    "Japan": {"language": "English/Japanese", "formality": "VERY HIGH", "greeting": "Dear Sir/Madam", "tz": "JST (UTC+9)", "currency": "JPY/USD", "notes": "Doubling defence budget, counter-strike capability, US alliance primary", "cycle": "12-36 months"},
    "South Korea": {"language": "English/Korean", "formality": "HIGH", "greeting": "Dear Sir/Madam", "tz": "KST (UTC+9)", "currency": "KRW/USD", "notes": "Major exporter, DAPA procurement agency, indigenous development priority", "cycle": "6-18 months"},
    "Turkey": {"language": "English/Turkish", "formality": "HIGH", "greeting": "Dear Sir/Madam", "tz": "TRT (UTC+3)", "currency": "TRY/USD", "notes": "SSB procurement agency, domestic industry priority, aggressive exporter", "cycle": "6-18 months"},
    "Vietnam": {"language": "English/Vietnamese", "formality": "HIGH", "greeting": "Dear Sir/Madam", "tz": "ICT (UTC+7)", "currency": "VND/USD", "notes": "Russian legacy replacement, diversifying to Israel/India/South Korea", "cycle": "12-24 months"},
    "Thailand": {"language": "English/Thai", "formality": "HIGH", "greeting": "Dear Sir/Madam", "tz": "ICT (UTC+7)", "currency": "THB/USD", "notes": "Mixed Western/Chinese fleet, budget-conscious, Chinese subs controversial", "cycle": "6-18 months"},
    "Romania": {"language": "English/Romanian", "formality": "HIGH", "greeting": "Dear Sir/Madam", "tz": "EET (UTC+2)", "currency": "RON/EUR", "notes": "NATO eastern flank, F-16 upgrade, Piranha V production, Black Sea focus", "cycle": "6-18 months"},
}

# ── OEM Rankings by Product ──────────────────────────────────────────────────

OEM_RANKINGS: dict[str, list[dict]] = {
    "armoured_vehicles": [
        {"oem": "Paramount Group", "country": "ZA", "itar": False, "price": "MEDIUM", "africa": "STRONG"},
        {"oem": "Otokar", "country": "TR", "itar": False, "price": "LOW-MEDIUM", "africa": "GROWING"},
        {"oem": "FNSS", "country": "TR", "itar": False, "price": "MEDIUM", "africa": "GROWING"},
        {"oem": "Rheinmetall", "country": "DE", "itar": False, "price": "HIGH", "africa": "MODERATE"},
        {"oem": "Norinco", "country": "CN", "itar": False, "price": "LOW", "africa": "STRONG"},
    ],
    "uav_systems": [
        {"oem": "Baykar", "country": "TR", "itar": False, "price": "MEDIUM", "africa": "STRONG"},
        {"oem": "Elbit Systems", "country": "IL", "itar": False, "price": "HIGH", "africa": "STRONG"},
        {"oem": "Turkish Aerospace", "country": "TR", "itar": False, "price": "MEDIUM", "africa": "GROWING"},
        {"oem": "AVIC/CATIC", "country": "CN", "itar": False, "price": "LOW", "africa": "GROWING"},
        {"oem": "IAI", "country": "IL", "itar": False, "price": "HIGH", "africa": "MODERATE"},
    ],
    "patrol_vessels": [
        {"oem": "Damen", "country": "NL", "itar": False, "price": "MEDIUM", "africa": "STRONG"},
        {"oem": "Navantia", "country": "ES", "itar": False, "price": "MEDIUM", "africa": "MODERATE"},
        {"oem": "Fincantieri", "country": "IT", "itar": False, "price": "HIGH", "africa": "MODERATE"},
    ],
    "ammunition": [
        {"oem": "Rheinmetall Denel", "country": "ZA/DE", "itar": False, "price": "MEDIUM", "africa": "STRONG"},
        {"oem": "Norinco", "country": "CN", "itar": False, "price": "LOW", "africa": "STRONG"},
        {"oem": "General Dynamics OTS", "country": "US", "itar": True, "price": "HIGH", "africa": "LIMITED"},
    ],
    "helicopters": [
        {"oem": "Leonardo", "country": "IT", "itar": False, "price": "HIGH", "africa": "STRONG"},
        {"oem": "Airbus Helicopters", "country": "FR", "itar": False, "price": "HIGH", "africa": "MODERATE"},
        {"oem": "Turkish Aerospace", "country": "TR", "itar": False, "price": "MEDIUM", "africa": "GROWING"},
    ],
    "radar_air_defence": [
        {"oem": "Aselsan", "country": "TR", "itar": False, "price": "MEDIUM", "africa": "GROWING"},
        {"oem": "Thales", "country": "FR", "itar": False, "price": "HIGH", "africa": "MODERATE"},
        {"oem": "Rafael", "country": "IL", "itar": False, "price": "HIGH", "africa": "MODERATE"},
        {"oem": "Saab", "country": "SE", "itar": False, "price": "HIGH", "africa": "LIMITED"},
    ],
    "training": [
        {"oem": "Leonardo (M-346)", "country": "IT", "itar": False, "price": "HIGH", "africa": "STRONG"},
        {"oem": "KAI (FA-50)", "country": "KR", "itar": False, "price": "MEDIUM", "africa": "GROWING"},
        {"oem": "Embraer (A-29)", "country": "BR", "itar": False, "price": "MEDIUM", "africa": "MODERATE"},
    ],
}


def _map_product_to_category(product: str) -> str:
    pl = product.lower()
    mapping = [
        ("armoured_vehicles", ["armour", "vehicle", "apc", "ifv", "mrap", "tank"]),
        ("uav_systems", ["uav", "drone", "unmanned", "uas", "rpas"]),
        ("patrol_vessels", ["vessel", "patrol", "opv", "corvette", "frigate", "naval"]),
        ("ammunition", ["ammunit", "ammo", "round", "mortar", "shell", "calibre"]),
        ("helicopters", ["helicopter", "rotary", "helo"]),
        ("radar_air_defence", ["radar", "air defen", "sam", "shorad", "c-ram"]),
        ("training", ["training", "trainer", "simulation", "exercise"]),
    ]
    for cat, kws in mapping:
        if any(k in pl for k in kws):
            return cat
    return ""


# ── Draft Messages ───────────────────────────────────────────────────────────

def _draft_message(profile: dict, product: str, market: str) -> str:
    lang = profile.get("language", "English")
    greeting = profile.get("greeting", "Dear Sir/Madam")

    if "Portuguese" in lang:
        return (
            f"{greeting},\n\n"
            f"A Arkmurus Group apresenta os seus cumprimentos e manifesta o seu interesse "
            f"em apoiar as necessidades de {product or 'defesa'} de {market}. "
            f"Enquanto consultora britânica de defesa com forte presença no espaço CPLP, "
            f"temos experiência comprovada na facilitação de aquisições de defesa em conformidade "
            f"com os regulamentos internacionais aplicáveis.\n\n"
            f"Gostaríamos de agendar uma reunião para discutir como podemos apoiar os vossos objectivos.\n\n"
            f"Com os melhores cumprimentos,\nArkmurus Group"
        )
    elif "French" in lang:
        return (
            f"{greeting},\n\n"
            f"Arkmurus Group vous présente ses salutations et exprime son intérêt à soutenir "
            f"les besoins de défense de {market}. En tant que cabinet-conseil britannique en défense, "
            f"nous disposons d'une expertise éprouvée en facilitation d'acquisitions de défense "
            f"conformément aux réglementations internationales.\n\n"
            f"Nous souhaiterions convenir d'une réunion pour discuter de notre collaboration.\n\n"
            f"Avec nos salutations distinguées,\nArkmurus Group"
        )
    elif "Arabic" in lang:
        return (
            f"{greeting},\n\n"
            f"Arkmurus Group presents its compliments and expresses interest in supporting "
            f"the defence requirements of {market}. As a UK-based defence advisory with "
            f"extensive regional experience and compliance-first approach, we bring both "
            f"established OEM relationships and deep understanding of Middle East procurement.\n\n"
            f"We would welcome the opportunity to discuss how we may be of service.\n\n"
            f"With highest regards,\nArkmurus Group"
        )
    else:
        return (
            f"{greeting},\n\n"
            f"Arkmurus Group presents its compliments and expresses interest in supporting "
            f"the {product or 'defence'} requirements of {market}. As a UK-registered defence "
            f"advisory and brokerage firm, we bring OEM relationships, export compliance "
            f"expertise, and proven experience across African and emerging markets.\n\n"
            f"We would welcome the opportunity to discuss collaboration.\n\n"
            f"With best regards,\nArkmurus Group"
        )


# ── Public API ───────────────────────────────────────────────────────────────

def generate_approach(market: str, product: str = "", context: str = "") -> dict:
    """Generate a complete approach strategy for a market/product."""
    profile = MARKET_PROFILES.get(market)
    if not profile:
        # Fuzzy match
        ml = market.lower()
        for k, v in MARKET_PROFILES.items():
            if ml in k.lower():
                profile = v
                market = k
                break
    if not profile:
        return {"error": f"No profile for market: {market}"}

    category = _map_product_to_category(product) if product else ""
    ranked_oems = OEM_RANKINGS.get(category, [])

    contacts = search_contacts(market)[:4]

    compliance = [
        f"Language: {profile['language']} required for all formal correspondence",
        "ITAR check: " + ("⚠️ ITAR-controlled items require US State Dept approval" if any(o.get("itar") for o in ranked_oems) else "Non-ITAR OEMs preferred"),
        f"End-user certificate required from {market} MoD",
        "UK SIEL export licence (20-60 working days processing)",
        f"UN Security Council embargo check for {market}",
        "OFAC SDN / EU consolidated / UK sanctions list verification",
    ]

    return {
        "market": market,
        "product": product or "general",
        "profile": profile,
        "contacts": contacts,
        "rankedOEMs": ranked_oems,
        "compliance": compliance,
        "draftMessage": _draft_message(profile, product, market),
        "timing": profile.get("notes", ""),
        "estimatedCycle": profile.get("cycle", "3-12 months"),
    }


def get_approach_context(query: str) -> str:
    """Synchronous prompt injection — triggers only on approach-related queries."""
    ql = query.lower()
    approach_kws = ["approach", "strategy", "enter", "engage", "pitch", "message", "draft", "contact"]
    if not any(k in ql for k in approach_kws):
        return ""
    # Find market in query
    for market in MARKET_PROFILES:
        if market.lower() in ql:
            result = generate_approach(market)
            if "error" in result:
                return ""
            p = result["profile"]
            lines = [
                f"\n[APPROACH STRATEGY — {market}]",
                f"Language: {p['language']} | Formality: {p['formality']} | Greeting: {p['greeting']}",
                f"Notes: {p['notes']}",
                f"Cycle: {result['estimatedCycle']}",
            ]
            if result["rankedOEMs"]:
                lines.append("Ranked OEMs: " + ", ".join(f"{o['oem']} ({o['country']})" for o in result["rankedOEMs"][:4]))
            return "\n".join(lines)
    return ""
