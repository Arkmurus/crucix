"""Political risk index — conflict trajectory per country.

Per-country risk tier for every market ARIA covers. Seeds from three
public indicators then blends:
  - Fund For Peace Fragile States Index (FSI) — 2023 latest publicly
    disclosed scores; higher = more fragile
  - ACLED event-density signal — relative conflict-event intensity
  - Crisis Group CrisisWatch tag — monthly updated "deteriorated /
    improved / stable" marker

Output tier:
  stable      — FSI < 60 AND no active major conflict
  watch       — FSI 60-80 OR recent CrisisWatch deterioration
  elevated    — FSI 80-95 OR active regional conflict proximity
  critical    — FSI > 95 OR active armed conflict / coup / embargo

This is a SEED — intended to be refreshed quarterly. The
WEEKLY-CRISISWATCH autonomous task already ingests Crisis Group;
downstream we should update entries here from that flow, but
for now the seed is hand-curated to the 2026-04 baseline.
"""
from __future__ import annotations
from .engine_wiring import wire_failure

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("aria.intel.political_risk_index")


@dataclass(frozen=True)
class PoliticalRiskEntry:
    iso2:         str
    country:      str
    tier:         str              # stable | watch | elevated | critical
    fsi_band:     str              # "<60" | "60-80" | "80-95" | ">95"
    trajectory:   str              # "improving" | "stable" | "deteriorating"
    drivers:      tuple[str, ...]  # short tags (coup, civil_war, insurgency, etc.)
    notes:        str = ""


# ═══════════════════════════════════════════════════════════════════════
# Seed — arranged by ARIA heat-map region
# ═══════════════════════════════════════════════════════════════════════

