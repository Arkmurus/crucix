"""coverage_heatmap — domain × jurisdiction knowledge coverage view
(R-F89, 2026-05-09).

Why this module exists
──────────────────────
Phase 2 of the independence roadmap. Total fact count is a vanity
metric — the honest measure is coverage across the matrix that
Arkmurus's customers actually care about: defence-DD domains × the
20 critical defence markets.

This module builds that matrix from existing knowledge + intel_ledger
data and surfaces it as a heatmap. Cells with low fact density / stale
data become the autonomous engine's targeting priority.

The matrix shape
────────────────
Rows = domains:
  sanctions_screening, eccn_classification, euc_jurisdictions,
  fatf_ml_typologies, fcpa_enforcement, defence_market_briefing,
  procurement_pipeline, weapon_systems, virtual_assets,
  sanctions_divergence, rca_screening, economic_substance, ...

Columns = jurisdictions / markets:
  Lusophone moat: Angola, Mozambique, Cape Verde, Guinea-Bissau, Brazil
  Wider Africa: Nigeria, Ghana, Kenya, Ethiopia, Tanzania, Senegal,
    Côte d'Ivoire, Cameroon, Rwanda, South Africa
  Gulf + MENA: Saudi Arabia, UAE, Qatar, Bahrain, Kuwait, Oman, Jordan,
    Iraq, Lebanon, Israel, Turkey, Egypt
  Asia-Pacific: Indonesia, Vietnam, Philippines, Bangladesh, India,
    Pakistan, South Korea, Japan
  LatAm: Mexico, Colombia, Peru, Venezuela, Argentina
  Europe (emerging): Romania, Poland, Ukraine
  Anchors: US, UK, EU, NATO

Each cell: { fact_count, signal_count, is_stale, last_refreshed_at,
              confidence_grade } — derived from learning_progress (R-F88)
              + a query against the knowledge base for the (domain,
              jurisdiction) pair.

Public API
──────────
    build_heatmap() -> dict
    coverage_score(matrix) -> float
    gap_targets(matrix, max_targets=20) -> list[dict]
    summary() -> dict
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("aria.coverage_heatmap")

# ── Domain rows ───────────────────────────────────────────────────

DOMAINS: list[str] = [
    # Sanctions surface
    "sanctions_screening",
    "sanctions_divergence",
    "rca_screening",

    # Export controls
    "eccn_classification",
    "euc_jurisdictions",
    "wassenaar_dual_use",
    "weapon_systems",

    # Anti-financial-crime
    "fatf_ml_typologies",
    "fatf_tbml",
    "fcpa_enforcement",
    "economic_substance",
    "virtual_assets",

    # Counterparty
    "defence_market_briefing",
    "procurement_pipeline",
    "counter_intelligence",

    # NATO + interoperability
    "nato_standards",
    "international_law",
]

# ── Jurisdiction columns ──────────────────────────────────────────

# Grouped for readability; flattened for the matrix
JURISDICTION_GROUPS: dict[str, list[str]] = {
    "anchors": ["US", "UK", "EU", "UN", "NATO"],
    "lusophone_moat": ["Angola", "Mozambique", "Cape Verde", "Guinea-Bissau",
                       "Brazil", "São Tomé"],
    "wider_africa":   ["Nigeria", "Ghana", "Kenya", "Ethiopia", "Tanzania",
                       "Senegal", "Côte d'Ivoire", "Cameroon", "Rwanda",
                       "South Africa", "Algeria", "Morocco"],
    "gulf_mena":      ["Saudi Arabia", "UAE", "Qatar", "Bahrain", "Kuwait",
                       "Oman", "Jordan", "Iraq", "Lebanon", "Israel",
                       "Turkey", "Egypt"],
    "asia_pacific":   ["Indonesia", "Vietnam", "Philippines", "Bangladesh",
                       "India", "Pakistan", "South Korea", "Japan"],
    "latam":          ["Mexico", "Colombia", "Peru", "Venezuela", "Argentina"],
    "europe_emerging": ["Romania", "Poland", "Ukraine"],
}

JURISDICTIONS: list[str] = [
    j for group in JURISDICTION_GROUPS.values() for j in group
]


# ── Cell density tiers ────────────────────────────────────────────

# Coverage grades for a single cell:
DENSITY_TIERS = [
    ("absent",   0,    0),     # 0 facts (gap)
    ("thin",     1,    9),     # 1-9 facts
    ("moderate", 10,   49),    # 10-49 facts
    ("strong",   50,   199),   # 50-199 facts
    ("deep",     200,  10**9), # 200+ facts
]


def density_tier(fact_count: int) -> str:
    for label, lo, hi in DENSITY_TIERS:
        if lo <= fact_count <= hi:
            return label
    return "absent"


# R-F128 (2026-05-10): jurisdiction synonyms — the dashboard heatmap
# was 100% absent because facts say "Saudi" not "Saudi Arabia",
# "UAE" or "Emirates" not "United Arab Emirates", "USA" not "US",
# etc. The previous matcher required EXACT substring of the canonical
# name and the result was 867 cells absent on a corpus of 20k facts.
JURISDICTION_SYNONYMS: dict[str, list[str]] = {
    "US":            ["us", "usa", "united states", "u.s.", "u.s.a"],
    "UK":            ["uk", "united kingdom", "britain", "british",
                      "great britain"],
    "EU":            ["eu", "european union", "europe"],
    "UN":            ["un", "united nations"],
    "NATO":          ["nato", "atlantic alliance"],
    "Angola":        ["angola", "angolan"],
    "Mozambique":    ["mozambique", "mozambican", "moçamb"],
    "Cape Verde":    ["cape verde", "cabo verde"],
    "Guinea-Bissau": ["guinea-bissau", "guinea bissau", "bissau"],
    "Brazil":        ["brazil", "brazilian", "brasil"],
    "São Tomé":      ["são tomé", "sao tome", "stp"],
    "Saudi Arabia":  ["saudi arabia", "saudi", "ksa", "riyadh"],
    "UAE":           ["uae", "u.a.e", "emirates", "united arab emirates",
                      "abu dhabi", "dubai"],
    "Côte d'Ivoire": ["côte d'ivoire", "cote d'ivoire", "ivory coast",
                      "ivorian"],
    "South Africa":  ["south africa", "south african"],
    "South Korea":   ["south korea", "republic of korea", "rok"],
    "North Korea":   ["north korea", "dprk"],
}


def _juris_synonyms(jurisdiction: str) -> list[str]:
    """Return lowercase synonym list for a jurisdiction (always includes
    the bare name)."""
    syn = JURISDICTION_SYNONYMS.get(jurisdiction)
    if syn:
        return [s.lower() for s in syn]
    return [jurisdiction.lower()]


def _domain_tokens(domain: str) -> list[str]:
    """Split a snake_case domain into significant lowercase tokens."""
    return [t for t in domain.lower().split("_") if len(t) >= 3]


def _matches_cell(text: str, dom_tokens: list[str],
                  jur_synonyms: list[str]) -> bool:
    """A fact matches a (domain, jurisdiction) cell when ANY jurisdiction
    synonym appears AND ALL substantive domain tokens appear in the text."""
    if not text:
        return False
    if not any(s in text for s in jur_synonyms):
        return False
    return all(tok in text for tok in dom_tokens)


async def _count_facts_for_cell(domain: str, jurisdiction: str) -> tuple[int, int]:
    """Return (fact_count, signal_count) for one matrix cell.

    Both knowledge base + intel_ledger are queried with token-level
    matching on domain + jurisdiction synonyms. R-F128 (2026-05-10)
    replaced the previous adjacency-required substring matcher (which
    scored 867/867 cells absent on 20k facts because "sanctions
    screening" rarely appears verbatim — facts say "OFAC sanctions
    list updated" + "screened against SDN" in separate fields).

    O(N) per cell today (no indexed query); 17×51 = 867 cells take
    ~2-4 seconds to build. Result is cached for 1h via the routes
    layer.
    """
    fact_count = 0
    signal_count = 0
    dom_tokens = _domain_tokens(domain)
    jur_synonyms = _juris_synonyms(jurisdiction)

    try:
        from . import knowledge as _k
        # R-F164 (2026-05-11): knowledge has no `search` function — the
        # previous hasattr probe silently failed for every cell, so fact_
        # count was 0/867 forever. Use the new `all_facts()` accessor and
        # iterate locally. With ~20k facts × 867 cells this is O(N*M), but
        # the matcher short-circuits hard on jurisdiction-synonym misses
        # (fact_text rarely contains the country) so most cells stop after
        # the synonym check. Cached for 1h via the routes layer.
        facts = _k.all_facts() if hasattr(_k, "all_facts") else []
        for fact in facts:
            if not isinstance(fact, dict):
                continue
            # Include `content` — facts store the body there. The previous
            # field list (entity, topic, summary, detail, source) missed
            # the actual fact body so even a "Saudi Arabia OFAC sanctions"
            # fact stored at `content` was scored as 0-match.
            text = " ".join(
                str(fact.get(k) or "") for k in
                ("entity", "topic", "content", "summary", "detail", "source")
            ).lower()
            if _matches_cell(text, dom_tokens, jur_synonyms):
                fact_count += 1
    except Exception as e:
        logger.debug("coverage knowledge query failed for %s/%s: %s",
                     domain, jurisdiction, e)

    # Same logic against intel_ledger if accessible
    try:
        from . import intel_ledger as _il
        for method_name in ("get_recent", "all_signals"):
            fn = getattr(_il, method_name, None)
            if not callable(fn):
                continue
            try:
                signals = fn()
                if hasattr(signals, "__await__"):
                    signals = await signals
            except Exception:
                continue
            if isinstance(signals, list):
                for s in signals:
                    if not isinstance(s, dict):
                        continue
                    text = " ".join(str(s.get(k) or "") for k in
                                    ("source", "summary", "detail",
                                     "entity", "topic")).lower()
                    if _matches_cell(text, dom_tokens, jur_synonyms):
                        signal_count += 1
                break
    except Exception as e:
        logger.debug("coverage ledger query failed: %s", e)

    return fact_count, signal_count


async def build_heatmap(
    *,
    domains: list[str] | None = None,
    jurisdictions: list[str] | None = None,
) -> dict[str, Any]:
    """Build the full coverage matrix.

    Returns:
        {
          "domains":       [...],
          "jurisdictions": [...],
          "matrix":        {domain: {jurisdiction: cell_dict, ...}, ...}
          "summary":       { coverage_score, gap_count, deep_cells, ... }
        }
    """
    domain_list = domains or DOMAINS
    juris_list = jurisdictions or JURISDICTIONS

    from . import learning_progress as _lp
    freshness_records = {}
    try:
        all_freshness = await _lp.get_all_domains()
        for f in all_freshness:
            freshness_records[f.get("domain", "")] = f
    except Exception:
        pass

    matrix: dict[str, dict[str, Any]] = {}
    for d in domain_list:
        matrix[d] = {}
        for j in juris_list:
            fact_count, signal_count = await _count_facts_for_cell(d, j)
            # R-F164: tier off the combined density. Signal_count was being
            # collected but never used in the grade, so a cell with 200
            # intel_ledger signals but 0 fact-store hits still rendered
            # 'absent'. Weight signals at 0.5 (they're noisier than curated
            # facts) so cells with strong ledger coverage but thin curated
            # knowledge still surface as moderate / strong.
            combined = fact_count + int(signal_count * 0.5)
            tier = density_tier(combined)
            # freshness for the domain (jurisdiction-specific freshness
            # would require finer-grained tagging — defer to next iter)
            domain_freshness = freshness_records.get(d, {})
            matrix[d][j] = {
                "fact_count":          fact_count,
                "signal_count":        signal_count,
                "tier":                tier,
                "is_stale":            domain_freshness.get("is_stale", True),
                "hours_since_refresh": domain_freshness.get("hours_since_refresh"),
            }

    score, summary_stats = _compute_score(matrix, domain_list, juris_list)
    return {
        "domains":            domain_list,
        "jurisdictions":      juris_list,
        "jurisdiction_groups": JURISDICTION_GROUPS,
        "matrix":             matrix,
        "summary":            summary_stats,
        "coverage_score":     score,
    }


def _compute_score(
    matrix: dict[str, dict[str, Any]],
    domains: list[str],
    jurisdictions: list[str],
) -> tuple[float, dict[str, Any]]:
    """Composite coverage score in [0, 1].

    Weighted by tier:
      absent=0, thin=0.25, moderate=0.50, strong=0.80, deep=1.00
    Average across all cells. Stale cells get 0.7× weight (still count
    as some coverage but less).
    """
    tier_weights = {"absent": 0.0, "thin": 0.25, "moderate": 0.50, "strong": 0.80, "deep": 1.00}
    total = 0
    n = 0
    deep_count = 0
    gap_count = 0
    stale_count = 0
    for d in domains:
        for j in jurisdictions:
            cell = matrix.get(d, {}).get(j) or {"tier": "absent"}
            w = tier_weights.get(cell.get("tier", "absent"), 0.0)
            if cell.get("is_stale"):
                w *= 0.7
                stale_count += 1
            total += w
            n += 1
            if cell.get("tier") == "deep":
                deep_count += 1
            if cell.get("tier") == "absent":
                gap_count += 1
    score = round(total / n, 3) if n else 0.0
    return score, {
        "cells":       n,
        "gap_count":   gap_count,
        "deep_cells":  deep_count,
        "stale_cells": stale_count,
        "gap_pct":     round(gap_count / n * 100, 1) if n else 0,
    }


def gap_targets(
    heatmap: dict[str, Any],
    *,
    max_targets: int = 20,
) -> list[dict[str, Any]]:
    """Pick the highest-priority gaps for autonomous targeting.

    Priority = absent > thin > moderate. Within tier, prefer cells
    where the domain + jurisdiction combination is high commercial
    value (Lusophone moat + critical anchors get a multiplier).
    """
    matrix = heatmap.get("matrix") or {}
    candidates: list[tuple[float, dict[str, Any]]] = []
    high_value_jurisdictions = set(JURISDICTION_GROUPS["lusophone_moat"]
                                   + JURISDICTION_GROUPS["anchors"])
    for d, jur_map in matrix.items():
        for j, cell in jur_map.items():
            tier = cell.get("tier", "absent")
            base = {"absent": 1.0, "thin": 0.7, "moderate": 0.3}.get(tier, 0.0)
            if base == 0:
                continue
            multiplier = 1.5 if j in high_value_jurisdictions else 1.0
            score = base * multiplier
            candidates.append((score, {
                "domain":       d,
                "jurisdiction": j,
                "tier":         tier,
                "fact_count":   cell.get("fact_count", 0),
                "is_stale":     cell.get("is_stale", True),
                "priority":     round(score, 3),
                "narrative":    f"{d} × {j}: {tier} ({cell.get('fact_count', 0)} facts).",
            }))
    candidates.sort(key=lambda kv: -kv[0])
    return [c[1] for c in candidates[:max_targets]]


def summary() -> dict[str, Any]:
    return {
        "module":              "coverage_heatmap",
        "domains_count":       len(DOMAINS),
        "jurisdictions_count": len(JURISDICTIONS),
        "matrix_size":         len(DOMAINS) * len(JURISDICTIONS),
        "groups":               list(JURISDICTION_GROUPS.keys()),
        "purpose":              "domain × jurisdiction knowledge-coverage view",
    }
