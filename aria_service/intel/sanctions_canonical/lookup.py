"""Unified canonical sanctions lookup with R-F518-pattern entity gate.

The headline function is `check_sanctions(name, jurisdiction='',
address='')` which returns:

  {
    "queried_name": "...",
    "verdict": "HARD_STOP" | "REVIEW" | "CLEAR" | "INSUFFICIENT_DATA",
    "matches": [
      {
        "source": "ofac_sdn" | "eu_consolidated" | ...,
        "source_uid": "...",
        "formatted_name": "...",
        "alias_matched": "..." or None,
        "match_method": "exact" | "exact_alias" | "jaccard" | "blocked_entity_gate",
        "match_score": 0..1,
        "entity_overlap": ["token", ...],   # R-F518 gate evidence
        "jurisdiction_overlap": True/False,
        "address_overlap": True/False,
        "countries": [...],
        "programs": [...],
        "raw_excerpt": "..." (truncated),
      }, ...
    ],
    "gate_blocked": [...],   # candidates blocked by the gate (for transparency)
    "cache_status": { "ofac_sdn": {...}, "eu_consolidated": {...} },
  }

The R-F518 entity-overlap gate
═══════════════════════════════
A name hit alone NEVER produces HARD_STOP. The candidate must
additionally pass at least ONE of:

  (a) Exact normalised-name equality with the query (handles
      explicit "Michele Zagaria" lookup against the SDN entry)
  (b) Entity-token overlap ≥1 AND jurisdiction overlap (query's
      named jurisdiction matches the candidate's country)
  (c) Entity-token overlap ≥1 AND address-country overlap (query's
      provided address country matches one of the candidate's
      country fields)
  (d) Multi-token entity overlap ≥2 (proper-noun pairs like
      "Wagner Group" matching "Wagner PMC" on shared {wagner})

If a candidate fails the gate, its match_method is recorded as
`blocked_entity_gate` and it appears in `gate_blocked` for audit
transparency — but it does NOT contribute to the verdict.

Live 2026-05-14 Swisscraft repro: query("Swisscraft Aviation Ltd",
jurisdiction="Switzerland", address="Via Industria 6, Biasca,
Switzerland") against an SDN store containing "ZAGARIA, Michele"
with country=Italy → no entity overlap on Zagaria, no jurisdiction
overlap (Italy vs Switzerland), no address overlap → blocked by
gate → verdict NOT HARD_STOP.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from . import store
from .normalise import entity_tokens, jaccard, normalise_name

logger = logging.getLogger("aria.sanctions_canonical.lookup")

# Tunables — kept loose, the entity-overlap gate is the real safety net.
_JACCARD_FLOOR = 0.5
_HARD_STOP_THRESHOLD = 0.85


def _country_aliases() -> dict[str, set[str]]:
    """Map common jurisdiction names to a set of country-string aliases
    that may appear in the canonical store's countries field."""
    # Lightweight — extend as needed. Lowercased keys + lowercased values.
    return {
        "uk": {"united kingdom", "uk", "great britain", "britain", "gb", "england"},
        "us": {"united states", "us", "usa", "u.s.", "u.s.a."},
        "eu": set(),  # EU is a regime, not a country
        "switzerland": {"switzerland", "swiss", "ch", "schweiz"},
        "italy": {"italy", "italia", "italian republic", "it"},
        "russia": {"russia", "russian federation", "ru"},
        "iran": {"iran", "iranian", "islamic republic of iran", "ir"},
        "syria": {"syria", "syrian", "syrian arab republic", "sy"},
        "north korea": {"north korea", "dprk", "korea, democratic peoples republic of"},
        "yemen": {"yemen", "yemeni", "ye"},
        "libya": {"libya", "libyan", "ly"},
        "sudan": {"sudan", "sudanese", "sd"},
        "myanmar": {"myanmar", "burma", "burmese", "mm"},
        "venezuela": {"venezuela", "venezuelan", "ve"},
        "belarus": {"belarus", "belarusian", "by"},
        "cuba": {"cuba", "cuban", "cu"},
    }


def _country_match(query: str, candidate_countries: list[str]) -> bool:
    """True if the operator-supplied jurisdiction/address-country
    overlaps any of the candidate's country fields."""
    if not query or not candidate_countries:
        return False
    q = query.lower().strip()
    cands_l = [c.lower().strip() for c in candidate_countries]
    if q in cands_l:
        return True
    # Alias-expand the query
    for canonical, alias_set in _country_aliases().items():
        if q == canonical or q in alias_set:
            if any(c in alias_set or c == canonical for c in cands_l):
                return True
    return False