_SEED: tuple[PoliticalRiskEntry, ...] = (
    # ── Lusophone ─────────────────────────────────────────────────────
    PoliticalRiskEntry("AO", "Angola",        "watch",    "60-80",   "stable",        ("one-party_state",), "Long-standing MPLA rule; stable but institutional fragility."),
    PoliticalRiskEntry("MZ", "Mozambique",    "elevated", "80-95",   "deteriorating", ("cabo_delgado_insurgency", "post-election_unrest"), "2024 post-election unrest + Cabo Delgado ongoing."),
    PoliticalRiskEntry("GW", "Guinea-Bissau", "elevated", "80-95",   "deteriorating", ("coup_history", "political_instability")),
    PoliticalRiskEntry("CV", "Cape Verde",    "stable",   "<60",     "stable",        ()),
    PoliticalRiskEntry("ST", "São Tomé & Príncipe", "stable", "<60", "stable",        ()),

    # ── West Africa ──────────────────────────────────────────────────
    PoliticalRiskEntry("NG", "Nigeria",       "elevated", "80-95",   "stable",        ("boko_haram", "ipob", "banditry_bandits", "delta_militancy"), "Northeast + NW insurgency ongoing; oil Delta militancy."),
    PoliticalRiskEntry("GH", "Ghana",         "watch",    "60-80",   "stable",        ("economic_pressure",), "Post-IMF programme. Generally stable."),
    PoliticalRiskEntry("SN", "Senegal",       "watch",    "60-80",   "stable",        ("casamance_separatism",)),
    PoliticalRiskEntry("CI", "Côte d'Ivoire", "watch",    "60-80",   "stable",        ()),
    PoliticalRiskEntry("CM", "Cameroon",      "elevated", "80-95",   "deteriorating", ("anglophone_crisis", "far_north_boko_haram")),
    # AES Alliance — all critical
    PoliticalRiskEntry("NE", "Niger",         "critical", ">95",     "deteriorating", ("2023_coup", "aes_alliance", "insurgency"), "Bazoum ouster July 2023. Wagner / Africa Corps footprint."),
    PoliticalRiskEntry("ML", "Mali",          "critical", ">95",     "deteriorating", ("coup_2020_2021", "aes_alliance", "insurgency", "wagner")),
    PoliticalRiskEntry("BF", "Burkina Faso",  "critical", ">95",     "deteriorating", ("coup_2022", "aes_alliance", "jnim_iss_ramp")),
    PoliticalRiskEntry("LR", "Liberia",       "watch",    "60-80",   "improving",     ()),
    PoliticalRiskEntry("SL", "Sierra Leone",  "watch",    "60-80",   "stable",        ()),

    # ── East / Central Africa ────────────────────────────────────────
    PoliticalRiskEntry("KE", "Kenya",         "watch",    "60-80",   "stable",        ("al_shabaab_border", "economic_unrest"), "Gen-Z protests 2024."),
    PoliticalRiskEntry("RW", "Rwanda",        "watch",    "60-80",   "stable",        ("m23_alleged_support",)),
    PoliticalRiskEntry("UG", "Uganda",        "watch",    "60-80",   "stable",        ("lra_historical", "drc_operations")),
    PoliticalRiskEntry("TZ", "Tanzania",      "watch",    "60-80",   "stable",        ()),
    PoliticalRiskEntry("BI", "Burundi",       "elevated", "80-95",   "stable",        ()),
    PoliticalRiskEntry("CD", "DRC",           "critical", ">95",     "deteriorating", ("m23", "armed_groups", "monusco_drawdown", "minerals_conflict"), "Eastern DRC / North+South Kivu active conflict."),
    PoliticalRiskEntry("ET", "Ethiopia",      "elevated", "80-95",   "deteriorating", ("post_tigray", "oromia_insurgency", "amhara_militia")),
    PoliticalRiskEntry("SO", "Somalia",       "critical", ">95",     "stable",        ("al_shabaab", "fragile_state")),
    PoliticalRiskEntry("SS", "South Sudan",   "critical", ">95",     "stable",        ("civil_war_legacy", "oil_conflict")),
    PoliticalRiskEntry("SD", "Sudan",         "critical", ">95",     "deteriorating", ("civil_war_2023", "saf_rsf", "humanitarian_catastrophe"), "Active civil war."),

    # ── North Africa ─────────────────────────────────────────────────
    PoliticalRiskEntry("MA", "Morocco",       "stable",   "<60",     "stable",        ("western_sahara_status",)),
    PoliticalRiskEntry("EG", "Egypt",         "watch",    "60-80",   "stable",        ("economic_pressure", "sinai_insurgency")),
    PoliticalRiskEntry("DZ", "Algeria",       "watch",    "60-80",   "stable",        ("hirak_legacy", "russia_ties"), "Non-aligned; Russia's primary African client."),
    PoliticalRiskEntry("TN", "Tunisia",       "watch",    "60-80",   "deteriorating", ("democratic_backsliding",)),
    PoliticalRiskEntry("LY", "Libya",         "critical", ">95",     "stable",        ("dual_government", "un_arms_embargo"), "UN Resolution 1970 embargo active."),

    # ── Southern Africa ──────────────────────────────────────────────
    PoliticalRiskEntry("ZA", "South Africa",  "watch",    "60-80",   "stable",        ("kzn_riots_legacy", "load_shedding")),
    PoliticalRiskEntry("BW", "Botswana",      "stable",   "<60",     "stable",        ()),
    PoliticalRiskEntry("NA", "Namibia",       "stable",   "<60",     "stable",        ()),
    PoliticalRiskEntry("ZW", "Zimbabwe",      "elevated", "80-95",   "stable",        ("economic_collapse_legacy",)),
    PoliticalRiskEntry("ZM", "Zambia",        "watch",    "60-80",   "improving",     ()),

    # ── Gulf / MENA ──────────────────────────────────────────────────
    PoliticalRiskEntry("AE", "UAE",           "stable",   "<60",     "stable",        ("free_zone_proliferation_risk",)),
    PoliticalRiskEntry("SA", "Saudi Arabia",  "stable",   "<60",     "stable",        ("yemen_proximity",)),
    PoliticalRiskEntry("QA", "Qatar",         "stable",   "<60",     "stable",        ()),
    PoliticalRiskEntry("KW", "Kuwait",        "stable",   "<60",     "stable",        ()),
    PoliticalRiskEntry("BH", "Bahrain",       "watch",    "60-80",   "stable",        ("sectarian_tension",)),
    PoliticalRiskEntry("OM", "Oman",          "stable",   "<60",     "stable",        ()),
    PoliticalRiskEntry("YE", "Yemen",         "critical", ">95",     "stable",        ("houthi", "un_embargo", "humanitarian_catastrophe")),
    PoliticalRiskEntry("IQ", "Iraq",          "elevated", "80-95",   "stable",        ("militia_proxy", "is_remnant")),
    PoliticalRiskEntry("IR", "Iran",          "elevated", "80-95",   "stable",        ("sanctions", "regional_proxy")),
    PoliticalRiskEntry("JO", "Jordan",        "watch",    "60-80",   "stable",        ("syria_border", "refugee_pressure")),
    PoliticalRiskEntry("LB", "Lebanon",       "critical", ">95",     "deteriorating", ("hezbollah_idf_conflict", "economic_collapse")),
    PoliticalRiskEntry("SY", "Syria",         "critical", ">95",     "stable",        ("civil_war_legacy", "sanctions")),
    PoliticalRiskEntry("IL", "Israel",        "elevated", "80-95",   "deteriorating", ("gaza_conflict", "multi_front_tensions")),
    PoliticalRiskEntry("PS", "Palestine",     "critical", ">95",     "deteriorating", ("gaza_destruction", "west_bank_tensions")),

    # ── Turkey ───────────────────────────────────────────────────────
    PoliticalRiskEntry("TR", "Türkiye",       "watch",    "60-80",   "stable",        ("pkk_kurdish_issue", "currency_pressure")),

    # ── South / Southeast Asia ───────────────────────────────────────
    PoliticalRiskEntry("IN", "India",         "watch",    "60-80",   "stable",        ()),
    PoliticalRiskEntry("PK", "Pakistan",      "elevated", "80-95",   "deteriorating", ("tlp_ttp", "economic_pressure", "civil_military_tension")),
    PoliticalRiskEntry("BD", "Bangladesh",    "watch",    "60-80",   "deteriorating", ("2024_political_transition",)),
    PoliticalRiskEntry("ID", "Indonesia",     "stable",   "<60",     "stable",        ()),
    PoliticalRiskEntry("PH", "Philippines",   "watch",    "60-80",   "stable",        ("south_china_sea", "npa_insurgency")),
    PoliticalRiskEntry("VN", "Vietnam",       "stable",   "<60",     "stable",        ()),
    PoliticalRiskEntry("MY", "Malaysia",      "stable",   "<60",     "stable",        ()),
    PoliticalRiskEntry("TH", "Thailand",      "watch",    "60-80",   "stable",        ("south_insurgency_low_level",)),
    PoliticalRiskEntry("SG", "Singapore",     "stable",   "<60",     "stable",        ()),
    PoliticalRiskEntry("MM", "Myanmar",       "critical", ">95",     "deteriorating", ("civil_war", "tatmadaw_junta", "sanctions")),

    # ── LatAm non-Lusophone ──────────────────────────────────────────
    PoliticalRiskEntry("PE", "Peru",          "watch",    "60-80",   "stable",        ("political_instability_legacy",)),
    PoliticalRiskEntry("CO", "Colombia",      "elevated", "80-95",   "stable",        ("eln_dissident_farc", "narco_economics")),
    PoliticalRiskEntry("CL", "Chile",         "watch",    "60-80",   "stable",        ("constitutional_reform_cycle",)),
    PoliticalRiskEntry("EC", "Ecuador",       "elevated", "80-95",   "deteriorating", ("narco_gang_violence_2024",)),
    PoliticalRiskEntry("MX", "Mexico",        "elevated", "80-95",   "stable",        ("cartel_violence",)),
    PoliticalRiskEntry("AR", "Argentina",     "watch",    "60-80",   "stable",        ("economic_pressure",)),
    PoliticalRiskEntry("VE", "Venezuela",     "critical", ">95",     "stable",        ("sanctions", "regime_politics")),

    # ── LatAm Lusophone ──────────────────────────────────────────────
    PoliticalRiskEntry("BR", "Brazil",        "watch",    "60-80",   "stable",        ("political_polarisation",)),

    # ── NATO / Europe / Balkans ──────────────────────────────────────
    PoliticalRiskEntry("UA", "Ukraine",       "critical", ">95",     "stable",        ("russian_invasion_active",)),
    PoliticalRiskEntry("RS", "Serbia",        "watch",    "60-80",   "stable",        ("kosovo_friction", "cfsp_gap")),
    PoliticalRiskEntry("BA", "Bosnia",        "watch",    "60-80",   "stable",        ("dayton_fragility", "rs_entity_brinkmanship")),
    PoliticalRiskEntry("XK", "Kosovo",        "watch",    "60-80",   "stable",        ("serbia_tensions",)),
    PoliticalRiskEntry("MK", "North Macedonia","stable",  "<60",     "stable",        ()),
    PoliticalRiskEntry("AL", "Albania",       "stable",   "<60",     "stable",        ()),
    PoliticalRiskEntry("ME", "Montenegro",    "stable",   "<60",     "stable",        ()),
)


