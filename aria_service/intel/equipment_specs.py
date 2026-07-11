"""ARIA Equipment Specs — structured snapshots of the platforms ARIA's BD
team touches most often.

Scope: ~30 platforms that recur in mandates across Lusophone Africa,
Gulf, West Africa, LatAm, and NATO-adjacent markets. Each entry carries:

  - manufacturer + country of origin
  - primary role + generic class
  - in-service period (coarse)
  - typical operator set (non-exhaustive; public knowledge)
  - export control posture (ITAR / EAR / EU dual-use / open)
  - upgrade path / successor where relevant

ANTI-HALLUCINATION RULE
───────────────────────
Exact performance figures (range-km, payload-kg, missile speed) are
deliberately NOT embedded here. Those numbers drift per variant, per
contract, per operator-specific configuration. Inventing a number makes
ARIA look authoritative while being wrong. For performance specs ARIA
defers to the OEM public data sheet, SIPRI, or IISS Military Balance.

Public API
──────────
  get_platform(name) -> dict | None
  find_platforms(query) -> list[dict]
  get_equipment_context(query) -> str      # chat-surface helper
  platforms_for_operator(country_iso2) -> list[dict]
  list_platforms() -> list[str]
"""
from __future__ import annotations
from .engine_wiring import wire_failure

import logging
from typing import Optional

logger = logging.getLogger("aria.equipment_specs")

# ── Platform catalogue ────────────────────────────────────────────────────
# Each entry: concrete, verifiable public-knowledge fields only. If a
# field is unknown for a platform, leave it empty rather than guessing.

