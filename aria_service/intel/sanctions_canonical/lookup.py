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
import os
import time
from typing import Any

from . import store
from .normalise import entity_tokens, jaccard, normalise_name

logger = logging.getLogger("aria.sanctions_canonical.lookup")

# Tunables — kept loose, the entity-overlap gate is the real safety net.
_JACCARD_FLOOR = 0.5

# R-F3691 — how many candidate rows the token pre-filter will pull per token.
# Named, and env-overridable, because the number is load-bearing: hitting it
# means the candidate set was TRUNCATED, and a truncated search may not have
# seen the designation at all. Raising it reduces how often that happens; it
# does NOT make a CLEAR safe on its own, which is why `candidate_truncated`
# forces INSUFFICIENT_DATA rather than being merely logged.
_CANDIDATE_LIMIT = max(500, int(os.getenv("ARIA_SANCTIONS_CANDIDATE_LIMIT", "5000") or 5000))
_HARD_STOP_THRESHOLD = 0.85

# R-F2373 — never-false-clean freshness gate. A store not refreshed for weeks
# (old rows persist) must NOT return an authoritative CLEAR. Env-tunable; only
# ever DOWNGRADES a would-be CLEAR, never a REVIEW/HARD_STOP.
_DEFAULT_MAX_STALENESS_DAYS = 30.0


def _max_staleness_seconds() -> float:
    """Staleness threshold in seconds (env `ARIA_SANCTIONS_MAX_STALENESS_DAYS`,
    default 30). A would-be CLEAR whose freshest successful refresh is older
    than this downgrades to INSUFFICIENT_DATA."""
    raw = (os.environ.get("ARIA_SANCTIONS_MAX_STALENESS_DAYS", "") or "").strip()
    try:
        days = float(raw) if raw else _DEFAULT_MAX_STALENESS_DAYS
    except (TypeError, ValueError):
        days = _DEFAULT_MAX_STALENESS_DAYS
    if days <= 0:
        days = _DEFAULT_MAX_STALENESS_DAYS
    return days * 86400.0


def _expected_sources() -> list[str]:
    """R-F2373 (H2) — the canonical loader registry: every source
    check_sanctions is expected to have loaded when `sources is None`.

    Derived from the loader modules' `SOURCE_ID` so it tracks the ACTUAL
    registry (ofac_sdn + eu_consolidated) rather than a hardcoded list.
    Returns [] if it cannot be determined — callers then fall back to the
    aggregate count>0 gate (never hard-fail on an undeterminable registry)."""
    srcs: list[str] = []
    try:
        from . import eu_consolidated, ofac_sdn
        for mod in (ofac_sdn, eu_consolidated):
            sid = getattr(mod, "SOURCE_ID", None)
            if isinstance(sid, str) and sid:
                srcs.append(sid)
    except Exception as e:  # pragma: no cover — defensive
        logger.debug("could not determine expected sanctions sources: %s", e)
        return []
    return srcs


def _expected_minimum(source: str) -> int:
    """R-F2570 — per-source plausibility floor for the H2 CLEAR gate, SELF-CALIBRATED from
    the source's OWN last successful refresh (refresh_log.rows_loaded). A store now holding
    fewer than DRIFT_MIN_FRACTION of its last healthy load is implausibly thin (partial
    schema drift, or a partial wipe after a good load) → treated as a coverage gap, so a
    would-be CLEAR downgrades to INSUFFICIENT_DATA rather than screen the dropped sanctioned
    entities as clean.

    Self-calibrating (no magic per-source counts) so it can't false-trip a legitimately-
    sized list and it works for direct-seeded test fixtures: no successful-refresh history
    → return 1 (i.e. the prior `<= 0` behaviour, unchanged). Fraction is the same
    ARIA_SANCTIONS_DRIFT_MIN_FRACTION the replace_source load-time floor uses (default 0.5)."""
    try:
        baseline = store.last_successful_rows_loaded(source)
    except Exception:
        baseline = 0
    if baseline <= 0:
        return 1
    try:
        frac = max(0.0, min(1.0, float(os.getenv("ARIA_SANCTIONS_DRIFT_MIN_FRACTION", "0.5"))))
    except Exception:
        frac = 0.5
    return max(1, int(baseline * frac))


