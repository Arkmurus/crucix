"""Minimal OEM priority list for the chat-triggered batch crawl intent.

Why this file exists
────────────────────
This file holds a curated Phase-1 priority list ARIA can use when the
user says "crawl the known OEMs" without pasting URLs. ARIA owns this
list directly — no external module dependency.

The research pipeline (`research_tasks._do_research_each`) only needs the
name — it uses the LLM + web_search to discover each OEM's official site
before running `extract_url_deep`, so no hardcoded URL is required.

Selection criteria for this list (Phase-1, April 2026)
──────────────────────────────────────────────────────
  - OEMs Arkmurus actively engages or tracks
  - Spread across the 7 priority capability categories:
      artillery, UAS/FPV, air defence, armoured vehicles, naval,
      small arms/munitions, electronic warfare
  - Geographic parity per the GLOBAL positioning rule — no single-region
    bias. Turkey / Israel / Korea / NATO / Gulf all represented.
"""
from __future__ import annotations

# Phase-1 priority OEMs. Each entry is a tuple (name, country, capability_tags).
# Names match the public brand the user would recognise — the research
# pipeline discovers the actual website via web_search on first call.
_PHASE_1_OEMS: list[tuple[str, str, list[str]]] = [
    # Turkey
    ("Baykar", "TR", ["uas", "fpv", "loitering_munitions"]),
    ("Roketsan", "TR", ["missiles", "air_defence", "artillery_rockets"]),
    ("ASELSAN", "TR", ["electronic_warfare", "c4isr", "air_defence"]),
    ("STM Defence", "TR", ["uas", "naval"]),
    ("Otokar", "TR", ["armoured_vehicles"]),
    ("MKEK", "TR", ["munitions", "artillery", "small_arms"]),

    # Israel
    ("Elbit Systems", "IL", ["c4isr", "uas", "artillery", "optronics"]),
    ("Israel Aerospace Industries", "IL", ["uas", "air_defence", "naval"]),
    ("Rafael Advanced Defense Systems", "IL", ["missiles", "air_defence", "loitering_munitions"]),

    # South Korea
    ("Hanwha Aerospace", "KR", ["artillery", "armoured_vehicles"]),
    ("Hyundai Rotem", "KR", ["armoured_vehicles", "tanks"]),
    ("LIG Nex1", "KR", ["missiles", "air_defence"]),
    ("KAI Korea Aerospace Industries", "KR", ["aircraft", "uas"]),

    # NATO / Western Europe
    ("Rheinmetall", "DE", ["artillery", "armoured_vehicles", "munitions"]),
    ("KNDS (Nexter + KMW)", "FR/DE", ["artillery", "armoured_vehicles"]),
    ("BAE Systems", "UK", ["naval", "armoured_vehicles", "electronic_warfare"]),
    ("Leonardo", "IT", ["c4isr", "helicopters", "electronic_warfare"]),
    ("Thales", "FR", ["c4isr", "air_defence", "electronic_warfare"]),
    ("MBDA", "UK/FR/IT/DE", ["missiles", "air_defence"]),
    ("Nammo", "NO", ["munitions", "artillery"]),
    ("Saab", "SE", ["uas", "air_defence", "armoured_vehicles"]),
    ("Kongsberg", "NO", ["missiles", "naval_weapons"]),
    ("Patria", "FI", ["armoured_vehicles", "mortars"]),

    # Czech / Balkans / Eastern EU
    ("Czechoslovak Group (CSG)", "CZ", ["armoured_vehicles", "artillery", "munitions"]),
    ("PGZ (Polska Grupa Zbrojeniowa)", "PL", ["artillery", "air_defence", "armoured_vehicles"]),
    ("ROMARM", "RO", ["small_arms", "munitions", "armoured_vehicles"]),

    # South Africa / Brazil / India / UAE (non-Western but non-competitor)
    ("Denel", "ZA", ["artillery", "munitions", "missiles"]),
    ("Paramount Group", "ZA", ["armoured_vehicles", "aircraft"]),
    ("Embraer Defense", "BR", ["aircraft", "c4isr"]),
    ("Avibras", "BR", ["artillery_rockets"]),
    ("Bharat Electronics (BEL)", "IN", ["c4isr", "electronic_warfare"]),
    ("EDGE Group", "AE", ["uas", "munitions", "missiles"]),
]


def phase_1_oem_names() -> list[str]:
    """Return the curated Phase-1 OEM name list (order is the crawl order)."""
    # R-F1001 - wire to brain
    from .engine_wiring import wire_success, wire_failure
    wire_success(
        module="oem_registry",
        summary="Phase 1 Oem Names",
        source_id="oem_registry:R-F1001",
    )

    return [name for (name, _country, _tags) in _PHASE_1_OEMS]


def phase_1_count() -> int:
    return len(_PHASE_1_OEMS)


def filter_by_capability(capability: str, limit: int = 15) -> list[str]:
    """Return Phase-1 OEMs whose capability_tags match the given capability.

    Matching is substring-based on the tag so "artillery" matches both
    "artillery" and "artillery_rockets". Falls back to the full Phase-1
    list if nothing matches or capability is empty.
    """
    if not capability:
        return phase_1_oem_names()[:limit]

    cap = capability.lower().strip()
    matches: list[str] = []
    for name, _country, tags in _PHASE_1_OEMS:
        if any(cap in t.lower() for t in tags):
            matches.append(name)
    if not matches:
        return phase_1_oem_names()[:limit]
    return matches[:limit]


# Capability aliases so user prose maps to our canonical tags.
_CAPABILITY_ALIASES = {
    "artillery": "artillery",
    "155mm": "artillery",
    "howitzer": "artillery",
    "self-propelled": "artillery",
    "fpv": "fpv",
    "drone": "uas",
    "drones": "uas",
    "uav": "uas",
    "uas": "uas",
    "loitering munition": "loitering_munitions",
    "kamikaze drone": "loitering_munitions",
    "air defence": "air_defence",
    "air defense": "air_defence",
    "sam": "air_defence",
    "manpads": "air_defence",
    "anti-aircraft": "air_defence",
    "tank": "tanks",
    "tanks": "tanks",
    "mbt": "tanks",
    "ifv": "armoured_vehicles",
    "apc": "armoured_vehicles",
    "armoured": "armoured_vehicles",
    "armored": "armoured_vehicles",
    "missile": "missiles",
    "missiles": "missiles",
    "naval": "naval",
    "frigate": "naval",
    "submarine": "naval",
    "radar": "c4isr",
    "electronic warfare": "electronic_warfare",
    "ew": "electronic_warfare",
    "small arms": "small_arms",
    "rifle": "small_arms",
    "ammunition": "munitions",
    "munitions": "munitions",
}


def canonicalise_capability(phrase: str) -> str:
    """Map a free-text capability phrase to a canonical tag. Returns '' if
    nothing matches, in which case callers should fall back to the full
    Phase-1 list."""
    if not phrase:
        return ""
    p = phrase.lower().strip()
    # Longest-alias-first so "air defence" beats "air"
    for alias in sorted(_CAPABILITY_ALIASES.keys(), key=len, reverse=True):
        if alias in p:
            return _CAPABILITY_ALIASES[alias]
    return ""

# R-F2119 §21a — wire failure handler for oem_registry
try:
    wire_failure(module="oem_registry", detail="module shutdown",
                gap_type="engine_failure", source="oem_registry:shutdown")
except Exception:
    pass