# Flatten by ISO2 for O(1) lookup
_BY_ISO2: dict[str, PoliticalRiskEntry] = {e.iso2: e for e in _SEED}


# ═══════════════════════════════════════════════════════════════════════
# Public API
# ═══════════════════════════════════════════════════════════════════════

def risk_for_country(iso2: str) -> dict[str, Any] | None:
    iso2 = (iso2 or "").upper().strip()
    e = _BY_ISO2.get(iso2)
    if not e:
        return None
    return _to_dict(e)


def countries_by_tier(tier: str) -> list[dict[str, Any]]:
    tier = (tier or "").lower().strip()
    return [_to_dict(e) for e in _SEED if e.tier == tier]


def get_risk_context(query: str) -> str:
    """Return a compact risk block when a known country is in the query."""
    if not query or not query.strip():
        return ""
    q = query.lower()
    # Name-based keyword fan — for every entry check whether its country
    # name or ISO2 is in the query. Short ISO2 is risky so word-boundary.
    import re
    hits: list[PoliticalRiskEntry] = []
    for e in _SEED:
        # Country name (longer than 4 chars) — substring match is safe
        if len(e.country) >= 5 and e.country.lower() in q:
            hits.append(e)
            continue
        # ISO2 — word-boundary match, upper-case in original query
        if re.search(rf"\b{e.iso2}\b", query):
            hits.append(e)
    if not hits:
        return ""
    # De-dup preserving order
    seen = set()
    unique = []
    for h in hits:
        if h.iso2 in seen:
            continue
        seen.add(h.iso2)
        unique.append(h)
    lines = ["[POLITICAL RISK INDEX]"]
    for h in unique[:4]:
        drivers = ", ".join(h.drivers[:3]) if h.drivers else "—"
        lines.append(
            f"• {h.country} ({h.iso2}): tier={h.tier.upper()}, FSI {h.fsi_band}, "
            f"trajectory={h.trajectory}. Drivers: {drivers}."
        )
    # R-F996 — wire to brain
    from .engine_wiring import wire_success, wire_failure
    wire_success(
        module="political_risk_index",
        summary="Get Risk Context",
        source_id="political_risk_index:R-F996",
    )

    return "\n".join(lines)


def summary() -> dict[str, Any]:
    by_tier: dict[str, int] = {}
    by_trajectory: dict[str, int] = {}
    for e in _SEED:
        by_tier[e.tier] = by_tier.get(e.tier, 0) + 1
        by_trajectory[e.trajectory] = by_trajectory.get(e.trajectory, 0) + 1
    return {
        "countries_covered": len(_SEED),
        "by_tier": by_tier,
        "by_trajectory": by_trajectory,
    }


def _to_dict(e: PoliticalRiskEntry) -> dict[str, Any]:
    return {
        "iso2": e.iso2,
        "country": e.country,
        "tier": e.tier,
        "fsi_band": e.fsi_band,
        "trajectory": e.trajectory,
        "drivers": list(e.drivers),
        "notes": e.notes,
    }

# R-F2119 §21a — wire failure handler for political_risk_index
try:
    wire_failure(module="political_risk_index", detail="module shutdown",
                gap_type="engine_failure", source="political_risk_index:shutdown")
except Exception:
    pass
