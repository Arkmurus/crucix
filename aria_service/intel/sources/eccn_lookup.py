"""ECCN / Wassenaar lookup table (R-F581, 2026-05-16).

Why this exists
───────────────
Model card has long flagged this as a known limitation:
    "Equipment ↔ ECCN/Wassenaar mapping currently prompt-augmented,
     not lookup-driven; in roadmap."

Pre-R-F581 the export-control layer asked the LLM to classify
equipment freeform. That had two failure modes:

  1. **Hallucinated ECCNs.** The LLM sometimes invented numbers like
     "ECCN 7A998" that don't exist in the real CCL.
  2. **Inconsistent recall.** The same equipment description scored
     different ECCNs across runs, depending on context window.

R-F581 ships a deterministic lookup: keyword match → ECCN + regime +
country restrictions + reference URL. Falls under [[aria_mirrors_claude]] —
pure native logic, no third-party API, no LLM call required.

Coverage
────────
Seed dataset (`data/eccn_lookup.json`) covers ~20 defence-broking-
relevant categories:
  - 9A010, 9A012 (UAVs + components)
  - 5A001, 5A002 (telecom + crypto)
  - 6A005, 6A008 (lasers, radar)
  - 7A003 (inertial navigation)
  - 9A001, 9A002 (aero + marine gas turbines)
  - 0A001 (nuclear)
  - 1C351 (CW precursors)
  - 3A001 (rad-hard electronics)
  - ML1, ML2, ML4, ML10, ML11, ML15, ML18, ML22 (munitions)

Not exhaustive (the full CCL is hundreds of pages); meant to cover
≥80% of defence-broker requests with a primary-source citation. Adding
new ECCNs is a one-line JSON append — no module change required.

Honesty discipline
──────────────────
Per clause 3 + clause 14: the lookup is an INDICATOR, not a legal
determination. Every hit carries `tier: "1a"` and a `reference_url`
to the public BIS / Wassenaar source. Callers (dd_orchestrator,
export-control layer) must tag findings as "indicator — operator
should consult licensed export counsel" before sharing externally.
"""
from __future__ import annotations

import json
import logging
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

logger = logging.getLogger("aria.intel.sources.eccn_lookup")

_DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "eccn_lookup.json"


@lru_cache(maxsize=1)
def _load_data() -> dict[str, Any]:
    """Load + cache the seed dataset. lru_cache(1) ensures a single
    parse per process. Falls back to an empty dataset on read error
    so the rest of the system keeps running."""
    try:
        with _DATA_PATH.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.warning("[R-F581] could not load eccn_lookup.json: %s", e)
        return {"version": "unknown", "categories": {}}


def lookup_by_keyword(
    text: str,
    *,
    limit: int = 8,
) -> list[dict[str, Any]]:
    """Return ECCN hits whose keyword list intersects with `text`.

    Pure function — no IO except the cached data load. Case-insensitive
    word-boundary matching so 'radar' matches in 'radar systems' but
    'rad' (substring) does not match 'radar' falsely.

    Returns hits sorted by match-strength (count of matched keywords),
    ties broken by ECCN code order for determinism.
    """
    if not text or not text.strip():
        return []
    data = _load_data()
    categories = data.get("categories") or {}
    if not categories:
        # R-F1304 — wire empty data as warning
        try:
            from ..engine_wiring import wire_failure
            wire_failure(
                module="sources.eccn_lookup",
                detail="lookup_by_keyword: no ECCN categories loaded",
                gap_type="source_failure",
                source="sources:eccn_lookup:lookup_by_keyword",
            )
        except Exception:
            pass
        return []

    text_lower = text.lower()
    hits: list[tuple[int, str, dict[str, Any], list[str]]] = []

    for eccn, entry in categories.items():
        keywords = entry.get("keywords") or []
        matched_kws: list[str] = []
        for kw in keywords:
            kw_lower = kw.lower()
            # Word-boundary check — multi-word keywords like "inertial
            # navigation" should match as a phrase, not as overlapping
            # substrings. Use a permissive boundary check that handles
            # hyphens and unicode whitespace.
            if " " in kw_lower or "-" in kw_lower:
                if kw_lower in text_lower:
                    matched_kws.append(kw)
            else:
                # Single-word keyword: enforce word boundary
                pattern = r"\b" + re.escape(kw_lower) + r"\b"
                if re.search(pattern, text_lower):
                    matched_kws.append(kw)

        if matched_kws:
            hits.append((len(matched_kws), eccn, entry, matched_kws))

    # Sort: highest match count first, then by ECCN string for stable order
    hits.sort(key=lambda x: (-x[0], x[1]))

    out: list[dict[str, Any]] = []
    for match_count, eccn, entry, matched_kws in hits[:limit]:
        out.append({
            "eccn":                 eccn,
            "title":                entry.get("title", ""),
            "regime":               entry.get("regime", ""),
            "wassenaar_ref":        entry.get("wassenaar_ref", ""),
            "matched_keywords":     matched_kws,
            "match_count":          match_count,
            "country_restrictions": entry.get("country_restrictions", []),
            "license_exceptions":   entry.get("license_exceptions", []),
            "reference_url":        entry.get("reference_url", ""),
            "source":               "bis_ccl_seed_rf581",
            "tier":                 "1a",
            "honesty_note": (
                "Indicator only — per clause 3, final ECCN classification "
                "must be confirmed by a licensed export-control counsel."
            ),
        })
    return out


def lookup_by_eccn(eccn: str) -> dict[str, Any] | None:
    """Return the full entry for a specific ECCN (e.g. '9A010') or None
    if not in the seed dataset."""
    if not eccn:
        return None
    data = _load_data()
    entry = (data.get("categories") or {}).get(eccn.upper().strip())
    if not entry:
        return None
    return {
        "eccn":                 eccn.upper().strip(),
        "title":                entry.get("title", ""),
        "regime":               entry.get("regime", ""),
        "wassenaar_ref":        entry.get("wassenaar_ref", ""),
        "keywords":             entry.get("keywords", []),
        "country_restrictions": entry.get("country_restrictions", []),
        "license_exceptions":   entry.get("license_exceptions", []),
        "reference_url":        entry.get("reference_url", ""),
        "source":               "bis_ccl_seed_rf581",
        "tier":                 "1a",
    }


def list_all_eccns() -> list[str]:
    """Return all ECCN codes in the seed dataset, for diagnostics."""
    data = _load_data()
    return sorted((data.get("categories") or {}).keys())


def dataset_version() -> str:
    """Return the dataset version string."""
    return _load_data().get("version", "unknown")
