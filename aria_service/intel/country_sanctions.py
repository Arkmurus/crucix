"""
Country-level sanctions regime lookup — grounded in legal instruments.

This module answers "is [country] under [EU/US/UN] sanctions?" — a REGIME
question, not an entity-list question. The authoritative sources are legal
instruments (UNSCR numbers, EU regulations, Executive Orders), not entity
screening lists (OFAC SDN, EU Consolidated).

Architecture
════════════
  1. REGIME ANSWER — curated table of country sanctions regimes, each entry
     citing the specific legal instrument (UNSCR, EU Reg, EO). This is the
     PRIMARY source for regime questions because no free live API provides
     program-level regime data with instrument citations.
  2. CLASSIFICATION — each regime is classified as:
       comprehensive  (full trade embargo, asset freeze)
       targeted       (specific individuals/entities/sectors)
       arms_embargo   (weapons/defence only, may have exceptions)
  3. ENTITY HANDOFF — for named counterparties, delegates to
     sanctions.screen_with_aliases() for live entity-list screening.

Every entry carries a last_reviewed date and the rendered answer includes
a caveat: "Sanctions regimes change — verify against the live lists before
acting on this information."

R-F1414 — country sanctions live screen (T0-2).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal

logger = logging.getLogger("aria.intel.country_sanctions")

RegimeType = Literal["comprehensive", "targeted", "arms_embargo"]
RegimeSource = Literal["un", "eu", "uk", "us"]


@dataclass(frozen=True)
class SanctionsRegime:
    """One country sanctions regime from one source.

    Attributes:
        country:      Country name (canonical).
        source:       Sanctions authority (un, eu, uk, us).
        regime_type:  comprehensive | targeted | arms_embargo.
        in_force:     Whether the regime is currently in force.
        instruments:  Legal instrument citations (UNSCR, EU Reg, EO, etc.).
        exceptions:   Known exceptions or nuances (e.g. government exemption).
        last_reviewed: ISO-8601 date of last review.
        detail:       Human-readable summary of the regime.
    """
    country: str
    source: RegimeSource
    regime_type: RegimeType
    in_force: bool
    instruments: tuple[str, ...]
    exceptions: str = ""
    last_reviewed: str = ""
    detail: str = ""


# ── Curated regime table ────────────────────────────────────────────────────
# Each entry is grounded in the specific legal instrument. Last reviewed dates
# are set at creation; update when regimes change.
# Sources: UN Security Council Resolutions, EU Council Decisions/Regulations,
# UK Sanctions and Anti-Money Laundering Act (SAMLA) statutory instruments,
# US Executive Orders / OFAC program designations.

_COUNTRY_REGIMES: list[SanctionsRegime] = [
    # ── Iraq ──────────────────────────────────────────────────────────────
    SanctionsRegime(
        country="Iraq",
        source="un",
        regime_type="arms_embargo",
        in_force=True,
        instruments=("UNSCR 1546 (2004)", "UNSCR 1518 (2003)", "UNSCR 1483 (2003)"),
        exceptions=(
            "Government-of-Iraq exception: arms and related materiel "
            "supplied TO the Government of Iraq are permitted subject to "
            "licensing (UNSCR 1546 para 10). The embargo targets "
            "non-state actors and terrorist groups, not the Iraqi state."
        ),
        last_reviewed="2026-06-07",
        detail=(
            "UN arms embargo on non-state actors in Iraq. The Government "
            "of Iraq is exempt — defence exports to the Iraqi government "
            "are permitted with appropriate licensing."
        ),
    ),
    SanctionsRegime(
        country="Iraq",
        source="eu",
        regime_type="targeted",
        in_force=True,
        instruments=("EU Regulation 1210/2003", "EU Common Position 2003/495/CFSP"),
        exceptions=(
            "EU measures are targeted (asset freezes on specific entities "
            "and individuals), not a comprehensive embargo on Iraq."
        ),
        last_reviewed="2026-06-07",
        detail=(
            "EU restrictive measures against Iraq are targeted — asset "
            "freezes on designated persons and entities linked to the "
            "former regime. No comprehensive trade embargo."
        ),
    ),
    SanctionsRegime(
        country="Iraq",
        source="us",
        regime_type="targeted",
        in_force=True,
        instruments=(
            "Executive Order 13303 (Development Fund for Iraq)",
            "Executive Order 13315 (former regime)",
            "OFAC Iraq Sanctions program",
        ),
        exceptions=(
            "US sanctions on Iraq are primarily targeted (former regime "
            "officials, terrorism-related designations). The Iraq "
            "Sanctions program is NOT a comprehensive embargo — trade "
            "and investment are generally permitted subject to "
            "OFAC compliance."
        ),
        last_reviewed="2026-06-07",
        detail=(
            "US sanctions related to Iraq are targeted — focused on "
            "former regime officials, terrorism financing, and "
            "designated entities. No comprehensive trade embargo. "
            "The Development Fund for Iraq (EO 13303) protections "
            "have been largely wound down."
        ),
    ),
    SanctionsRegime(
        country="Iraq",
        source="uk",
        regime_type="targeted",
        in_force=True,
        instruments=(
            "Iraq (Sanctions) Regulations 2023 (SI 2023/xxx)",
            "UK SAMLA 2018",
        ),
        exceptions=(
            "UK sanctions on Iraq are targeted (asset freezes on "
            "designated persons). No comprehensive embargo."
        ),
        last_reviewed="2026-06-07",
        detail=(
            "UK sanctions on Iraq are targeted — asset freezes on "
            "designated persons linked to the former regime or "
            "terrorism. Defence exports require a UK SIEL licence "
            "but are not prohibited outright."
        ),
    ),

    # ── Iran ──────────────────────────────────────────────────────────────
    SanctionsRegime(
        country="Iran",
        source="un",
        regime_type="targeted",
        in_force=True,
        instruments=("UNSCR 2231 (2015)", "UNSCR 1737 (2006)"),
        exceptions=(
            "Conventional arms embargo LIFTED October 2020 per JCPOA "
            "schedule. Ballistic missile restrictions remain. "
            "Nuclear-related procurement requires UN approval. "
            "Note: US asserts snapback of UN sanctions (disputed)."
        ),
        last_reviewed="2026-06-07",
        detail=(
            "UN sanctions on Iran were significantly rolled back under "
            "the JCPOA (UNSCR 2231). The conventional arms embargo "
            "expired October 2020. Ballistic missile-related "
            "restrictions remain in place. US asserts snapback "
            "of UN sanctions (internationally disputed)."
        ),
    ),
    SanctionsRegime(
        country="Iran",
        source="eu",
        regime_type="targeted",
        in_force=True,
        instruments=(
            "EU Regulation 2018/1100",
            "EU Council Decision 2010/413/CFSP",
        ),
        exceptions=(
            "EU arms embargo on Iran (military goods + dual-use). "
            "Nuclear-related procurement restrictions. "
            "Asset freezes on designated entities."
        ),
        last_reviewed="2026-06-07",
        detail=(
            "EU maintains targeted sanctions on Iran — arms embargo "
            "(military and dual-use goods), asset freezes on "
            "designated persons/entities, and nuclear-related "
            "procurement restrictions."
        ),
    ),
    SanctionsRegime(
        country="Iran",
        source="us",
        regime_type="comprehensive",
        in_force=True,
        instruments=(
            "Executive Order 13902 (2020)",
            "Executive Order 13876 (2019)",
            "OFAC Iran Sanctions program",
            "CAATSA Title II",
        ),
        exceptions=(
            "Humanitarian exemptions (food, medicine, medical devices) "
            "are permitted under OFAC general licences. "
            "Secondary sanctions apply to non-US persons."
        ),
        last_reviewed="2026-06-07",
        detail=(
            "US maintains comprehensive sanctions on Iran — broad "
            "trade embargo, asset freezes, secondary sanctions. "
            "Humanitarian goods exempted. CAATSA Title II "
            "sanctions on Iran's ballistic missile program."
        ),
    ),

    # ── Syria ─────────────────────────────────────────────────────────────
    SanctionsRegime(
        country="Syria",
        source="un",
        regime_type="targeted",
        in_force=True,
        instruments=("UNSCR 2118 (2013)", "UNSCR 2254 (2015)"),
        exceptions=(
            "No comprehensive UN arms embargo on Syria. UN measures "
            "are limited to chemical weapons-related restrictions "
            "and targeted designations."
        ),
        last_reviewed="2026-06-07",
        detail=(
            "UN sanctions on Syria are limited — chemical weapons "
            "restrictions (UNSCR 2118) and targeted designations. "
            "No comprehensive UN arms embargo."
        ),
    ),
    SanctionsRegime(
        country="Syria",
        source="eu",
        regime_type="targeted",
        in_force=True,
        instruments=(
            "EU Regulation 36/2012",
            "EU Council Decision 2011/782/CFSP",
        ),
        exceptions=(
            "EU arms embargo on Syria (all military goods). "
            "Oil embargo, investment restrictions, asset freezes. "
            "Humanitarian exemptions apply."
        ),
        last_reviewed="2026-06-07",
        detail=(
            "EU maintains broad targeted sanctions on Syria — arms "
            "embargo, oil embargo, investment restrictions, "
            "asset freezes on designated entities."
        ),
    ),
    SanctionsRegime(
        country="Syria",
        source="us",
        regime_type="comprehensive",
        in_force=True,
        instruments=(
            "Executive Order 13572 (2011)",
            "Executive Order 13573 (2011)",
            "Executive Order 13582 (2011)",
            "Caesar Syria Civilian Protection Act 2019",
            "OFAC Syria Sanctions program",
        ),
        exceptions=(
            "Humanitarian exemptions apply. Caesar Act imposes "
            "secondary sanctions on foreign persons."
        ),
        last_reviewed="2026-06-07",
        detail=(
            "US maintains comprehensive sanctions on Syria — broad "
            "trade embargo, asset freezes, secondary sanctions "
            "under the Caesar Act."
        ),
    ),

    # ── Russia ────────────────────────────────────────────────────────────
    SanctionsRegime(
        country="Russia",
        source="eu",
        regime_type="targeted",
        in_force=True,
        instruments=(
            "EU Regulation 833/2014 (sectoral)",
            "EU Regulation 269/2014 (asset freezes)",
            "EU Council Decision 2014/512/CFSP",
        ),
        exceptions=(
            "Sectoral sanctions (finance, energy, defence, technology). "
            "Asset freezes on designated persons/entities. "
            "Oil price cap and diamond ban in effect. "
            "Humanitarian and food exemptions apply."
        ),
        last_reviewed="2026-06-07",
        detail=(
            "EU maintains broad sectoral sanctions on Russia — "
            "finance, energy, defence, and technology restrictions. "
            "Oil price cap and diamond ban in effect. "
            "Asset freezes on designated persons. No comprehensive "
            "trade embargo."
        ),
    ),
    SanctionsRegime(
        country="Russia",
        source="us",
        regime_type="targeted",
        in_force=True,
        instruments=(
            "Executive Order 14024 (2021)",
            "Executive Order 13662 (2014)",
            "CAATSA Title I (2017)",
            "OFAC Russia/Ukraine programs",
        ),
        exceptions=(
            "Sectoral sanctions (SSI list). Asset freezes on "
            "designated persons/entities. Secondary sanctions "
            "under CAATSA. Humanitarian exemptions apply."
        ),
        last_reviewed="2026-06-07",
        detail=(
            "US maintains broad sectoral sanctions on Russia — "
            "finance, energy, defence, and technology. Asset "
            "freezes on designated persons. Secondary sanctions "
            "under CAATSA."
        ),
    ),
    SanctionsRegime(
        country="Russia",
        source="uk",
        regime_type="targeted",
        in_force=True,
        instruments=(
            "Russia (Sanctions) Regulations 2019 (SI 2019/855)",
            "UK SAMLA 2018",
        ),
        exceptions=(
            "Sectoral sanctions mirroring EU. Asset freezes on "
            "designated persons. Humanitarian exemptions."
        ),
        last_reviewed="2026-06-07",
        detail=(
            "UK maintains sectoral sanctions on Russia — finance, "
            "energy, defence, and technology restrictions. "
            "Asset freezes on designated persons."
        ),
    ),

    # ── North Korea (DPRK) ────────────────────────────────────────────────
    SanctionsRegime(
        country="North Korea",
        source="un",
        regime_type="comprehensive",
        in_force=True,
        instruments=(
            "UNSCR 1718 (2006)",
            "UNSCR 2270 (2016)",
            "UNSCR 2371 (2017)",
            "UNSCR 2397 (2017)",
        ),
        exceptions=(
            "Comprehensive arms embargo — all weapons, all parties. "
            "No exceptions for government transactions. "
            "Humanitarian exemptions for food and medicine."
        ),
        last_reviewed="2026-06-07",
        detail=(
            "UN maintains comprehensive sanctions on North Korea — "
            "full arms embargo, trade restrictions, asset freezes. "
            "No government exception."
        ),
    ),
    SanctionsRegime(
        country="North Korea",
        source="eu",
        regime_type="comprehensive",
        in_force=True,
        instruments=(
            "EU Regulation 2017/1509",
            "EU Council Decision 2016/849/CFSP",
        ),
        exceptions=(
            "Comprehensive arms embargo. Trade restrictions. "
            "Humanitarian exemptions."
        ),
        last_reviewed="2026-06-07",
        detail=(
            "EU maintains comprehensive sanctions on North Korea — "
            "full arms embargo, trade restrictions, asset freezes."
        ),
    ),
    SanctionsRegime(
        country="North Korea",
        source="us",
        regime_type="comprehensive",
        in_force=True,
        instruments=(
            "Executive Order 13466 (2008)",
            "Executive Order 13551 (2010)",
            "Executive Order 13687 (2015)",
            "OFAC North Korea Sanctions program",
        ),
        exceptions=(
            "Comprehensive trade embargo. Humanitarian exemptions "
            "for food and medicine."
        ),
        last_reviewed="2026-06-07",
        detail=(
            "US maintains comprehensive sanctions on North Korea — "
            "broad trade embargo, asset freezes, secondary sanctions."
        ),
    ),

    # ── Yemen ─────────────────────────────────────────────────────────────
    SanctionsRegime(
        country="Yemen",
        source="un",
        regime_type="targeted",
        in_force=True,
        instruments=("UNSCR 2216 (2015)", "UNSCR 2140 (2014)"),
        exceptions=(
            "Arms embargo applies to Houthi forces and forces loyal "
            "to former President Saleh. NOT a general Yemen embargo. "
            "Saudi-led coalition is NOT subject to the embargo. "
            "Government of Yemen forces are exempt."
        ),
        last_reviewed="2026-06-07",
        detail=(
            "UN arms embargo on Yemen is targeted — applies to "
            "Houthi forces and Saleh-loyal forces only. The "
            "internationally recognised Government of Yemen is "
            "exempt. Saudi-led coalition operations are not "
            "subject to the embargo."
        ),
    ),

    # ── Libya ─────────────────────────────────────────────────────────────
    SanctionsRegime(
        country="Libya",
        source="un",
        regime_type="arms_embargo",
        in_force=True,
        instruments=("UNSCR 1970 (2011)", "UNSCR 2009 (2011)", "UNSCR 2292 (2016)"),
        exceptions=(
            "General arms embargo with exceptions for non-lethal "
            "equipment to the Government of Libya. Multiple "
            "documented violations by UAE, Turkey, Russia."
        ),
        last_reviewed="2026-06-07",
        detail=(
            "UN arms embargo on Libya — general embargo with "
            "exceptions for non-lethal equipment to government "
            "forces. Enforcement is weak with documented violations."
        ),
    ),

    # ── Sudan ─────────────────────────────────────────────────────────────
    SanctionsRegime(
        country="Sudan",
        source="un",
        regime_type="targeted",
        in_force=True,
        instruments=("UNSCR 1591 (2005)", "UNSCR 1556 (2004)"),
        exceptions=(
            "Arms embargo applies to Darfur specifically. NOT a "
            "general Sudan embargo. Post-April 2023 conflict "
            "has led to additional restrictive measures."
        ),
        last_reviewed="2026-06-07",
        detail=(
            "UN arms embargo on Sudan is limited to Darfur "
            "(UNSCR 1591). Not a general Sudan embargo. "
            "Post-2023 conflict situation may involve additional "
            "restrictions."
        ),
    ),

    # ── Venezuela ─────────────────────────────────────────────────────────
    SanctionsRegime(
        country="Venezuela",
        source="eu",
        regime_type="targeted",
        in_force=True,
        instruments=(
            "EU Regulation 2017/2063",
            "EU Council Decision 2017/2074/CFSP",
        ),
        exceptions=(
            "Arms embargo + surveillance equipment controls. "
            "Asset freezes on designated officials. "
            "No comprehensive trade embargo."
        ),
        last_reviewed="2026-06-07",
        detail=(
            "EU sanctions on Venezuela are targeted — arms embargo, "
            "surveillance equipment controls, asset freezes on "
            "designated officials. No comprehensive embargo."
        ),
    ),
    SanctionsRegime(
        country="Venezuela",
        source="us",
        regime_type="targeted",
        in_force=True,
        instruments=(
            "Executive Order 13692 (2015)",
            "Executive Order 13850 (2018)",
            "OFAC Venezuela Sanctions program",
        ),
        exceptions=(
            "Sectoral sanctions on oil (PDVSA). Asset freezes on "
            "designated officials. No comprehensive trade embargo. "
            "Humanitarian exemptions apply."
        ),
        last_reviewed="2026-06-07",
        detail=(
            "US sanctions on Venezuela are targeted — oil sector "
            "sanctions (PDVSA), asset freezes on designated "
            "officials. No comprehensive trade embargo."
        ),
    ),

    # ── Belarus ───────────────────────────────────────────────────────────
    SanctionsRegime(
        country="Belarus",
        source="eu",
        regime_type="targeted",
        in_force=True,
        instruments=(
            "EU Regulation 765/2006",
            "EU Council Decision 2012/642/CFSP",
        ),
        exceptions=(
            "Arms embargo + asset freezes on designated persons. "
            "Sectoral restrictions on finance, energy, and "
            "technology. No comprehensive trade embargo."
        ),
        last_reviewed="2026-06-07",
        detail=(
            "EU sanctions on Belarus are targeted — arms embargo, "
            "asset freezes, sectoral restrictions. No "
            "comprehensive trade embargo."
        ),
    ),
    SanctionsRegime(
        country="Belarus",
        source="us",
        regime_type="targeted",
        in_force=True,
        instruments=(
            "Executive Order 13405 (2006)",
            "OFAC Belarus Sanctions program",
        ),
        exceptions=(
            "Asset freezes on designated persons. Sectoral "
            "restrictions. No comprehensive trade embargo."
        ),
        last_reviewed="2026-06-07",
        detail=(
            "US sanctions on Belarus are targeted — asset freezes "
            "on designated persons. No comprehensive embargo."
        ),
    ),

    # ── Myanmar (Burma) ───────────────────────────────────────────────────
    SanctionsRegime(
        country="Myanmar",
        source="eu",
        regime_type="targeted",
        in_force=True,
        instruments=(
            "EU Regulation 2019/795",
            "EU Council Decision 2013/184/CFSP",
        ),
        exceptions=(
            "Arms embargo + asset freezes on designated persons. "
            "No comprehensive trade embargo."
        ),
        last_reviewed="2026-06-07",
        detail=(
            "EU sanctions on Myanmar are targeted — arms embargo, "
            "asset freezes on designated military officials. "
            "No comprehensive trade embargo."
        ),
    ),
    SanctionsRegime(
        country="Myanmar",
        source="us",
        regime_type="targeted",
        in_force=True,
        instruments=(
            "Executive Order 14014 (2021)",
            "OFAC Burma Sanctions program",
        ),
        exceptions=(
            "Asset freezes on designated persons. Restrictions "
            "on defence trade. No comprehensive trade embargo."
        ),
        last_reviewed="2026-06-07",
        detail=(
            "US sanctions on Myanmar are targeted — asset freezes "
            "on designated military officials, defence trade "
            "restrictions. No comprehensive embargo."
        ),
    ),
]

# Build lookup index
_COUNTRY_INDEX: dict[str, list[SanctionsRegime]] = {}
for _regime in _COUNTRY_REGIMES:
    _key = _regime.country.lower()
    if _key not in _COUNTRY_INDEX:
        _COUNTRY_INDEX[_key] = []
    _COUNTRY_INDEX[_key].append(_regime)

# Country name aliases for matching
_COUNTRY_ALIASES: dict[str, str] = {
    "dprk": "north korea",
    "north korea": "north korea",
    "burma": "myanmar",
    "venezuela": "venezuela",
    "iran": "iran",
    "iraq": "iraq",
    "syria": "syria",
    "russia": "russia",
    "russian federation": "russia",
    "libya": "libya",
    "yemen": "yemen",
    "sudan": "sudan",
    "belarus": "belarus",
    "myanmar": "myanmar",
}


def _normalise_country(name: str) -> str | None:
    """Normalise a country name to the canonical key used in the regime table."""
    key = name.strip().lower()
    # Direct match
    if key in _COUNTRY_INDEX:
        return key
    # Alias match
    if key in _COUNTRY_ALIASES:
        return _COUNTRY_ALIASES[key]
    return None


def lookup_country(country: str, source: str | None = None) -> list[SanctionsRegime]:
    """Look up sanctions regimes for a country.

    Args:
        country: Country name (e.g. "Iraq", "Iran", "Russia").
        source:  Optional filter — "un", "eu", "uk", "us". If None, returns all.

    Returns:
        List of SanctionsRegime entries for the country, filtered by source if given.
    """
    key = _normalise_country(country)
    if not key:
        return []

    regimes = _COUNTRY_INDEX.get(key, [])
    if source:
        source_lower = source.lower().strip()
        return [r for r in regimes if r.source == source_lower]
    return regimes


def format_regime_answer(country: str, source: str | None = None) -> dict:
    """Format a human-readable sanctions regime answer for a country.

    This is the PRIMARY answer for "is [country] under sanctions?" questions.
    Returns a structured dict with regime details, classifications, and citations.

    Args:
        country: Country name.
        source:  Optional source filter.

    Returns:
        dict with keys: country, found (bool), regimes (list), summary (str),
                        worst_regime_type (str), has_comprehensive (bool),
                        has_arms_embargo (bool), has_targeted (bool),
                        entity_screen_offered (bool), caveat (str).
    """
    regimes = lookup_country(country, source)

    if not regimes:
        return {
            "country": country,
            "found": False,
            "regimes": [],
            "summary": (
                f"No sanctions regime data available for {country}. "
                f"This does NOT mean {country} is free of sanctions — "
                f"regimes change frequently. Verify against the live "
                f"OFAC SDN, EU Consolidated, UK OFSI, and UN lists."
            ),
            "worst_regime_type": "unknown",
            "has_comprehensive": False,
            "has_arms_embargo": False,
            "has_targeted": False,
            "entity_screen_offered": True,
            "caveat": (
                "Sanctions regimes change frequently. Verify against "
                "the live OFAC SDN, EU Consolidated, UK OFSI, and UN "
                "lists before acting on this information."
            ),
        }

    # Classify
    has_comprehensive = any(r.regime_type == "comprehensive" for r in regimes)
    has_arms_embargo = any(r.regime_type == "arms_embargo" for r in regimes)
    has_targeted = any(r.regime_type == "targeted" for r in regimes)

    if has_comprehensive:
        worst_type = "comprehensive"
    elif has_arms_embargo:
        worst_type = "arms_embargo"
    else:
        worst_type = "targeted"

    # Build per-regime details
    regime_details = []
    for r in regimes:
        regime_details.append({
            "source": r.source,
            "regime_type": r.regime_type,
            "in_force": r.in_force,
            "instruments": list(r.instruments),
            "exceptions": r.exceptions,
            "detail": r.detail,
            "last_reviewed": r.last_reviewed,
        })

    # Build summary
    source_labels = {"un": "UN", "eu": "EU", "uk": "UK", "us": "US"}
    parts = []
    for r in regimes:
        label = source_labels.get(r.source, r.source.upper())
        type_label = r.regime_type.replace("_", " ").title()
        instr = "; ".join(r.instruments[:2])
        if len(r.instruments) > 2:
            instr += f" [+{len(r.instruments) - 2} more]"
        parts.append(f"{label}: {type_label} ({instr})")
        if r.exceptions:
            parts.append(f"  Exception: {r.exceptions[:200]}")

    summary = "\n".join(parts)

    return {
        "country": country,
        "found": True,
        "regimes": regime_details,
        "summary": summary,
        "worst_regime_type": worst_type,
        "has_comprehensive": has_comprehensive,
        "has_arms_embargo": has_arms_embargo,
        "has_targeted": has_targeted,
        "entity_screen_offered": True,
        "caveat": (
            "Sanctions regimes change frequently. Verify against "
            "the live OFAC SDN, EU Consolidated, UK OFSI, and UN "
            "lists before acting on this information."
        ),
    }
