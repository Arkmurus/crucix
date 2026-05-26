"""sanctions_divergence — cross-list divergence analysis (R-F68, 2026-05-09).

Maps OpenSanctions `datasets` field → jurisdictions, then surfaces
*where the same entity appears on some lists but not others*. This is
exactly what sophisticated sanctions evaders exploit: an entity removed
from the EU list but still on OFAC can move money through European
correspondent banks; an entity added to HMT but not yet OFAC creates a
window for US-exposed transactions before the US designation catches up.

The data is already flowing — `sanctions.fuzzy_screen()` returns the
`lists` array on every match. This module is the cross-reference query
that nobody was running.

Public API
──────────
    analyze_divergence(name) -> dict
        High-level: screen the name, group by jurisdiction, surface
        divergences (jurisdictions that have it AND those that don't).

    jurisdiction_for_dataset(dataset_id) -> str | None
        Lookup helper.

Output shape (analyze_divergence):
    {
      "name": "...",
      "matches": N,
      "jurisdictions_listed":     ["US", "UK", "EU", "UN", ...],
      "jurisdictions_not_listed": ["CA", "CH", "AU", "JP", ...],
      "divergence_count":         3,            # how many tracked
                                                # jurisdictions show
                                                # divergence
      "per_match": [
        {
          "name": "...", "score": 0.97,
          "listed_in": [
            {"jurisdiction": "US", "list": "OFAC SDN", "dataset_id": "us_ofac_sdn"},
            {"jurisdiction": "UK", "list": "HMT OFSI", "dataset_id": "gb_hmt_sanctions"},
            ...
          ],
          "first_seen":  "2024-08-...",
          "last_change": "2026-04-..."
        },
        ...
      ],
      "narrative": "Entity X is on US (OFAC) and UK (HMT) but NOT on
                    EU FSF or UN SC. ... ",
    }

Why this matters commercially
─────────────────────────────
No competitor maps cross-list divergences as a first-class output.
Compliance officers reading a counterparty DD report should see the
divergence pattern explicitly — it changes the transaction routing
choice (which correspondent bank, which payment rails, which clearing
jurisdiction).
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger("aria.sanctions_divergence")

# OpenSanctions dataset_id → (jurisdiction_iso, list_label).
# Sourced from https://www.opensanctions.org/datasets/. Keep this list
# narrow and authoritative — every entry is a sanctions list that the
# defence-DD desk actually cares about. Adding more is cheap (one row);
# adding bad ones (commercial PEP lists, etc.) pollutes the divergence
# narrative, so we curate.
_DATASET_TO_JURISDICTION: dict[str, tuple[str, str]] = {
    # United States
    "us_ofac_sdn":       ("US", "OFAC SDN"),
    "us_ofac_cons":      ("US", "OFAC Consolidated (non-SDN)"),
    "us_bis_denied":     ("US", "BIS Denied Persons"),
    "us_bis_entity":     ("US", "BIS Entity List"),
    "us_bis_unverified": ("US", "BIS Unverified List"),
    "us_bis_mil":        ("US", "BIS Military End User"),
    "us_dod_1260h":      ("US", "DoD Section 1260H (Chinese Military Companies)"),
    "us_state_terrorist": ("US", "State Dept SDGT/FTO"),
    "us_trade_csl":      ("US", "Consolidated Screening List"),

    # United Kingdom
    "gb_hmt_sanctions":  ("UK", "HMT OFSI Consolidated"),
    "gb_fcdo_sanctions": ("UK", "FCDO Sanctions List"),

    # European Union
    "eu_fsf":            ("EU", "Financial Sanctions Files (CFSP)"),
    "eu_travel_ban":     ("EU", "Travel Ban"),
    "eu_meps":           ("EU", "Members of European Parliament"),  # not sanctions, PEP

    # United Nations
    "un_sc_sanctions":   ("UN", "Security Council Consolidated"),

    # Canada
    "ca_dfatd_sema":     ("CA", "Canada SEMA Consolidated"),
    "ca_listed_terror":  ("CA", "Canada Listed Terrorist Entities"),

    # Switzerland
    "ch_seco_sanctions": ("CH", "SECO Sanctions"),

    # Australia
    "au_dfat_sanctions": ("AU", "DFAT Consolidated"),

    # Japan
    "jp_mof_sanctions":  ("JP", "MoF Foreign Exchange Sanctions"),

    # France (specific designations beyond EU FSF)
    "fr_tresor_gels":    ("FR", "Trésor — Gels Avoirs"),

    # Ukraine (NSDC counter-sanctions register)
    "ua_nabc_sanctions": ("UA", "NABC Counter-sanctions"),

    # Russia (counter-sanctions register — useful for reverse exposure)
    "ru_minfin_blocked": ("RU", "Russia MoF Blocked Persons"),

    # Singapore / Hong Kong / Israel etc — add as defence-DD desk
    # counter-parties show up there. Curated, not exhaustive.
    "sg_terrorism":      ("SG", "Terrorism (Suppression of Financing) Act"),
    "il_terror":         ("IL", "Israel Terror Designations"),
}

# The jurisdictions we actively *track* — divergences are computed against
# this set, not against every jurisdiction in the table. Adding a country
# here changes the divergence narrative; removing one hides it.
_TRACKED_JURISDICTIONS: list[str] = ["US", "UK", "EU", "UN", "CA", "CH", "AU"]


def jurisdiction_for_dataset(dataset_id: str) -> tuple[str, str] | None:
    """Lookup helper. Returns (jurisdiction_iso, list_label) or None."""
    return _DATASET_TO_JURISDICTION.get(dataset_id)


def _build_per_match(match: dict[str, Any]) -> dict[str, Any]:
    """For one match, group its `lists` array by jurisdiction."""
    listed: list[dict[str, str]] = []
    seen_jurisdictions: set[str] = set()

    for ds in match.get("lists") or []:
        meta = _DATASET_TO_JURISDICTION.get(ds)
        if not meta:
            # Unmapped dataset — surface as raw, don't silently drop.
            # Lets the operator add new mappings as new lists appear.
            listed.append({
                "jurisdiction": "UNMAPPED",
                "list": ds,
                "dataset_id": ds,
            })
            continue
        jurisdiction, label = meta
        listed.append({
            "jurisdiction": jurisdiction,
            "list": label,
            "dataset_id": ds,
        })
        seen_jurisdictions.add(jurisdiction)

    return {
        "name":        match.get("name", ""),
        "score":       match.get("score", 0.0),
        "listed_in":   listed,
        "jurisdictions": sorted(seen_jurisdictions),
        "first_seen":  match.get("first_seen"),
        "last_change": match.get("last_change"),
        "url":         match.get("url"),
        "topics":      match.get("topics", []),
        "relationships": match.get("relationships", []),
    }


# R-F133 (2026-05-10): token-overlap filter for fuzzy matches.
# Live evidence on /explorer.html search "Assan Group Turkey":
# fuzzy_screen at threshold 0.78 returned RosAero FZC (Russian
# aviation, score 0.877) and "德里斯·范阿赫特" (Dries van Achter,
# Dutch PEP, score 0.833). Both shared NO substantive token with the
# query yet drove the divergence narrative ("listed on CH, US but
# NOT on UK, EU..."). The narrative was structurally correct but the
# matches it referenced were unrelated entities. Operators read this
# as a false positive against Assan Group when the screen result
# actually belonged to other firms entirely.
import re as _re_div


def _substantive_tokens(s: str) -> set[str]:
    """Return the set of lowercased Latin-script tokens of length ≥ 4
    that we consider substantive for entity-name overlap. Drops common
    corporate suffixes (Group, Limited, Holdings...) so e.g. "Assan
    Group Turkey" → {"assan", "turkey"}."""
    if not s:
        return set()
    stop = {
        "group", "limited", "holdings", "holding", "company", "corp",
        "corporation", "inc", "incorporated", "ltd", "llc", "lp",
        "plc", "gmbh", "private", "public", "international", "industries",
        "industrial", "trading", "trade", "services", "service",
        "global", "worldwide", "co", "the", "and",
    }
    tokens = _re_div.findall(r"[A-Za-z]{4,}", s.lower())
    return {t for t in tokens if t not in stop}