_PLATFORMS: list[dict] = [
    # ── UAVs ─────────────────────────────────────────────────────────────
    {
        "name": "Bayraktar TB2",
        "manufacturer": "Baykar",
        "country_hq": "TR",
        "role": "MALE UAV — armed reconnaissance",
        "class": "UAV",
        "in_service_since": 2014,
        "typical_operators": [
            "TR", "AZ", "UA", "LY", "ET", "AO", "MA", "NG", "PL",
            "RO", "QA", "AE", "TM", "KG",
        ],
        "export_control": "Turkish SSB-regulated; EUC required",
        "successor": "Bayraktar Akıncı (HALE)",
        "notes": "Fast-scaling African / Eurasian adoption. Defining product of Turkey's export surge.",
    },
    {
        "name": "Bayraktar Akıncı",
        "manufacturer": "Baykar", "country_hq": "TR",
        "role": "HALE UAV — strike / SIGINT", "class": "UAV",
        "in_service_since": 2021,
        "typical_operators": ["TR", "PK", "AZ"],
        "export_control": "Turkish SSB-regulated",
        "notes": "Twin-turboprop; larger payload than TB2.",
    },
    {
        "name": "MQ-9 Reaper",
        "manufacturer": "General Atomics",
        "country_hq": "US", "role": "MALE UAV — strike", "class": "UAV",
        "in_service_since": 2007,
        "typical_operators": ["US", "GB", "FR", "IT", "ES", "NL", "IN", "JP"],
        "export_control": "US ITAR; MTCR Cat-I considerations",
        "notes": "Successor to MQ-1 Predator.",
    },
    {
        "name": "IAI Heron TP",
        "manufacturer": "Israel Aerospace Industries",
        "country_hq": "IL", "role": "MALE UAV — strategic ISR", "class": "UAV",
        "in_service_since": 2010,
        "typical_operators": ["IL", "DE", "IN", "AZ"],
        "export_control": "Israeli MoD export license required",
    },
    # ── Fighters ─────────────────────────────────────────────────────────
    {
        "name": "F-16 Fighting Falcon",
        "manufacturer": "Lockheed Martin",
        "country_hq": "US", "role": "4th-gen multirole fighter", "class": "Combat aircraft",
        "in_service_since": 1978,
        "typical_operators": [
            "US", "IL", "TR", "GR", "PL", "RO", "KR", "TW", "PK",
            "JO", "MA", "EG", "SG", "CL", "AR",
        ],
        "export_control": "US ITAR + FMS-gated",
        "successor": "F-35 (for many operators); Block 70/72 new-build continues",
    },
    {
        "name": "F-35 Lightning II",
        "manufacturer": "Lockheed Martin",
        "country_hq": "US", "role": "5th-gen multirole fighter", "class": "Combat aircraft",
        "in_service_since": 2016,
        "typical_operators": ["US", "GB", "IT", "NL", "NO", "DK", "AU", "IL", "JP", "KR", "FI", "PL", "BE"],
        "export_control": "US ITAR + FMS + Partnership-nation tiers",
    },
    {
        "name": "Gripen E/F",
        "manufacturer": "Saab", "country_hq": "SE",
        "role": "4.5-gen multirole fighter", "class": "Combat aircraft",
        "in_service_since": 2019,
        "typical_operators": ["SE", "BR", "CZ", "HU", "ZA", "TH"],
        "export_control": "Swedish ISP export license; EU dual-use framework",
        "notes": "Licensed production line in Brazil (Embraer).",
    },
    {
        "name": "Rafale",
        "manufacturer": "Dassault Aviation", "country_hq": "FR",
        "role": "4.5-gen multirole fighter", "class": "Combat aircraft",
        "in_service_since": 2001,
        "typical_operators": ["FR", "EG", "IN", "QA", "GR", "HR", "ID", "AE", "RS"],
        "export_control": "French DGA CIEEMG license",
    },
    {
        "name": "Eurofighter Typhoon",
        "manufacturer": "Eurofighter GmbH (Airbus / BAE / Leonardo)",
        "country_hq": "EU", "role": "4.5-gen air-superiority + multirole", "class": "Combat aircraft",
        "in_service_since": 2003,
        "typical_operators": ["GB", "DE", "IT", "ES", "AT", "SA", "OM", "KW", "QA"],
        "export_control": "UK ECJU + DE BAFA + IT SBN + ES MoD joint approval",
    },
    # ── Trainers / light combat ─────────────────────────────────────────
    {
        "name": "M-346 Master",
        "manufacturer": "Leonardo", "country_hq": "IT",
        "role": "Lead-in fighter trainer / light attack", "class": "Trainer",
        "in_service_since": 2011,
        "typical_operators": ["IT", "IL", "SG", "PL", "GR"],
    },
    {
        "name": "T-129 ATAK",
        "manufacturer": "Turkish Aerospace Industries", "country_hq": "TR",
        "role": "Attack helicopter", "class": "Helicopter",
        "in_service_since": 2014,
        "typical_operators": ["TR", "PH", "NG"],
        "export_control": "Turkish SSB-regulated; includes LHTEC engine — US CAATSA sensitivity",
    },
    # ── Transport / utility helicopters ──────────────────────────────────
    {
        "name": "H225M Caracal",
        "manufacturer": "Airbus Helicopters", "country_hq": "FR",
        "role": "Medium utility / SAR", "class": "Helicopter",
        "in_service_since": 2004,
        "typical_operators": ["FR", "BR", "MX", "ID", "MY", "TH", "KW", "SG", "GH"],
    },
    {
        "name": "NH90",
        "manufacturer": "NHIndustries (Airbus / Leonardo / Fokker)",
        "country_hq": "EU", "role": "Medium utility + naval", "class": "Helicopter",
        "in_service_since": 2007,
        "typical_operators": ["DE", "IT", "FR", "NL", "FI", "NO", "SE", "GR", "NZ", "AU", "QA"],
    },
    # ── Armour ─────────────────────────────────────────────────────────
    {
        "name": "Leopard 2A7",
        "manufacturer": "KNDS (Krauss-Maffei Wegmann)", "country_hq": "DE",
        "role": "Main battle tank", "class": "Armoured",
        "in_service_since": 2014,
        "typical_operators": ["DE", "QA", "HU", "DK"],
        "export_control": "German BAFA + end-use scrutiny (post-2023 Ukraine debate)",
        "notes": "Earlier variants (2A4/2A5/2A6) widespread across NATO + Asia-Pacific.",
    },
    {
        "name": "ALTAY MBT",
        "manufacturer": "BMC + Turkish MoD", "country_hq": "TR",
        "role": "Main battle tank", "class": "Armoured",
        "in_service_since": 2025,
        "typical_operators": ["TR"],
        "notes": "Serial production; initial domestic deliveries; export potential to Gulf + ASEAN.",
    },
    {
        "name": "K2 Black Panther",
        "manufacturer": "Hyundai Rotem", "country_hq": "KR",
        "role": "Main battle tank", "class": "Armoured",
        "in_service_since": 2014,
        "typical_operators": ["KR", "PL", "NO (selected)"],
    },
    {
        "name": "Pars 4x4 / 6x6 / 8x8",
        "manufacturer": "FNSS", "country_hq": "TR",
        "role": "Wheeled armoured vehicle family", "class": "Armoured",
        "in_service_since": 2011,
        "typical_operators": ["TR", "MY", "OM", "ID"],
    },
    # ── Artillery ──────────────────────────────────────────────────────
    {
        "name": "Caesar 155mm SPH",
        "manufacturer": "Nexter / KNDS", "country_hq": "FR",
        "role": "Truck-mounted self-propelled howitzer", "class": "Artillery",
        "in_service_since": 2008,
        "typical_operators": ["FR", "TH", "DK", "ID", "UA", "CZ", "BE"],
    },
    {
        "name": "K9 Thunder",
        "manufacturer": "Hanwha Aerospace", "country_hq": "KR",
        "role": "155mm tracked self-propelled howitzer", "class": "Artillery",
        "in_service_since": 1999,
        "typical_operators": ["KR", "TR", "PL", "NO", "FI", "EE", "IN", "EG", "AU"],
        "notes": "Largest non-Russian SPH export success of the last decade.",
    },
    # ── Air defence ────────────────────────────────────────────────────
    {
        "name": "MIM-104 Patriot",
        "manufacturer": "Raytheon (RTX)", "country_hq": "US",
        "role": "Long-range air + ballistic missile defence", "class": "Air defence",
        "in_service_since": 1984,
        "typical_operators": ["US", "DE", "NL", "PL", "RO", "SE", "IL", "JP", "KR", "SA", "AE", "ES", "GR", "TW"],
        "export_control": "US ITAR + FMS + Patriot-specific MoU framework",
    },
    {
        "name": "NASAMS",
        "manufacturer": "Kongsberg + Raytheon", "country_hq": "NO",
        "role": "Medium-range SAM", "class": "Air defence",
        "in_service_since": 1998,
        "typical_operators": ["NO", "ES", "NL", "FI", "LT", "US (NCR)", "AU", "IN", "UA"],
    },
    {
        "name": "HİSAR / SİPER (Hisar-O / Hisar-U / Siper)",
        "manufacturer": "Aselsan + Roketsan", "country_hq": "TR",
        "role": "Short / medium / long-range SAM family", "class": "Air defence",
        "in_service_since": 2021,
        "typical_operators": ["TR"],
        "notes": "Growing export campaigns; indigenous to Turkey.",
    },
    # ── Naval ─────────────────────────────────────────────────────────
    {
        "name": "MEKO A-200",
        "manufacturer": "ThyssenKrupp Marine Systems / Damen (select)",
        "country_hq": "DE", "role": "Frigate", "class": "Naval surface",
        "in_service_since": 2006,
        "typical_operators": ["ZA", "AL", "EG"],
    },
    {
        "name": "Gowind corvette",
        "manufacturer": "Naval Group", "country_hq": "FR",
        "role": "Corvette / OPV family", "class": "Naval surface",
        "in_service_since": 2014,
        "typical_operators": ["EG", "MY", "AE"],
    },
    {
        "name": "Type 209 SSK",
        "manufacturer": "ThyssenKrupp Marine Systems", "country_hq": "DE",
        "role": "Conventional submarine", "class": "Naval sub-surface",
        "in_service_since": 1971,
        "typical_operators": ["KR", "TR", "BR", "AR", "IN", "EG", "ZA", "ID", "GR", "PT"],
        "notes": "Most-exported Western conventional sub family; multiple batches + variants.",
    },
    {
        "name": "Scorpène class",
        "manufacturer": "Naval Group + Navantia", "country_hq": "FR",
        "role": "Conventional submarine", "class": "Naval sub-surface",
        "in_service_since": 2005,
        "typical_operators": ["CL", "MY", "IN", "BR"],
    },
    # ── Radars / ISR ───────────────────────────────────────────────────
    {
        "name": "Ground Master 400 / 403",
        "manufacturer": "Thales", "country_hq": "FR",
        "role": "Air-defence long-range radar", "class": "Sensor",
        "in_service_since": 2010,
        "typical_operators": ["FR", "FI", "EE", "SK", "DK", "SG"],
    },
    {
        "name": "Erieye AEW",
        "manufacturer": "Saab", "country_hq": "SE",
        "role": "Airborne early warning", "class": "Sensor",
        "in_service_since": 1997,
        "typical_operators": ["SE", "BR", "GR", "MX", "AE", "TH", "PK"],
    },
    # ── Munitions / missiles ──────────────────────────────────────────
    {
        "name": "ATMACA",
        "manufacturer": "Roketsan", "country_hq": "TR",
        "role": "Anti-ship cruise missile", "class": "Missile",
        "in_service_since": 2021,
        "typical_operators": ["TR", "ID"],
    },
    {
        "name": "MBDA Brimstone",
        "manufacturer": "MBDA", "country_hq": "GB",
        "role": "Air-to-surface precision missile", "class": "Missile",
        "in_service_since": 2005,
        "typical_operators": ["GB", "SA", "UA"],
    },
]