def _has_refresh_metadata() -> bool:
    """True once the REAL refresh pipeline has recorded at least one refresh
    (production). Pure direct-seed stores (test fixtures / operator-manual
    seed via replace_source) carry NO refresh metadata — in that context we
    cannot reason about expected coverage or freshness, so the H1/H2 gates
    fall back rather than fabricate a signal (R-F2373; mirrors the operator's
    'unknown freshness is a soft signal' directive)."""
    try:
        return len(store.get_last_refresh()) > 0
    except Exception:
        return False


def _freshest_refresh_age_seconds(in_scope: list[str] | None) -> float | None:
    """Age (seconds) of the FRESHEST successful refresh among the in-scope
    sources. Returns None when no freshness metadata is available at all —
    unknown freshness is a SOFT signal (do not fabricate / do not hard-fail a
    would-be CLEAR on missing metadata alone)."""
    summary = _cache_status_summary()
    now = time.time()
    freshest_ts: float | None = None
    for src, meta in summary.items():
        if in_scope and src not in in_scope:
            continue
        # Only a SUCCESSFUL refresh counts as fresh. success is stored as an
        # INTEGER 1/0 in refresh_log (store.py:218), None for direct-seeded
        # sources. R-F2373 cross-check: `is False` would NOT skip a failed
        # refresh (0 is not False in Python), so a source that keeps FAILING to
        # refresh (recent failed-attempt timestamps over stale successful data)
        # would be counted as fresh → a false-clean. Skip any explicit non-
        # success (0 / False); keep None (unknown/direct-seed, whose ts is None
        # anyway) for the soft-signal path.
        _succ = meta.get("success")
        if _succ is not None and not _succ:
            continue
        ts = meta.get("last_refresh_at")
        if ts is None:
            continue
        try:
            ts = float(ts)
        except (TypeError, ValueError):
            continue
        if freshest_ts is None or ts > freshest_ts:
            freshest_ts = ts
    if freshest_ts is None:
        return None
    return max(0.0, now - freshest_ts)


def _stalest_refresh_age_seconds(in_scope: list[str] | None) -> float | None:
    """Age (seconds) of the OLDEST in-scope source. This is what the H1
    never-false-clean gate must read.

    R-F3957 (C-47) — the gate used `_freshest_refresh_age_seconds`, so the
    stalest list governed nothing: OFAC 400 days stale + EU refreshed a second
    ago gave `age = 0.0 days` and a CLEAR verdict over year-old designations.
    A screen is only as current as the LEAST current list it consulted, and the
    H2 row-count gate cannot cover for it because rows persist — a list that
    stopped updating a year ago still holds all of last year's rows.

    Per source, in order of preference:
      1. its freshest SUCCESSFUL refresh (a failed attempt is not a refresh —
         R-F2373: `is False` would not skip `success=0`, so test truthiness);
      2. failing that, the true age of its DATA rows (R-F2417), because a
         source that has only ever FAILED would otherwise contribute nothing
         and be cleared by a healthy neighbour — the same MAX-hides-the-worst
         shape one level down;
      3. failing both, the source is genuinely unknown and is skipped.

    Returns None only when NOTHING is known about any in-scope source, keeping
    R-F2373's rule that unknown freshness is a SOFT signal: missing metadata is
    not evidence of age, and direct-seeded fixture stores must not hard-fail.
    """
    summary = _cache_status_summary()
    now = time.time()
    scope = list(in_scope) if in_scope else list(summary.keys())
    oldest_age: float | None = None
    for src in scope:
        meta = summary.get(src) or {}
        age: float | None = None
        _succ = meta.get("success")
        ts = meta.get("last_refresh_at")
        if not (_succ is not None and not _succ) and ts is not None:
            try:
                age = max(0.0, now - float(ts))
            except (TypeError, ValueError):
                age = None
        if age is None:
            # No usable successful refresh for THIS source — fall back to the
            # age of the rows it actually holds.
            try:
                row_ts = store.newest_entry_refresh(src)
            except Exception:
                row_ts = None
            if row_ts is not None:
                age = max(0.0, now - float(row_ts))
        if age is None:
            continue
        if oldest_age is None or age > oldest_age:
            oldest_age = age
    return oldest_age