def _shares_substantive_token(query_name: str, match_name: str) -> bool:
    """A match counts as related to the query iff they share at least
    one substantive Latin-script token. Pure non-Latin matches (e.g.
    Cyrillic, CJK without a Latin caption) get a free pass — fuzzy_screen
    is responsible for transliteration."""
    q = _substantive_tokens(query_name)
    m = _substantive_tokens(match_name)
    if not q:
        return True   # query has no scoreable tokens — accept all
    if not m:
        return True   # match is non-Latin only — fuzzy_screen vouches
    return bool(q & m)


def _build_narrative(name: str, listed: list[str], not_listed: list[str]) -> str:
    """One-line operator-readable summary of the divergence pattern.

    Examples:
        'X is on US, UK, EU but NOT on UN, CA, CH, AU.'
        'X is on US (OFAC SDN) only.'
        'X is on EU + UN — US-exposed transactions free of OFAC restriction.'
    """
    if not listed:
        return f"{name}: no tracked jurisdictions list this entity."
    if not not_listed:
        return f"{name}: listed across all tracked jurisdictions ({', '.join(listed)})."
    listed_str = ", ".join(listed)
    not_listed_str = ", ".join(not_listed)
    return f"{name}: listed on {listed_str} but NOT on {not_listed_str}."