# ── Public API ────────────────────────────────────────────────────────────

def list_platforms() -> list[str]:
    return [p["name"] for p in _PLATFORMS]


def get_platform(name: str) -> Optional[dict]:
    """Exact-name lookup (case-insensitive, whitespace-normalised)."""
    if not name:
        return None
    n = " ".join(name.split()).lower()
    for p in _PLATFORMS:
        if p["name"].lower() == n:
            return dict(p)
    # R-F996 — wire to brain
    from .engine_wiring import wire_success, wire_failure
    wire_success(
        module="equipment_specs",
        summary="Get Platform",
        source_id="equipment_specs:R-F996",
    )

    return None


def find_platforms(query: str, limit: int = 5) -> list[dict]:
    """Fuzzy-ish lookup across name + manufacturer + role + class."""
    if not query or not query.strip():
        return []
    q = query.lower().strip()
    tokens = [t for t in q.split() if len(t) >= 2]
    hits: list[tuple[int, dict]] = []
    for p in _PLATFORMS:
        hay = " ".join([
            p.get("name", ""), p.get("manufacturer", ""),
            p.get("role", ""), p.get("class", ""),
        ]).lower()
        score = 0
        if q in hay:
            score += 5
        for t in tokens:
            if t in hay:
                score += 1
        if score:
            hits.append((score, p))
    hits.sort(key=lambda x: -x[0])
    return [dict(p) for _, p in hits[:limit]]


