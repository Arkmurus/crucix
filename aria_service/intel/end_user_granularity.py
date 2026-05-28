"""R-F641 — End-user granularity scorer.

Why this exists
───────────────
The 2026-05-17 ADSM case had a verbal "EUC = Saudi Gov" — generic
country name only, no named ministry / directorate / service.
For Clause 4.5(e) Arkmurus-reasonably-satisfied compliance, AND for
ECJU / DSCA EUC processing, generic country-level end-user is
INSUFFICIENT. A real sovereign procurement names a specific
authoritative entity (RSLF, SANG, RSADF, GAMI, MoD-specific
directorate).

This module:
  - Knows which named entities are acceptable end-users per country
  - Scores a stated end-user from INSUFFICIENT → COUNTRY_LEVEL →
    MINISTRY_LEVEL → SPECIFIC_DIRECTORATE
  - Returns a Finding-shaped dict for orchestrator wiring

Honesty discipline
──────────────────
Acceptable-entity lists are SNAPSHOTS. Saudi GAMI was created 2017.
SAMI 2017. SANG vs Saudi Land Forces have evolved. Each entry carries
as_of. Each entry cites the authoritative reference where possible.
"""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger("aria.end_user_granularity")


# ─────────────────────────────────────────────────────────────────────
# Enums
# ─────────────────────────────────────────────────────────────────────


class GranularityLevel:
    """Four discrete buckets in order of increasing specificity."""
    INSUFFICIENT = "insufficient"          # "the government", "Saudi MOD"
    COUNTRY_LEVEL = "country_level"         # "Saudi Government"
    MINISTRY_LEVEL = "ministry_level"       # "Ministry of Defence"
    SPECIFIC_DIRECTORATE = "specific_directorate"  # "Royal Saudi Land Forces"


# ─────────────────────────────────────────────────────────────────────
# Per-country acceptable-end-user directory
# ─────────────────────────────────────────────────────────────────────
#
# Per ISO2: list of (canonical_name, level, aliases, notes).
# `level` indicates the BEST granularity each named entity achieves.
# A SPECIFIC_DIRECTORATE entry satisfies ECJU EUC requirements; a
# MINISTRY_LEVEL entry is borderline; a COUNTRY_LEVEL entry is
# typically insufficient for SIEL applications.