def _evaluate_gate(
    query_entity_tokens: set[str],
    query_normalised: str,
    candidate_entity_tokens: set[str],
    candidate_normalised: str,
    jurisdiction: str,
    address: str,
    candidate_countries: list[str],
) -> tuple[bool, dict]:
    """Apply the R-F518 entity-overlap gate.

    Returns (passes, evidence-dict). `evidence` always carries the
    fields that drove the decision for audit transparency.
    """
    evidence = {
        "entity_overlap": sorted(query_entity_tokens & candidate_entity_tokens),
        "jurisdiction_overlap": False,
        "address_overlap": False,
        "exact_name_match": False,
        "rule_applied": None,
    }

    # (a) Exact normalised-name equality — explicit lookup case.
    if query_normalised and query_normalised == candidate_normalised:
        evidence["exact_name_match"] = True
        evidence["rule_applied"] = "exact_name"
        return True, evidence

    overlap_size = len(query_entity_tokens & candidate_entity_tokens)

    # (d) Multi-token entity overlap ≥2 — proper-noun pair safety.
    if overlap_size >= 2:
        evidence["rule_applied"] = "multi_token_entity_overlap"
        return True, evidence

    # (b) Single-token entity overlap + jurisdiction match
    if overlap_size >= 1 and jurisdiction:
        if _country_match(jurisdiction, candidate_countries):
            evidence["jurisdiction_overlap"] = True
            evidence["rule_applied"] = "entity_plus_jurisdiction"
            return True, evidence

    # (c) Single-token entity overlap + address-country match
    if overlap_size >= 1 and address:
        # crude country extraction — last comma-separated token
        addr_country = address.rsplit(",", 1)[-1].strip()
        if addr_country and _country_match(addr_country, candidate_countries):
            evidence["address_overlap"] = True
            evidence["rule_applied"] = "entity_plus_address"
            return True, evidence

    # Failed: candidate name fuzzy-matched the query but neither
    # jurisdiction nor address nor multi-token-entity overlap was found.
    evidence["rule_applied"] = "blocked"
    return False, evidence