def _data_age_seconds(in_scope: list[str] | None) -> float | None:
    """Age (seconds) of the freshest ACTUAL data row among the in-scope sources,
    read from ``entries.last_refreshed`` via ``store.newest_entry_refresh``.

    R-F2417: fallback for the H1 staleness gate. When every in-scope source's
    LATEST refresh ATTEMPT failed, ``_freshest_refresh_age_seconds`` returns None
    (all non-success rows skipped) even though the store still holds genuinely
    stale rows — which previously fell through to CLEAR. This reports the TRUE
    data age so that outage is judged stale instead of 'freshness unknown'.
    Returns None only when there is no data at all."""
    now = time.time()
    newest: float | None = None
    scope: list[str | None] = list(in_scope) if in_scope else [None]  # None → whole store
    for src in scope:
        try:
            ts = store.newest_entry_refresh(src)
        except Exception:
            continue
        if ts is None:
            continue
        if newest is None or ts > newest:
            newest = ts
    if newest is None:
        return None
    return max(0.0, now - newest)


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
    # R-F3691 — initialised at FUNCTION scope, not inside the `with` below, so
    # a connection that raises before the candidate pass cannot leave this name
    # unbound at the verdict gate (NameError inside a never-false-clean check
    # would be the worst possible failure mode for it).
    candidate_truncated = False

    with store.connect() as conn:
        cur = conn.cursor()
        source_filter = set(sources or [])

        # First pass — exact normalised-name match on the primary or any alias.
        cur.execute(
            """
            SELECT e.id, e.source, e.source_uid, e.formatted_name,
                   e.normalised_name, e.entity_type, e.countries,
                   e.addresses, e.aliases, e.programs, e.raw_excerpt
            FROM entries e
            WHERE (e.normalised_name = ? OR e.id IN (
                SELECT entry_id FROM aliases WHERE normalised = ?
            ))
            """,
            [q_normalised, q_normalised],
        )
        exact_rows = [
            r for r in cur.fetchall()
            if not source_filter or r[1] in source_filter
        ]
        seen_ids = {r[0] for r in exact_rows}

        # Second pass — token-overlap candidates (cheap pre-filter via
        # any-shared-token via aliases). We fetch all rows with at
        # least one shared word in the normalised name; jaccard-score
        # them in Python.
        # ── R-F3691 — a truncated candidate set cannot produce a CLEAR ───────
        #
        # THE DEFECT, reproduced by execution: this pre-filter is
        # `LIKE '%token%' ... LIMIT 500` with NO `ORDER BY`, so SQLite returns
        # rows in rowid (insertion) order and the first 500 INSERTED rows win.
        # `%token%` has a leading wildcard, so a common name particle ("ali",
        # "mohammed", "hassan", "al") matches thousands of rows in a ~25k-row
        # store. Same store contents, only decoy volume changed:
        #     400 decoys -> REVIEW, 1 match (the designated entity)
        #     600 decoys -> CLEAR,  0 matches, gate_blocked 0
        # The designation was silently outside the window and the answer was an
        # authoritative CLEAR — not INSUFFICIENT_DATA, not gate_blocked.
        #
        # This is the worst available shape: the never-false-clean gates below
        # are downstream of a truncation that removes the evidence BEFORE they
        # can see it, so they cannot fire. It bites hardest on Arabic and
        # Slavic name populations, i.e. most of the SDN list.
        #
        # Raising the cap alone would be a band-aid (§ROOT CAUSE): a bigger
        # window still truncates silently at some store size. The honest fix is
        # to DETECT truncation and refuse to certify — a screen that could not
        # enumerate its own candidates has not searched.
        if q_entity_tokens:
            for token in q_entity_tokens:
                cur.execute(
                    """
                SELECT e.id, e.source, e.source_uid, e.formatted_name,
                       e.normalised_name, e.entity_type, e.countries,
                       e.addresses, e.aliases, e.programs, e.raw_excerpt
                FROM entries e
                WHERE e.id IN (
                    SELECT entry_id FROM aliases
                    WHERE normalised LIKE ?
                )
                LIMIT ?
                    """,
                    [f"%{token}%", _CANDIDATE_LIMIT],
                )
                _fetched = cur.fetchall()
                if len(_fetched) >= _CANDIDATE_LIMIT:
                    # Hit the cap ⇒ there may be candidates we never looked at.
                    candidate_truncated = True
                for r in _fetched:
                    if r[0] in seen_ids:
                        continue
                    if source_filter and r[1] not in source_filter:
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

            # ── R-F3691 — score with CONTAINMENT as well as Jaccard ─────────
            #
            # Jaccard is symmetric, but the relationship here is not: a SHORT
            # query against a LONG listed name is penalised by every token the
            # listing adds. Reproduced against a store holding
            #   'Rosoboronexport Federal State Unitary Enterprise Defence
            #    Export Agency'
            #     'Rosoboronexport'     -> jaccard 0.143 -> CLEAR, gate_blocked 0
            #     'Rosoboronexport Ltd' -> jaccard 0.143 -> CLEAR
            # i.e. the exact brand token of a designated entity screened CLEAN,
            # and did not even surface for audit. The §18 live probe passed only
            # because the real store happens to also hold the short alias
            # 'JSC ROSOBORONEXPORT', which the exact-alias pass catches.
            #
            # Containment (|q ∩ c| / |q|) answers the question that actually
            # matters — "is the query fully present in the listed name?" — and
            # letting it survive to the R-F518 gate does NOT weaken precision:
            # that gate is the component designed to reject coincidences, and
            # it still runs on everything admitted here.
            _containment = (
                len(q_entity_tokens & cand_entity_tokens) / len(q_entity_tokens)
                if q_entity_tokens else 0.0
            )
            if _containment > best_score:
                best_score = _containment
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
    # R-F2159: an empty match set is CLEAR *only* when the canonical store
    # actually HOLDS loaded sanctions data for the in-scope sources. Before,
    # `q_entity_tokens` (non-empty for essentially every real name) alone made
    # the verdict CLEAR — so an EMPTY/un-refreshed store returned an
    # authoritative "clean" for any company. That is a sanctions false-negative
    # (the single worst output a compliance tool can emit). Gate CLEAR on store
    # readiness; an unloaded store → INSUFFICIENT_DATA + source_unavailable so
    # callers render UNVERIFIED, never "clean".
    store_unavailable = False
    coverage_gap: list[str] = []
    reason: str | None = None
    freshness_age_days: float | None = None
    if not matches:
        try:
            if sources:
                _loaded = sum(store.count_entries(s) for s in sources)
            else:
                _loaded = store.count_entries()
        except Exception:
            _loaded = 0  # cannot prove the store has data → treat as unavailable
        if _loaded <= 0:
            verdict = "INSUFFICIENT_DATA"
            store_unavailable = True
            reason = "sanctions_store_empty_or_unavailable"
        elif blocked:
            # R-F3691 — a GATE-BLOCKED near-miss is not a clearance.
            #
            # Surfaced by this change's own capability test: with containment
            # scoring (above), 'Rosoboronexport' now correctly reaches the
            # R-F518 gate against the long listed name — and the gate blocks it,
            # because a single-token overlap needs jurisdiction or address
            # corroboration that a bare name query does not carry. The candidate
            # landed in `gate_blocked` and the verdict was STILL "CLEAR", so the
            # near-miss was recorded for audit and contradicted by the headline.
            #
            # The R-F518 gate exists to stop false HITS, not to manufacture
            # cleans. "We found a name-overlapping designation but could not
            # corroborate it" is the textbook REVIEW case: a human decides.
            #
            # Note this only fires when there are NO real matches — a genuine
            # HARD_STOP/REVIEW is computed in the else branch and is untouched.
            # And an unrelated name never gets here: it falls below the score
            # floor and is dropped before the gate, so it is not in `blocked`.
            verdict = "REVIEW"
            reason = "gate_blocked_near_miss"
        elif candidate_truncated:
            # R-F3691 — the candidate pre-filter hit its cap, so rows exist that
            # were never scored. A no-match result here means "we did not look
            # at everything", which is INSUFFICIENT_DATA, never CLEAR. Ordered
            # before the coverage/staleness gates because it is a stronger
            # statement: those ask whether the DATA is adequate, this asks
            # whether the SEARCH completed.
            verdict = "INSUFFICIENT_DATA"
            reason = "sanctions_candidate_truncation"
        elif exact_rows or q_entity_tokens:
            # A would-be CLEAR. Before returning it, apply two R-F2373
            # never-false-clean gates that only ever DOWNGRADE a clean (they
            # never touch a REVIEW/HARD_STOP, which live in the else branch):
            #   H2 partial-coverage — every EXPECTED in-scope source must hold
            #       rows, else an EU-only entity screens CLEAR against an
            #       OFAC-only store (EU loader failed/empty).
            #   H1 staleness — the freshest SUCCESSFUL refresh of the in-scope
            #       sources must be within ARIA_SANCTIONS_MAX_STALENESS_DAYS.
            expected = _expected_sources()
            in_scope = list(sources) if sources else (expected or None)
            # H2 is enforced only once the real refresh pipeline has run at
            # least once (production). Pure direct-seed stores carry no refresh
            # metadata → we cannot reason about expected coverage → fall back to
            # the aggregate count>0 gate above (never hard-fail a seeded store).
            if expected and _has_refresh_metadata():
                check_set = list(sources) if sources else expected
                for src in check_set:
                    if src not in expected:
                        continue  # unknown/non-registry source — do not enforce
                    try:
                        # R-F2570 — plausibility floor (was `<= 0`): a source present but
                        # implausibly THIN (partial schema drift) must not yield CLEAR.
                        if store.count_entries(src) < _expected_minimum(src):
                            coverage_gap.append(src)
                    except Exception:
                        coverage_gap.append(src)
            if coverage_gap:
                verdict = "INSUFFICIENT_DATA"
                store_unavailable = True
                reason = "sanctions_partial_coverage"
            else:
                # H1 — unknown freshness (None) is a SOFT signal: do NOT
                # hard-fail CLEAR on missing metadata alone (keeps direct-seeded
                # fixtures working). Downgrade only when we KNOW the freshest
                # successful refresh is older than the threshold.
                # R-F3957 (C-47) — the OLDEST in-scope source, not the freshest.
                # A screen is only as current as the least current list it
                # consulted; reading the freshest let one healthy list clear a
                # 400-day-stale neighbour. `_stalest_refresh_age_seconds` also
                # absorbs R-F2417's per-source data-age fallback, so a source
                # whose every refresh ATTEMPT failed contributes its true row
                # age instead of dropping out of the aggregate entirely.
                age = _stalest_refresh_age_seconds(in_scope)
                # Whole-store fallback for the case where not one in-scope
                # source is known: gated on _has_refresh_metadata() so pure
                # direct-seed stores (fixtures, no refresh_log) keep the
                # soft/unknown path and are never hard-failed on absence.
                if age is None and _has_refresh_metadata():
                    age = _data_age_seconds(in_scope)
                if age is not None and age > _max_staleness_seconds():
                    verdict = "INSUFFICIENT_DATA"
                    store_unavailable = True
                    reason = "sanctions_data_stale"
                    freshness_age_days = round(age / 86400.0, 1)
                else:
                    verdict = "CLEAR"
                    # R-F3957 — a CLEAR must state how current it is. Reporting
                    # the age only on the FAILING branch meant a screen against
                    # 29-day-old data and one against one-hour-old data rendered
                    # identically to the reader.
                    if age is not None:
                        freshness_age_days = round(age / 86400.0, 1)
        else:
            verdict = "INSUFFICIENT_DATA"
            store_unavailable = True
            reason = "sanctions_store_empty_or_unavailable"
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
        # R-F2159: explicit signal that the screen could NOT run against loaded
        # data (empty/unavailable store). Mirrors the sanctions_claim_guard
        # vocabulary so every caller can render COULD_NOT_VERIFY, not "clean".
        "source_unavailable": store_unavailable,
    }
    # R-F2373 — surface WHY the screen could not clear so callers render the
    # right UNVERIFIED reason (stale vs partial-coverage vs empty store).
    if reason:
        result["reason"] = reason
    if coverage_gap:
        result["coverage_gap"] = coverage_gap
    if freshness_age_days is not None:
        result["freshness_age_days"] = freshness_age_days
        result["max_staleness_days"] = round(_max_staleness_seconds() / 86400.0, 1)
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
