"""KSA Vision 2030 defence-localisation tracker.

Tracks Saudi Vision 2030's explicit 50%-by-2030 domestic defence
localisation target, year-by-year intermediate goals, key programmes
under GAMI oversight, and the offset multiplier regime that governs
foreign-OEM market access.

Factual anchor points (publicly disclosed):
  - Baseline 2016:   <2% domestic defence spend
  - 2023 milestone:  15% localisation (GAMI annual report)
  - 2025 interim:    25% (public SAMI / GAMI target)
  - 2030 target:     50% — formal Vision 2030 KPI
Programme-specific targets may run ahead of the aggregate — the
Al Jubail Multi-Mission Surface Combatant programme cites 60%+
localisation by end-of-programme.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("aria.intel.vision_2030_tracker")


# ═══════════════════════════════════════════════════════════════════════
# Target curve
# ═══════════════════════════════════════════════════════════════════════

_LOCALISATION_CURVE: list[tuple[int, float, str]] = [
    (2016, 2.0,  "Baseline before Vision 2030 launch."),
    (2020, 8.0,  "GAMI established 2017; SAMI 2017. Infrastructure year."),
    (2022, 11.0, "IDEX + ancillary programmes online."),
    (2023, 15.0, "First formal GAMI milestone disclosed."),
    (2025, 25.0, "Publicly-stated interim Vision 2030 KPI."),
    (2027, 35.0, "Mid-cycle target — tracking rather than hard milestone."),
    (2030, 50.0, "Formal Vision 2030 KPI — 50% domestic defence manufacturing."),
]


@dataclass(frozen=True)
class ProgrammeMilestone:
    programme:      str
    category:       str          # "aerospace" | "land" | "naval" | "c4isr" | "training"
    prime:          str
    status:         str          # "contract" | "production" | "planning"
    localisation:   int          # percentage
    year_target:    int
    notes:          str = ""


_KEY_PROGRAMMES: tuple[ProgrammeMilestone, ...] = (
    ProgrammeMilestone(
        programme="Multi-Mission Surface Combatant (Al Jubail-class)",
        category="naval", prime="SAMI-Navantia",
        status="production", localisation=60, year_target=2026,
        notes="Avante 2200 variant. 5 hulls. Spain + KSA build-share.",
    ),
    ProgrammeMilestone(
        programme="Localised 155mm Howitzer Production",
        category="land", prime="SAMI Land Systems",
        status="contract", localisation=50, year_target=2027,
        notes="Artillery localisation under General Dynamics Land Systems JV.",
    ),
    ProgrammeMilestone(
        programme="Airbus H145 KSA Final Assembly",
        category="aerospace", prime="SAMI Aerospace",
        status="planning", localisation=40, year_target=2027,
        notes="Light-utility rotary assembly in KSA for RSLF + SANG demand.",
    ),
    ProgrammeMilestone(
        programme="F-15SA Sustainment + MRO",
        category="aerospace", prime="Alsalam Aerospace / SAMI",
        status="production", localisation=35, year_target=2025,
        notes="Heavy maintenance migration from CONUS to KSA.",
    ),
    ProgrammeMilestone(
        programme="Patriot Missile Localised Maintenance",
        category="c4isr", prime="SAMI-Thales + SAMI AE",
        status="contract", localisation=30, year_target=2028,
        notes="Post-Vision 2030 extension — in-country missile sustainment.",
    ),
    ProgrammeMilestone(
        programme="Training Systems Localisation (Flight + Simulator)",
        category="training", prime="CAE / Raytheon KSA JV",
        status="production", localisation=45, year_target=2025,
    ),
)


# ═══════════════════════════════════════════════════════════════════════
# Offset multiplier regime (GAMI)
# ═══════════════════════════════════════════════════════════════════════
#
# GAMI offset multipliers apply to foreign-supplied defence content.
# Numbers below reflect GAMI-disclosed tiers as of 2024 — reconfirm
# before relying on them in a live mandate.

_OFFSET_MULTIPLIERS: tuple[dict[str, Any], ...] = (
    {"activity": "Direct defence manufacturing in KSA",    "multiplier": 3.0,
     "notes": "Highest credit; includes local hiring, IP transfer."},
    {"activity": "Technology transfer to Saudi entity",     "multiplier": 2.5,
     "notes": "Must include documented IP + training."},
    {"activity": "KSA supply-chain sourcing",               "multiplier": 2.0,
     "notes": "Tier-1 + Tier-2 Saudi suppliers."},
    {"activity": "Training of Saudi defence personnel",     "multiplier": 1.5},
    {"activity": "R&D partnership with KAUST / KACST",      "multiplier": 2.0},
    {"activity": "Export-route marketing of KSA-made kit",  "multiplier": 1.5,
     "notes": "Encourages Saudi re-export — credit for hitting new markets."},
    {"activity": "Non-defence industrial investment in KSA","multiplier": 0.5,
     "notes": "Soft credit — minor offset weight."},
)


# ═══════════════════════════════════════════════════════════════════════
# Public API
# ═══════════════════════════════════════════════════════════════════════

def localisation_target_for_year(year: int) -> dict[str, Any]:
    """Interpolate the Vision 2030 target for any year between 2016–2030."""
    if year <= _LOCALISATION_CURVE[0][0]:
        y, pct, note = _LOCALISATION_CURVE[0]
        return {"year": year, "target_pct": pct, "interpolated": False, "note": note}
    if year >= _LOCALISATION_CURVE[-1][0]:
        y, pct, note = _LOCALISATION_CURVE[-1]
        return {"year": year, "target_pct": pct, "interpolated": False, "note": note}
    for i in range(len(_LOCALISATION_CURVE) - 1):
        y1, p1, _ = _LOCALISATION_CURVE[i]
        y2, p2, _ = _LOCALISATION_CURVE[i + 1]
        if y1 <= year <= y2:
            ratio = (year - y1) / max(y2 - y1, 1)
            pct = p1 + ratio * (p2 - p1)
            return {"year": year, "target_pct": round(pct, 1), "interpolated": True,
                    "between": f"{y1}({p1}%)–{y2}({p2}%)"}
    return {"year": year, "target_pct": None, "interpolated": False, "note": ""}


def programmes_for_category(category: str) -> list[dict[str, Any]]:
    cat = (category or "").lower().strip()
    return [
        {"programme": p.programme, "prime": p.prime, "status": p.status,
         "localisation_pct": p.localisation, "year_target": p.year_target,
         "category": p.category, "notes": p.notes}
        for p in _KEY_PROGRAMMES if p.category == cat
    ]


def all_programmes() -> list[dict[str, Any]]:
    return [
        {"programme": p.programme, "prime": p.prime, "status": p.status,
         "localisation_pct": p.localisation, "year_target": p.year_target,
         "category": p.category, "notes": p.notes}
        for p in _KEY_PROGRAMMES
    ]


def offset_multipliers() -> list[dict[str, Any]]:
    return [dict(e) for e in _OFFSET_MULTIPLIERS]


def get_vision_2030_context(query: str) -> str:
    """Chat-context injection — only fires when Vision 2030 / KSA / GAMI
    / SAMI keywords are in the query."""
    if not query or not query.strip():
        return ""
    q = query.lower()
    triggers = ("vision 2030", "ksa", "saudi", "gami", "sami", "riyadh", "localisation")
    if not any(t in q for t in triggers):
        return ""
    curve = ", ".join(f"{y}: {p}%" for y, p, _ in _LOCALISATION_CURVE)
    lines = [
        "[VISION 2030 TRACKER]",
        f"Localisation curve — {curve}.",
        f"Formal 2030 KPI: 50% domestic defence manufacturing.",
        "Key programmes tracked:",
    ]
    for p in _KEY_PROGRAMMES[:4]:
        lines.append(
            f"  • {p.programme} ({p.prime}, {p.status}) — "
            f"{p.localisation}% target by {p.year_target}"
        )
    lines.append("Offset multipliers: direct defence manufacturing 3×, "
                 "tech transfer 2.5×, KSA supply-chain 2×, training 1.5×.")
    # R-F996 — wire to brain
    from .engine_wiring import wire_success, wire_failure
    wire_success(
        module="vision_2030_tracker",
        summary="Get Vision 2030 Context",
        source_id="vision_2030_tracker:R-F996",
    )

    return "\n".join(lines)


def summary() -> dict[str, Any]:
    by_status: dict[str, int] = {}
    for p in _KEY_PROGRAMMES:
        by_status[p.status] = by_status.get(p.status, 0) + 1
    return {
        "programmes_tracked": len(_KEY_PROGRAMMES),
        "by_status": by_status,
        "curve_anchor_years": [y for y, _, _ in _LOCALISATION_CURVE],
        "final_target_pct": _LOCALISATION_CURVE[-1][1],
    }

# R-F2119 §21a — wire failure handler for vision_2030_tracker
try:
    wire_failure(module="vision_2030_tracker", detail="module shutdown",
                gap_type="engine_failure", source="vision_2030_tracker:shutdown")
except Exception:
    pass