def check_sanctions(
    name: str,
    jurisdiction: str = "",
    address: str = "",
    *,
    sources: list[str] | None = None,
) -> dict:
    """Canonical sanctions screen. The headline function callers consume.

    `name`         — the entity / person name to check.
    `jurisdiction` — operator-asserted jurisdiction (e.g. "Switzerland").
    `address`      — operator-asserted address (free text; last
                     comma-separated token treated as country).
    `sources`      — restrict to a subset of canonical sources.
                     Default: all sources currently in cache.

    Returns the structure documented at the top of this module.
    """
    queried_name = (name or "").strip()
    if not queried_name:
        # R-F1304 — wire empty-name case as warning
        try:
            from ..engine_wiring import wire_failure
            wire_failure(
                module="sanctions_canonical.lookup",
                detail="check_sanctions called with empty name",
                gap_type="input_error",
                source="sanctions_canonical:lookup:check_sanctions",
            )
        except Exception:
            pass
        return {
            "queried_name": "",
            "verdict": "INSUFFICIENT_DATA",
            "matches": [],
            "gate_blocked": [],
            "cache_status": _cache_status_summary(),
            "reason": "no name supplied",
        }
    q_normalised = normalise_name(queried_name)
    q_entity_tokens = entity_tokens(q_normalised)

    matches: list[dict] = []
    blocked: list[dict] = []

    with store.connect() as conn:
        cur = conn.cursor()
        params = []
        where = []
        if sources:
            placeholders = ",".join(["?"] * len(sources))
            where.append(f"e.source IN ({placeholders})")
            params.extend(sources)
        sql_where = (" AND " + " AND ".join(where)) if where else ""

        # First pass — exact normalised-name match on the primary or any alias.
        cur.execute(
            f"""
            SELECT e.id, e.source, e.source_uid, e.formatted_name,
                   e.normalised_name, e.entity_type, e.countries,
                   e.addresses, e.aliases, e.programs, e.raw_excerpt
            FROM entries e
            WHERE (e.normalised_name = ? OR e.id IN (
                SELECT entry_id FROM aliases WHERE normalised = ?
            )) {sql_where}
            """,
            [q_normalised, q_normalised, *params],
        )
        exact_rows = cur.fetchall()
        seen_ids = {r[0] for r in exact_rows}

        # Second pass — token-overlap candidates (cheap pre-filter via
        # any-shared-token via aliases). We fetch all rows with at
        # least one shared word in the normalised name; jaccard-score
        # them in Python.
        if q_entity_tokens:
            placeholders = ",".join(["?"] * len(q_entity_tokens))
            cur.execute(
                f"""
                SELECT e.id, e.source, e.source_uid, e.formatted_name,
                       e.normalised_name, e.entity_type, e.countries,
                       e.addresses, e.aliases, e.programs, e.raw_excerpt
                FROM entries e
                WHERE e.id IN (
                    SELECT entry_id FROM aliases
                    WHERE normalised LIKE ? {''.join([' OR normalised LIKE ?'] * (len(q_entity_tokens) - 1))}
                ) {sql_where}
                LIMIT 500
                """,
                [
                    *[f"%{t}%" for t in q_entity_tokens],
                    *params,
                ],
            )
            for r in cur.fetchall():
                if r[0] in seen_ids:
                    continue
                seen_ids.add(r[0])
                exact_rows.append(r)

        for row in exact_rows:
            (entry_id, src, source_uid, formatted, norm, entity_type,
             countries_json, addresses_json, aliases_json, programs_json,
             raw_excerpt) = row
            try:
                countries = json.loads(countries_json or "[]")
                addresses_l = json.loads(addresses_json or "[]")
                aliases_list = json.loads(aliases_json or "[]")
                programs = json.loads(programs_json or "[]")
            except json.JSONDecodeError:
                countries, addresses_l, aliases_list, programs = [], [], [], []

            cand_entity_tokens = entity_tokens(norm)
            # Best alias score for this candidate
            best_alias_match = None
            best_score = 0.0
            for a in aliases_list:
                a_norm = a.get("normalised", "")
                if a_norm == q_normalised:
                    best_score = 1.0
                    best_alias_match = a.get("formatted", "")
                    break
                s = jaccard(q_entity_tokens, entity_tokens(a_norm))
                if s > best_score:
                    best_score = s
                    best_alias_match = a.get("formatted", "")
            # Also score against the primary normalised
            primary_score = jaccard(q_entity_tokens, cand_entity_tokens)
            if primary_score > best_score:
                best_score = primary_score
                best_alias_match = None

            if best_score < _JACCARD_FLOOR and q_normalised != norm:
                # Below floor and not an exact match — drop
                continue

            passes, evidence = _evaluate_gate(
                q_entity_tokens, q_normalised,
                cand_entity_tokens, norm,
                jurisdiction, address, countries,
            )

            entry_dict = {
                "source": src,
                "source_uid": source_uid,
                "formatted_name": formatted,
                "alias_matched": best_alias_match,
                "match_method": (
                    "exact" if best_score >= 0.999
                    else "exact_alias" if best_alias_match and best_score >= 0.999
                    else "jaccard"
                ),
                "match_score": round(best_score, 3),
                "entity_overlap": evidence["entity_overlap"],
                "jurisdiction_overlap": evidence["jurisdiction_overlap"],
                "address_overlap": evidence["address_overlap"],
                "gate_rule": evidence["rule_applied"],
                "countries": countries,
                "addresses": addresses_l[:3],   # cap for response weight
                "programs": programs,
                "entity_type": entity_type,
                "raw_excerpt": (raw_excerpt or "")[:500],
            }
            if passes:
                matches.append(entry_dict)
            else:
                entry_dict["match_method"] = "blocked_entity_gate"
                blocked.append(entry_dict)

    # Verdict logic — name on the gate-passing matches
    if not matches:
        verdict = "CLEAR" if exact_rows or q_entity_tokens else "INSUFFICIENT_DATA"
    else:
        # Take max score among gate-passing matches
        top = max(m["match_score"] for m in matches)
        if top >= _HARD_STOP_THRESHOLD:
            verdict = "HARD_STOP"
        else:
            verdict = "REVIEW"

    matches.sort(key=lambda m: m["match_score"], reverse=True)
    result = {
        "queried_name": queried_name,
        "queried_normalised": q_normalised,
        "queried_jurisdiction": jurisdiction,
        "queried_address": address,
        "verdict": verdict,
        "matches": matches,
        "gate_blocked": blocked,
        "cache_status": _cache_status_summary(),
    }
    # R-F1304 — wire to brain (§21a)
    try:
        from ..engine_wiring import wire_success
        wire_success(
            module="sanctions_canonical.lookup",
            summary=f"Sanctions check for {queried_name}: {verdict} ({len(matches)} matches, {len(blocked)} blocked)",
            source_id="sanctions_canonical:lookup:check_sanctions",
        )
    except Exception:
        pass
    return result


def _cache_status_summary() -> dict[str, Any]:
    """Per-source freshness — drives the verdict's confidence.

    Enumerates sources from BOTH the refresh_log (for last-refresh
    metadata) AND the entries table (so a source that was seeded
    directly without going through refresh_sanctions.py — e.g. test
    fixtures or operator-manual seed — still shows up with its
    row count)."""
    out: dict[str, dict] = {}
    # Refresh-log path (metadata-rich)
    for r in store.get_last_refresh():
        out[r["source"]] = {
            "last_refresh_at": r["finished_at"] or r["started_at"],
            "rows_loaded": r["rows_loaded"],
            "success": r["success"],
            "error": r["error"],
            "entries_in_cache": store.count_entries(r["source"]),
        }
    # Entries-table fallback (covers direct-seeded sources)
    with store.connect() as conn:
        cur = conn.cursor()
        cur.execute("SELECT DISTINCT source FROM entries")
        for (src,) in cur.fetchall():
            if src not in out:
                out[src] = {
                    "last_refresh_at": None,
                    "rows_loaded": None,
                    "success": None,
                    "error": "",
                    "entries_in_cache": store.count_entries(src),
                }
    return out


def get_cache_status() -> dict:
    """Operator-facing freshness report."""
    return {
        "total_entries": store.count_entries(),
        "per_source": _cache_status_summary(),
    }