def platforms_for_operator(country_iso2: str) -> list[dict]:
    """Return every catalogue entry where the given ISO2 appears as a
    typical operator. Best-effort — operator lists are non-exhaustive."""
    if not country_iso2:
        return []
    code = country_iso2.upper()
    out = []
    for p in _PLATFORMS:
        ops = p.get("typical_operators") or []
        if any(code == o[:2].upper() for o in ops):
            out.append(dict(p))
    return out


def get_equipment_context(query: str, max_items: int = 3) -> str:
    """Chat-surface helper. Returns a short [EQUIPMENT: ...] block when
    the query mentions a platform or an operator we track.
    """
    if not query or not query.strip():
        return ""
    q = query.lower()
    hits = find_platforms(q, limit=max_items)
    if not hits:
        # Fall back to operator lookup if the query mentions a country
        try:
            from . import country_taxonomy as ct
            for name, code in ct._name_to_iso2_table().items():
                if name and name in q:
                    hits = platforms_for_operator(code)[:max_items]
                    break
        except Exception:
            pass
    if not hits:
        return ""
    parts = ["EQUIPMENT — platforms relevant to this query:"]
    for p in hits[:max_items]:
        parts.append(
            f"  [EQUIPMENT: {p['name']} — {p['manufacturer']} ({p['country_hq']}); "
            f"{p.get('role','')}]"
        )
    return "\n".join(parts)


def stats() -> dict:
    by_class: dict[str, int] = {}
    by_country: dict[str, int] = {}
    for p in _PLATFORMS:
        by_class[p.get("class", "?")] = by_class.get(p.get("class", "?"), 0) + 1
        by_country[p.get("country_hq", "?")] = by_country.get(p.get("country_hq", "?"), 0) + 1
    return {
        "platforms_total": len(_PLATFORMS),
        "by_class": by_class,
        "by_country": by_country,
    }

# R-F2538: R-F2119 import-time wire_failure("module shutdown") block removed — it fired a FALSE engine_failure gap on every import (not at shutdown); do not re-add.