_BY_COUNTRY: dict[str, list[dict]] = {

    "SA": [  # Saudi Arabia
        {"name": "Royal Saudi Land Forces", "level": "specific_directorate",
         "aliases": ["RSLF", "Saudi Army", "Saudi Royal Land Forces"],
         "notes": "Primary land-forces procurement end-user."},
        {"name": "Royal Saudi Air Force", "level": "specific_directorate",
         "aliases": ["RSAF", "Saudi Air Force"], "notes": ""},
        {"name": "Royal Saudi Air Defence Forces", "level": "specific_directorate",
         "aliases": ["RSADF", "Saudi Air Defence", "Saudi Air Defense Forces"],
         "notes": ""},
        {"name": "Royal Saudi Naval Forces", "level": "specific_directorate",
         "aliases": ["RSNF", "Saudi Navy"], "notes": ""},
        {"name": "Saudi Arabian National Guard", "level": "specific_directorate",
         "aliases": ["SANG", "National Guard", "Saudi National Guard"],
         "notes": "Separate command from RSLF; reports to King directly historically."},
        {"name": "Royal Guard of Saudi Arabia", "level": "specific_directorate",
         "aliases": ["Saudi Royal Guard"], "notes": ""},
        {"name": "General Authority for Military Industries",
         "level": "specific_directorate",
         "aliases": ["GAMI"],
         "notes": "Procurement regulator + licensor (R-F603 inventory)."},
        {"name": "Saudi Arabian Military Industries",
         "level": "specific_directorate",
         "aliases": ["SAMI"], "notes": "State-owned defence holding."},
        {"name": "Ministry of Defence (Saudi Arabia)", "level": "ministry_level",
         "aliases": ["Saudi MoD", "MoD Saudi", "Saudi Ministry of Defense"],
         "notes": "Acceptable for SIEL but specific service preferred."},
        {"name": "Saudi Government", "level": "country_level",
         "aliases": ["KSA Government", "Saudi Gov", "Kingdom of Saudi Arabia"],
         "notes": "INSUFFICIENT for ECJU EUC — demand specific service."},
    ],

    "AE": [  # UAE
        {"name": "UAE Armed Forces", "level": "specific_directorate",
         "aliases": ["UAE AF"], "notes": ""},
        {"name": "Tawazun Economic Council", "level": "specific_directorate",
         "aliases": ["Tawazun"], "notes": "UAE defence offset / procurement."},
        {"name": "EDGE Group", "level": "specific_directorate",
         "aliases": ["EDGE"], "notes": "UAE state defence holding."},
        {"name": "EDIC (Emirates Defense Industries Company)",
         "level": "specific_directorate",
         "aliases": ["EDIC"], "notes": "Now part of EDGE Group."},
        {"name": "Ministry of Defence (UAE)", "level": "ministry_level",
         "aliases": ["UAE MoD"], "notes": ""},
    ],

    "EG": [  # Egypt
        {"name": "National Service Projects Organization",
         "level": "specific_directorate",
         "aliases": ["NSPO"], "notes": "Military-owned holding."},
        {"name": "Arab Organization for Industrialization",
         "level": "specific_directorate",
         "aliases": ["AOI"], "notes": ""},
        {"name": "Ministry of Military Production (Egypt)",
         "level": "specific_directorate",
         "aliases": ["MoMP", "MIC"], "notes": "Egyptian state defence holding."},
        {"name": "Egyptian Armed Forces", "level": "specific_directorate",
         "aliases": ["EAF"], "notes": ""},
        {"name": "Ministry of Defence (Egypt)", "level": "ministry_level",
         "aliases": ["Egyptian MoD"], "notes": ""},
    ],

    "JO": [  # Jordan
        {"name": "KADDB (King Abdullah II Design and Development Bureau)",
         "level": "specific_directorate",
         "aliases": ["KADDB"], "notes": ""},
        {"name": "Jordanian Armed Forces", "level": "specific_directorate",
         "aliases": ["JAF"], "notes": ""},
    ],

    "GB": [  # United Kingdom
        {"name": "Defence Equipment and Support", "level": "specific_directorate",
         "aliases": ["DE&S", "DES"], "notes": "UK MoD procurement arm."},
        {"name": "Royal Air Force", "level": "specific_directorate",
         "aliases": ["RAF"], "notes": ""},
        {"name": "Royal Navy", "level": "specific_directorate",
         "aliases": ["RN"], "notes": ""},
        {"name": "British Army", "level": "specific_directorate",
         "aliases": ["UK Army"], "notes": ""},
        {"name": "UK Ministry of Defence", "level": "ministry_level",
         "aliases": ["UK MoD", "Ministry of Defence"], "notes": ""},
    ],

    "US": [  # United States
        {"name": "Defense Security Cooperation Agency",
         "level": "specific_directorate",
         "aliases": ["DSCA"], "notes": "US FMS gateway."},
        {"name": "US Army", "level": "specific_directorate",
         "aliases": ["U.S. Army"], "notes": ""},
        {"name": "US Navy", "level": "specific_directorate",
         "aliases": ["U.S. Navy"], "notes": ""},
        {"name": "US Marine Corps", "level": "specific_directorate",
         "aliases": ["USMC"], "notes": ""},
        {"name": "US Air Force", "level": "specific_directorate",
         "aliases": ["USAF"], "notes": ""},
        {"name": "Department of Defense", "level": "ministry_level",
         "aliases": ["DOD", "DoD", "US DoD"], "notes": ""},
    ],

    "FR": [  # France
        {"name": "DGA (Direction générale de l'armement)",
         "level": "specific_directorate",
         "aliases": ["DGA"], "notes": "French defence procurement agency."},
        {"name": "French Army (Armée de Terre)",
         "level": "specific_directorate",
         "aliases": ["Armée de Terre"], "notes": ""},
        {"name": "French Air and Space Force",
         "level": "specific_directorate",
         "aliases": ["Armée de l'Air"], "notes": ""},
        {"name": "French Navy", "level": "specific_directorate",
         "aliases": ["Marine nationale"], "notes": ""},
    ],

    "DE": [  # Germany
        {"name": "BAAINBw (Bundesamt für Ausrüstung)",
         "level": "specific_directorate",
         "aliases": ["BAAINBw"], "notes": "German federal procurement office."},
        {"name": "Bundeswehr", "level": "specific_directorate",
         "aliases": [], "notes": ""},
    ],
}


# ─────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class GranularityResult:
    level: str                    # GranularityLevel value
    matched_entity_name: Optional[str]  # canonical name if matched
    iso2: Optional[str]
    detail: str


