"""Gulf OEM subsidiary structure — SAMI, EDGE, Tawazun, Barzan, Nimr.

Answers the question: "which Gulf subsidiary should we route this
requirement to?" Seeded from publicly available state-OEM disclosures
as of 2026-04-17. Product → subsidiary mapping is high-value because
EDGE alone has ~25 subsidiaries and direct routing matters.

Each entry:
  parent       — SAMI / EDGE / Tawazun / Barzan / TawazunIndustrial
  subsidiary   — trading name
  country      — parent country ISO2
  focus        — one-line product focus (verbatim where possible)
  products     — keyword list for fuzzy match
  website      — public website root (for operator verification)
  joint_venture — optional: 'Lockheed Martin', 'Thales', etc.
  notes        — optional commercial / compliance note
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("aria.intel.gulf_oem_structure")


@dataclass(frozen=True)
class GulfOEMSubsidiary:
    parent:         str
    subsidiary:     str
    country:        str
    focus:          str
    products:       tuple[str, ...]
    website:        str = ""
    joint_venture:  str = ""
    notes:          str = ""


# ═══════════════════════════════════════════════════════════════════════
# Saudi Arabian Military Industries (SAMI) — state OEM aggregator
# ═══════════════════════════════════════════════════════════════════════

_SAMI: tuple[GulfOEMSubsidiary, ...] = (
    GulfOEMSubsidiary(
        parent="SAMI", subsidiary="SAMI Aerospace", country="SA",
        focus="Aerostructures + MRO + C-130 / F-15 sustainment",
        products=("aerostructures", "mro", "f-15", "c-130", "composites"),
        website="https://www.sami.com.sa",
    ),
    GulfOEMSubsidiary(
        parent="SAMI", subsidiary="SAMI Advanced Electronics", country="SA",
        focus="Comms, EW, radar systems, sensor integration",
        products=("radar", "ew", "electronic warfare", "comms", "sensor"),
        notes="Formed from Advanced Electronics Company (AEC) absorption 2020.",
    ),
    GulfOEMSubsidiary(
        parent="SAMI", subsidiary="SAMI Land Systems", country="SA",
        focus="Armoured vehicles, howitzer localisation",
        products=("apc", "ifv", "armoured vehicle", "howitzer", "artillery"),
        joint_venture="General Dynamics Land Systems (LAV-25 family)",
    ),
    GulfOEMSubsidiary(
        parent="SAMI", subsidiary="SAMI-Navantia", country="SA",
        focus="Naval — F-90 / Al Jubail / Avante 2200 corvettes",
        products=("corvette", "frigate", "naval", "avante 2200"),
        joint_venture="Navantia (Spain)",
    ),
    GulfOEMSubsidiary(
        parent="SAMI", subsidiary="SAMI-Thales", country="SA",
        focus="Radar, EW suite, command-and-control integration",
        products=("radar", "c2", "ew"),
        joint_venture="Thales",
    ),
    GulfOEMSubsidiary(
        parent="SAMI", subsidiary="Alsalam Aerospace Industries", country="SA",
        focus="MRO for Boeing 707, E-3 AWACS, tactical aircraft",
        products=("mro", "awacs", "e-3", "b707", "heavy maintenance"),
        website="https://www.alsalam.aero",
    ),
)


# ═══════════════════════════════════════════════════════════════════════
# EDGE Group — UAE state-owned conglomerate (~25 subsidiaries)
# ═══════════════════════════════════════════════════════════════════════

_EDGE: tuple[GulfOEMSubsidiary, ...] = (
    # ── Platforms ─────────────────────────────────────────────────────
    GulfOEMSubsidiary(
        parent="EDGE", subsidiary="NIMR Automotive", country="AE",
        focus="Tactical / armoured wheeled vehicles — AJBAN, RILA, HAFEET",
        products=("ajban", "rila", "hafeet", "armoured vehicle", "apc", "mrap"),
        website="https://www.nimrautomotive.com",
    ),
    GulfOEMSubsidiary(
        parent="EDGE", subsidiary="ADSB (Abu Dhabi Ship Building)", country="AE",
        focus="Naval build + MRO — Ghannatha patrol, Baynunah corvettes",
        products=("corvette", "patrol vessel", "ghannatha", "baynunah", "naval mro"),
    ),
    GulfOEMSubsidiary(
        parent="EDGE", subsidiary="ADASI", country="AE",
        focus="Unmanned systems — Reach-S, Garmoosha, Qasef, Yabhon",
        products=("uav", "drone", "reach-s", "garmoosha", "qasef", "yabhon", "loitering"),
    ),
    # ── Munitions ─────────────────────────────────────────────────────
    GulfOEMSubsidiary(
        parent="EDGE", subsidiary="Caracal", country="AE",
        focus="Small arms — CMP-9, CSR-338, CAR-816, pistols",
        products=("pistol", "rifle", "smg", "caracal cmp", "cmp-9", "cmp9", "car-816"),
    ),
    GulfOEMSubsidiary(
        parent="EDGE", subsidiary="Al Tariq", country="AE",
        focus="Precision guided munitions — Al Tariq LR / ER",
        products=("guided munition", "pgm", "smart bomb", "al tariq"),
    ),
    GulfOEMSubsidiary(
        parent="EDGE", subsidiary="HALCON", country="AE",
        focus="Precision guided weapons + missiles — SkyKnight, Desert Sting",
        products=("missile", "skyknight", "desert sting", "sam", "agm"),
    ),
    GulfOEMSubsidiary(
        parent="EDGE", subsidiary="Burkan Munitions", country="AE",
        focus="Mortars, artillery ammunition, small-calibre",
        products=("mortar", "artillery ammo", "small calibre", "munitions"),
    ),
    GulfOEMSubsidiary(
        parent="EDGE", subsidiary="Lahab Defence Systems", country="AE",
        focus="Artillery + tank ammunition, fuses",
        products=("tank ammunition", "artillery", "fuse", "105mm", "120mm", "155mm"),
    ),
    # ── Electronic warfare / cyber ────────────────────────────────────
    GulfOEMSubsidiary(
        parent="EDGE", subsidiary="Beacon Red", country="AE",
        focus="Cyber security, training, digital defence",
        products=("cyber", "red team", "cybersecurity", "soc"),
    ),
    GulfOEMSubsidiary(
        parent="EDGE", subsidiary="SIGN4L", country="AE",
        focus="Electronic warfare + spectrum operations",
        products=("ew", "electronic warfare", "jamming", "spectrum"),
    ),
    GulfOEMSubsidiary(
        parent="EDGE", subsidiary="EDGE Autonomous", country="AE",
        focus="Autonomous maritime + land unmanned systems",
        products=("usv", "ugv", "autonomous", "unmanned"),
    ),
    # ── Mission support ──────────────────────────────────────────────
    GulfOEMSubsidiary(
        parent="EDGE", subsidiary="AMMROC", country="AE",
        focus="Rotary + fixed-wing MRO — Black Hawk, C-130, CH-47",
        products=("mro", "black hawk", "uh-60", "c-130", "ch-47", "rotary mro"),
    ),
    GulfOEMSubsidiary(
        parent="EDGE", subsidiary="GAL (Global Aerospace Logistics)", country="AE",
        focus="Fleet support + logistics for rotary-wing operators",
        products=("mro", "fleet support", "logistics"),
    ),
    GulfOEMSubsidiary(
        parent="EDGE", subsidiary="Horizon Flight Academy", country="AE",
        focus="Pilot training (rotary + fixed wing)",
        products=("training", "pilot training", "flight academy"),
    ),
    GulfOEMSubsidiary(
        parent="EDGE", subsidiary="EARTH (Emirates Advanced Research & Tech Holding)", country="AE",
        focus="R&D / communications / specialist electronics",
        products=("r&d", "communications", "tactical radio"),
    ),
)


# ═══════════════════════════════════════════════════════════════════════
# Tawazun Economic Council — UAE offset administrator
# ═══════════════════════════════════════════════════════════════════════

_TAWAZUN: tuple[GulfOEMSubsidiary, ...] = (
    GulfOEMSubsidiary(
        parent="Tawazun", subsidiary="Tawazun Industrial Park", country="AE",
        focus="Industrial-zone land for foreign-OEM JV / offset fulfilment",
        products=("industrial land", "jv facility", "offset fulfilment"),
    ),
    GulfOEMSubsidiary(
        parent="Tawazun", subsidiary="Tawazun Strategic Development Fund", country="AE",
        focus="Sovereign co-investment vehicle for defence JVs",
        products=("sovereign investment", "jv capital"),
    ),
)


# ═══════════════════════════════════════════════════════════════════════
# Barzan Holdings — Qatar defence investment arm
# ═══════════════════════════════════════════════════════════════════════

_BARZAN: tuple[GulfOEMSubsidiary, ...] = (
    GulfOEMSubsidiary(
        parent="Barzan", subsidiary="Barzan Aeronautical", country="QA",
        focus="Aerospace R&D + unmanned aerial systems",
        products=("uav", "aerospace r&d", "drone"),
    ),
    GulfOEMSubsidiary(
        parent="Barzan", subsidiary="Barzan Maritime", country="QA",
        focus="Naval ship-building + integration",
        products=("naval", "frigate", "ship building"),
    ),
)


ALL_SUBSIDIARIES: tuple[GulfOEMSubsidiary, ...] = _SAMI + _EDGE + _TAWAZUN + _BARZAN


# ═══════════════════════════════════════════════════════════════════════
# Public API
# ═══════════════════════════════════════════════════════════════════════

def find_subsidiary_for_product(product_keyword: str) -> list[dict[str, Any]]:
    """Given a product keyword, return matching subsidiaries ranked by parent scale."""
    if not product_keyword or not product_keyword.strip():
        return []
    needle = product_keyword.lower().strip()
    matches: list[GulfOEMSubsidiary] = []
    for sub in ALL_SUBSIDIARIES:
        if any(p in needle or needle in p for p in sub.products):
            matches.append(sub)
    # Parent-scale ordering: EDGE (25 subs) > SAMI (6) > Tawazun > Barzan
    _scale = {"EDGE": 4, "SAMI": 3, "Tawazun": 2, "Barzan": 1}
    matches.sort(key=lambda s: -_scale.get(s.parent, 0))
    # R-F996 — wire to brain
    from .engine_wiring import wire_success, wire_failure
    wire_success(
        module="gulf_oem_structure",
        summary="Find Subsidiary For Product",
        source_id="gulf_oem_structure:R-F996",
    )

    return [_to_dict(s) for s in matches]


def list_subsidiaries_by_parent(parent: str) -> list[dict[str, Any]]:
    parent = (parent or "").strip()
    return [_to_dict(s) for s in ALL_SUBSIDIARIES if s.parent.lower() == parent.lower()]


def get_gulf_oem_context(query: str, max_hits: int = 4) -> str:
    """Return a context block for chat injection when a Gulf OEM product
    keyword is mentioned."""
    if not query or not query.strip():
        return ""
    matches = find_subsidiary_for_product(query)
    if not matches:
        return ""
    lines = ["[GULF-OEM MATCH]"]
    for m in matches[:max_hits]:
        jv = f" — JV: {m['joint_venture']}" if m.get("joint_venture") else ""
        lines.append(
            f"• {m['subsidiary']} ({m['parent']}, {m['country']}): {m['focus']}{jv}"
        )
    return "\n".join(lines)


def summary() -> dict[str, Any]:
    by_parent: dict[str, int] = {}
    for s in ALL_SUBSIDIARIES:
        by_parent[s.parent] = by_parent.get(s.parent, 0) + 1
    return {
        "total_subsidiaries": len(ALL_SUBSIDIARIES),
        "by_parent": by_parent,
    }


def _to_dict(s: GulfOEMSubsidiary) -> dict[str, Any]:
    return {
        "parent": s.parent,
        "subsidiary": s.subsidiary,
        "country": s.country,
        "focus": s.focus,
        "products": list(s.products),
        "website": s.website,
        "joint_venture": s.joint_venture,
        "notes": s.notes,
    }

# R-F2119 §21a — wire failure handler for gulf_oem_structure
try:
    wire_failure(module="gulf_oem_structure", detail="module shutdown",
                gap_type="engine_failure", source="gulf_oem_structure:shutdown")
except Exception:
    pass