async def analyze_divergence(name: str, *, threshold: float = 0.78) -> dict[str, Any]:
    """High-level divergence query.

    Calls the existing `sanctions.fuzzy_screen` (which already aggregates
    OFAC, OFSI, EU FSF, UN SC, and 100+ other lists via OpenSanctions),
    then groups by jurisdiction and surfaces the divergence pattern.

    Returns the empty-divergence shape on no matches — never raises.
    """
    try:
        from . import sanctions as _s
        screen = await _s.fuzzy_screen(name, threshold=threshold)
    except Exception as e:
        logger.warning("sanctions divergence: fuzzy_screen failed for %r: %s", name, e)
        return {
            "name": name,
            "ok": False,
            "error": str(e),
            "matches": 0,
            "jurisdictions_listed": [],
            "jurisdictions_not_listed": list(_TRACKED_JURISDICTIONS),
            "per_match": [],
            "narrative": f"{name}: screen failed — {e}",
        }

    matches: list[dict[str, Any]] = screen.get("matches", []) or []
    # R-F133 — strip fuzzy matches that don't share a substantive
    # token with the query. fuzzy_screen at threshold 0.78 will
    # admit matches purely on transliteration / character bigrams;
    # for the divergence narrative to be honest, a match must
    # plausibly be the same entity. We log the drop count so it's
    # visible in fly logs how aggressive this filter is.
    raw_match_count = len(matches)
    filtered_matches: list[dict[str, Any]] = []
    for m in matches:
        m_name = m.get("name") or m.get("caption") or ""
        if _shares_substantive_token(name, m_name):
            filtered_matches.append(m)
    if raw_match_count and len(filtered_matches) < raw_match_count:
        logger.info(
            "sanctions divergence: dropped %d/%d unrelated fuzzy matches for %r",
            raw_match_count - len(filtered_matches), raw_match_count, name,
        )
    matches = filtered_matches
    if not matches:
        return {
            "name": name,
            "ok": True,
            "matches": 0,
            "raw_match_count": raw_match_count,  # surface the drop
            "jurisdictions_listed": [],
            "jurisdictions_not_listed": list(_TRACKED_JURISDICTIONS),
            "divergence_count": 0,
            "per_match": [],
            "narrative": (
                f"{name}: no sanctions matches above threshold {threshold} "
                f"share a substantive token with the query."
                if raw_match_count else
                f"{name}: no sanctions matches above threshold {threshold}."
            ),
        }

    per_match = [_build_per_match(m) for m in matches]

    # Aggregate: any tracked jurisdiction listing ANY match counts as
    # "listed". The narrative reflects the union across matches because
    # a fuzzy screen returns one canonical entity + its variants — they
    # are the same real-world target.
    listed: set[str] = set()
    for pm in per_match:
        for j in pm["jurisdictions"]:
            if j in _TRACKED_JURISDICTIONS:
                listed.add(j)

    listed_sorted   = sorted(listed)
    not_listed      = [j for j in _TRACKED_JURISDICTIONS if j not in listed]
    narrative       = _build_narrative(name, listed_sorted, not_listed)

    # R-F898 — a real cross-list divergence (listed on some tracked
    # jurisdictions, silent on others) is high-value intel ARIA should
    # remember (pay-once-remember §15); the success/learning side of this
    # engine was dark. Fire-and-forget so the divergence query isn't blocked
    # on the brain fan-out. Only fires on a genuine divergence (not "listed
    # everywhere" / "listed nowhere").
    if listed_sorted and not_listed:
        try:
            from . import brain_hook as _bh
            coro = _bh.absorb_silent(
                module="sanctions",
                summary=f"Sanctions cross-list divergence: {narrative}"[:200],
                detail=narrative[:600],
                entity_name=str(name)[:120],
                success=True,
                confidence="PROBABLE",
                source_id="sanctions_divergence:R-F898",
            )
        except Exception:
            coro = None
        if coro is not None:
            try:
                _t = asyncio.create_task(coro)  # analyze_divergence is async → loop present
                _t.add_done_callback(
                    lambda t: t.result() if not t.cancelled() and not t.exception() else None
                )
            except Exception:
                try:
                    coro.close()
                except Exception:
                    pass

    return {
        "name": name,
        "ok": True,
        "matches": len(matches),
        "jurisdictions_listed":     listed_sorted,
        "jurisdictions_not_listed": not_listed,
        "divergence_count":         len(not_listed) if listed else 0,
        "per_match":                per_match,
        "narrative":                narrative,
        "tracked_jurisdictions":    list(_TRACKED_JURISDICTIONS),
        "threshold":                threshold,
    }


def summary() -> dict[str, Any]:
    """Capability-manifest entry — shape matches sibling modules."""
    return {
        "module":                "sanctions_divergence",
        "tracked_jurisdictions": list(_TRACKED_JURISDICTIONS),
        "mapped_datasets":       len(_DATASET_TO_JURISDICTION),
        "data_source":           "OpenSanctions (consolidates 100+ lists)",
    }