def score(stated_end_user: str, country_iso2: Optional[str] = None) -> GranularityResult:
    """Score an end-user statement.

    Args:
        stated_end_user: free-text statement from the counterparty
            ("Saudi Government", "Royal Saudi Land Forces", "RSLF", etc.)
        country_iso2: optional context — restricts directorate lookup.

    Returns: GranularityResult.
    """
    if not stated_end_user or not isinstance(stated_end_user, str):
        return GranularityResult(
            level=GranularityLevel.INSUFFICIENT,
            matched_entity_name=None,
            iso2=country_iso2,
            detail="No end-user stated.",
        )
    text_lc = stated_end_user.lower().strip()
    if not text_lc:
        return GranularityResult(
            level=GranularityLevel.INSUFFICIENT,
            matched_entity_name=None,
            iso2=country_iso2,
            detail="Empty end-user.",
        )

    # Search the per-country directory. If country_iso2 given, scope
    # to that; else scan all.
    countries_to_search = (
        [country_iso2.upper()] if country_iso2
        else list(_BY_COUNTRY.keys())
    )
    best: tuple[int, Optional[dict], Optional[str]] = (0, None, None)
    rank = {
        GranularityLevel.SPECIFIC_DIRECTORATE: 3,
        GranularityLevel.MINISTRY_LEVEL: 2,
        GranularityLevel.COUNTRY_LEVEL: 1,
        GranularityLevel.INSUFFICIENT: 0,
    }
    for iso2 in countries_to_search:
        for entry in _BY_COUNTRY.get(iso2, []):
            candidates = [entry["name"]] + entry.get("aliases", [])
            for c in candidates:
                c_lc = c.lower()
                if len(c_lc) < 3:
                    continue  # avoid spurious 2-char matches
                # Word-boundary match in either direction
                if re.search(rf"\b{re.escape(c_lc)}\b", text_lc) or text_lc == c_lc:
                    level = entry["level"]
                    if rank[level] > best[0]:
                        best = (rank[level], entry, iso2)
    if best[1]:
        entry, iso2 = best[1], best[2]
        return GranularityResult(
            level=entry["level"],
            matched_entity_name=entry["name"],
            iso2=iso2,
            detail=(
                f"Matched named entity '{entry['name']}' "
                f"(level: {entry['level']}). {entry.get('notes', '')}"
            ),
        )
    # No match — treat as INSUFFICIENT unless the text contains a
    # generic country reference (which gives COUNTRY_LEVEL at best).
    if country_iso2 and country_iso2.lower() in text_lc:
        return GranularityResult(
            level=GranularityLevel.COUNTRY_LEVEL,
            matched_entity_name=None,
            iso2=country_iso2,
            detail=(
                f"Text mentions country {country_iso2} but no named "
                f"ministry/directorate. INSUFFICIENT for ECJU EUC."
            ),
        )
    return GranularityResult(
        level=GranularityLevel.INSUFFICIENT,
        matched_entity_name=None,
        iso2=country_iso2,
        detail=(
            f"End-user statement '{stated_end_user[:120]}' does not "
            f"resolve to any acceptable directorate. "
            f"INSUFFICIENT for Clause 4.5(e) satisfaction."
        ),
    )


def is_sufficient_for_euc(stated_end_user: str, country_iso2: Optional[str] = None) -> bool:
    """True iff the end-user statement is specific enough for an
    Export-Control EUC / SIEL application."""
    result = score(stated_end_user, country_iso2)
    return result.level == GranularityLevel.SPECIFIC_DIRECTORATE


def list_known_entities(iso2: str) -> list[dict]:
    """All known acceptable end-users for a country."""
    return list(_BY_COUNTRY.get(iso2.upper(), []))


def render_finding(stated_end_user: str, country_iso2: Optional[str] = None) -> dict:
    """Finding-shaped dict for the DD orchestrator's compliance section."""
    result = score(stated_end_user, country_iso2)
    severity_map = {
        GranularityLevel.SPECIFIC_DIRECTORATE: "info",
        GranularityLevel.MINISTRY_LEVEL: "amber",
        GranularityLevel.COUNTRY_LEVEL: "red",
        GranularityLevel.INSUFFICIENT: "hard_stop",
    }
    title_map = {
        GranularityLevel.SPECIFIC_DIRECTORATE: "End-user adequately specified",
        GranularityLevel.MINISTRY_LEVEL: "End-user specified at ministry level — preferable to name specific service",
        GranularityLevel.COUNTRY_LEVEL: "End-user specified only at country level — INSUFFICIENT for ECJU EUC",
        GranularityLevel.INSUFFICIENT: "No acceptable end-user identified — HARD STOP",
    }
    finding = {
        "severity": severity_map[result.level],
        "title": title_map[result.level],
        "detail": result.detail,
        "source": (
            "end_user_granularity.score (R-F641) — "
            f"matched: {result.matched_entity_name or 'none'}"
        ),
        "confidence": "CONFIRMED" if result.matched_entity_name else "ASSESSED",
        "level": result.level,
        "matched_entity_name": result.matched_entity_name,
    }
    # R-F994 — wire to brain
    from .engine_wiring import wire_success
    wire_success(
        module="end_user_granularity",
        summary=f"End-user granularity: {result.level} ({severity_map[result.level]})",
        detail=result.detail[:600],
        entity_name=stated_end_user[:120],
        confidence=finding["confidence"],
        source_id="end_user_granularity:R-F641",
    )
    return finding
