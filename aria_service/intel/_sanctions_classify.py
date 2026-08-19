"""Shared sanctions-match classifier used by dd_orchestrator and
network_walker.

A score-only classifier is wrong for defence DD: legitimate primes
(BAE Systems, Thales, Lockheed Martin, Rolls-Royce) routinely hit
OpenSanctions at score 1.00 against transparency / state-ownership
data (topics: corp.state, corp.public, gov.soe), which is NOT a
refusal ground. The CORRECT discipline is to look at the OpenSanctions
topic labels on each match and classify by the meaning of the list,
not by the string-match confidence.

Topic taxonomy: https://www.opensanctions.org/reference/#topics

Usage:
    from ._sanctions_classify import classify_matches
    result = classify_matches(matches)  # matches = list[dict] from sanctions.py
    # result = {"worst_severity": "amber", "summary": "...", "per_match": [...]}
"""
from __future__ import annotations
from .engine_wiring import wire_failure


# Topic → severity. Anything NOT in this map defaults to "info".
_TOPIC_SEVERITY: dict[str, str] = {
    # ── HARD STOP ── active sanction / asset freeze / export prohibition
    "sanction":         "hard_stop",
    "sanction.linked":  "hard_stop",
    # R-F3053 (2026-07-25) — COUNTER-sanctions are NOT a Western legal bar, and
    # treating them as one produces a false mandatory refusal against exactly the
    # people this product exists to serve.
    #
    # Live evidence: a DD on **Charles Woodburn — the CEO of BAE Systems** returned
    # `HARD STOP — triggers a mandatory refusal. Do NOT proceed with the
    # transaction.` The underlying fact is TRUE and correctly matched (OpenSanctions
    # Q32159211, lists `wikidata, cn_sanctions`, topic `sanction.counter`): China
    # counter-sanctioned him. But a Chinese counter-designation of a UK defence
    # executive creates NO prohibition for a UK/EU/US counterparty — it is imposed
    # in retaliation for legitimate Western defence work, so for our users it is
    # closer to the opposite signal.
    #
    # This is the same correction R-F569 made for `export.risk` (Embraer HARD_STOP'd
    # at an MVP fire-test), and the same principle as R-F3000 (UK sovereign debt
    # demoted from AMBER to country-context INFO): the FACT is reported, the
    # unjustified BLOCKING VERDICT is not.
    #
    # NEVER-FALSE-CLEAN IS PRESERVED: this only reclassifies the counter-sanctions
    # topic. A genuine OFAC/EU/UN/OFSI listing on the same entity carries the
    # `sanction` topic above and still HARD_STOPs, and the counter-sanction itself
    # remains visible at AMBER with an explanation — it is material to the
    # counterparty's banking, travel and ability to operate in that jurisdiction.
    "sanction.counter": "amber",
    "asset.frozen":     "hard_stop",
    "frozen":           "hard_stop",
    "export.control":   "hard_stop",
    # R-F569 (2026-05-16) — `export.risk` is a watchlist-grade topic carried
    # by lists like UANI's `ir_uani_business_registry` (companies still
    # trading with Iran) and similar export-risk monitors. These are NOT
    # legal sanctions — they're due-diligence pointers. Live evidence:
    # Embraer S.A. HARD_STOP'd at MVP fire-test 2026-05-16 because UANI
    # had it tagged `export.risk` (correct signal — Embraer historically
    # delivered E-Jets to Iran) but ARIA's classifier was treating that
    # as equivalent to OFAC SDN. Demoted to AMBER so the operator sees
    # the flag, runs enhanced DD, but isn't auto-blocked from transacting
    # with a publicly-listed clean entity. Real sanctions on the same
    # entity (if any) still surface via the OFAC SDN / UN SC / EU FSF
    # lists which carry the `sanction` topic and remain HARD_STOP.
    "export.risk":      "amber",
    # ── HARD STOP ── ICC warrants / Interpol Red Notices (explicit routing)
    # 2026-04-12: was implicit via "wanted" topic. Now explicit so the DD
    # report can show "ICC warrant" vs generic "wanted" in the finding text.
    "icc":              "hard_stop",
    "icc.wanted":       "hard_stop",
    "interpol":         "red",
    "interpol.red":     "hard_stop",
    # ── RED ── crime / debarment / regulatory action — human review required
    "debarment":        "red",
    "crime":            "red",
    "crime.fin":        "red",
    "crime.fraud":      "red",
    "crime.theft":      "red",
    "crime.war":        "red",
    "crime.terror":     "red",
    "crime.traffick":   "red",
    "crime.cyber":      "red",
    "crime.env":        "red",
    "crime.boss":       "red",
    "crime.org":        "red",
    "wanted":           "red",
    "reg.action":       "red",
    # ── AMBER ── PEP / adverse media / disqualified director — enhanced DD
    "role.pep":         "amber",
    "role.pol":         "amber",
    "role.rca":         "amber",
    "role.judge":       "amber",
    "corp.disqual":     "amber",
    "reg.warn":         "amber",
    # ── INFO ── transparency / state-ownership / regulated entity — not a risk
    "corp.public":      "info",
    "corp.state":       "info",
    "corp.offshore":    "info",
    "role.civil":       "info",
    "role.diplo":       "info",
    "role.acting":      "info",
    "fin":              "info",
    "fin.bank":         "info",
    "fin.fund":         "info",
    "fin.adivsor":      "info",
    "gov":              "info",
    "gov.national":     "info",
    "gov.state":        "info",
    "gov.muni":         "info",
    "gov.soe":          "info",
    "gov.igo":          "info",
    "mil":              "info",
    "poi":              "info",
}

SEVERITY_RANK = {"info": 0, "amber": 1, "red": 2, "hard_stop": 3}

# R-F55 (2026-05-09): defence-DD-relevant restricted-entity lists. Keys
# are OpenSanctions dataset slug substrings (lowercased contains-match);
# values are (severity, human_label). When a match's `lists` / `datasets`
# array contains any of these, the match is escalated to the listed
# severity AND the per-match output carries the friendly label. This
# turns opaque slugs like `us_dod_chinese_military_companies` into
# "NDAA Sec 1260H Chinese Military Companies" in the DD report — what
# a compliance officer actually wants to see.
#
# Lists chosen for the substring-match are the load-bearing ones for
# defence-DD work: 1260H + 1233 + CMIC variants + BIS Entity List + EU
# restrictive measures + UK FCDO. Other OpenSanctions datasets fall
# through to topic-based classification unchanged.
_DEFENCE_LIST_LABELS: dict[str, tuple[str, str]] = {
    # US DoD restricted-entity lists
    "1260h":             ("hard_stop", "NDAA Sec 1260H — Chinese Military Companies (DoD)"),
    "chinese_military":  ("hard_stop", "DoD Chinese Military Companies List"),
    "1233":              ("hard_stop", "Sec 1233 — Russian Defence Companies (DoD)"),
    "russian_defence":   ("hard_stop", "Russian Defence Companies (DoD)"),
    # OFAC / Treasury Chinese / NS-CMIC
    "cmic":              ("hard_stop", "Chinese Military-Industrial Complex (NS-CMIC)"),
    "ns_cmic":           ("hard_stop", "Non-SDN Chinese Military-Industrial Complex (NS-CMIC)"),
    # BIS Entity List — high-impact for export controls
    "bis_entity":        ("hard_stop", "BIS Entity List (US Commerce)"),
    "us_trade":          ("hard_stop", "BIS / US Trade Sanctions"),
    "us_bis":            ("hard_stop", "BIS (US Commerce)"),
    # OFAC SSI / unverified list
    "us_ssi":            ("hard_stop", "OFAC Sectoral Sanctions Identifications"),
    "us_unverified":     ("red", "BIS Unverified List"),
    "us_mil_end_user":   ("hard_stop", "BIS Military End User List"),
    # EU + UK
    "eu_fsf":            ("hard_stop", "EU Financial Sanctions File"),
    "eu_council":        ("hard_stop", "EU Council Restrictive Measures"),
    "gb_hmt":            ("hard_stop", "HM Treasury Sanctions"),
    "gb_fcdo":           ("hard_stop", "UK FCDO Sanctions List"),
    "ofsi":              ("hard_stop", "OFSI / FCDO UK Sanctions"),
    # UN
    "un_sc_sanctions":   ("hard_stop", "UN Security Council Sanctions"),
    "un_consolidated":   ("hard_stop", "UN Consolidated Sanctions"),
}


# R-F287 (2026-05-11) — Canonical sanctions sources that EVERY DD search
# must report per-source status for. Operator mandate: "ensure all the
# sanctions sources are always verified on any DD search — not fabricate".
# The pre-R-F287 chat-output was fabricating "UK OFSI: NOT CHECKED"
# claims even when OpenSanctions had successfully queried that exact
# dataset. The fix is structural: derive_verified_sources() returns an
# explicit dict mapping each canonical source to {status, match_count,
# matched_entity, dataset_slug} so the renderer NEVER has to invent a
# per-source verdict.
#
# Each entry: (display_label, [opensanctions_slug_substrings_to_match]).
# Substrings are lowercase contains-match against the `lists`/`datasets`
# field on each OpenSanctions match.
_CANONICAL_SANCTIONS_SOURCES: dict[str, tuple[str, list[str]]] = {
    "OFAC SDN": (
        "US Treasury — Office of Foreign Assets Control · SDN List",
        ["us_ofac_sdn", "ofac_sdn", "us_sdn"],
    ),
    "OFAC NS-CMIC": (
        "US Treasury — Non-SDN Chinese Military-Industrial Complex",
        ["ns_cmic", "us_cmic", "us_nscmic"],
    ),
    "OFAC SSI": (
        "US Treasury — Sectoral Sanctions Identifications",
        ["us_ssi", "ofac_ssi"],
    ),
    "BIS Entity List": (
        "US Commerce — Bureau of Industry and Security Entity List",
        ["bis_entity", "us_bis_entity", "us_bis"],
    ),
    "BIS Military End User": (
        "US Commerce — Military End User List",
        ["us_mil_end_user", "us_meu"],
    ),
    "UK OFSI / HMT": (
        "HM Treasury Office of Financial Sanctions Implementation",
        ["gb_hmt", "ofsi", "gb_fcdo"],
    ),
    "EU Consolidated": (
        "EU Financial Sanctions Database / Council Restrictive Measures",
        ["eu_fsf", "eu_council", "eu_consolidated"],
    ),
    "UN SC Consolidated": (
        "UN Security Council Consolidated Sanctions List",
        ["un_sc_sanctions", "un_consolidated", "un_sc"],
    ),
    "NDAA Sec 1260H": (
        "DoD — Chinese Military Companies List (FY21 NDAA §1260H)",
        ["1260h", "chinese_military"],
    ),
    "DoD Sec 1233 Russia": (
        "DoD — Russian Defence Companies List (Sec 1233)",
        ["1233", "russian_defence"],
    ),
}


def _coverage_split(screen: dict | None) -> tuple[set, set]:
    """R-F3945 — (unavailable, covered_by_local) canonical source names.

    THE ONE computation. `unavailable_sources_for()` and
    `locally_covered_sources_for()` are thin readers of this, so the three
    dd_orchestrator call sites cannot drift apart the way the two phase-gate
    aggregators did (CLAUDE.md §1, R-F2639: "there is ONE measure now").

    Reads `screen["coverage"]` written by `sanctions.fuzzy_screen`. Mapping is
    by the EXISTING `_CANONICAL_SANCTIONS_SOURCES` slug table — `ofac_sdn` and
    `eu_consolidated` are already listed there — so this adds no second list to
    keep in step with the first.

    ABSENCE RULES, and they are the load-bearing part:
      * no `coverage` key  → a legacy/full-aggregate result. Returns EMPTY, i.e.
        the historical meaning. This fix must not retroactively rewrite the
        reading of results produced before it shipped.
      * mode `local_canonical_floor` with an EMPTY consulted list → the loader
        registry could not be determined, so NOTHING is known to be covered and
        everything is unavailable. Fails CLOSED: an undeterminable registry is
        never full coverage.
    """
    cov = (screen or {}).get("coverage")
    if not isinstance(cov, dict):
        return set(), set()                     # legacy shape — unchanged meaning
    mode = str(cov.get("mode") or "")
    if mode == "opensanctions_aggregate":
        return set(), set()                     # the aggregate covers all of them
    if mode not in ("local_canonical_floor", "none"):
        return set(), set()                     # unknown mode — do not invent a claim
    consulted = {str(s).strip().lower() for s in (cov.get("sources_consulted") or [])}
    unavailable: set = set()
    local: set = set()
    for src_name, (_label, slugs) in _CANONICAL_SANCTIONS_SOURCES.items():
        if consulted and any(slug in consulted for slug in slugs):
            local.add(src_name)
        else:
            unavailable.add(src_name)
    return unavailable, local


def unavailable_sources_for(screen: dict | None) -> set:
    """Canonical sources this screen did NOT search. Feed to derive_verified_sources."""
    return _coverage_split(screen)[0]


def locally_covered_sources_for(screen: dict | None) -> set:
    """Canonical sources cleared by the LOCAL store rather than the aggregate."""
    return _coverage_split(screen)[1]


def derive_verified_sources(
    matches: list[dict],
    *,
    screen_succeeded: bool = True,
    unavailable_sources: set | frozenset | None = None,
    covered_by_local: set | frozenset | None = None,
    screen: dict | None = None,
) -> dict[str, dict]:
    """Per-canonical-source verification status for the DD report.

    R-F287 (2026-05-11) — the chat-output layer was fabricating per-source
    "NOT CHECKED" claims (e.g., "UK OFSI: NOT CHECKED — list unavailable")
    on sources that OpenSanctions HAD queried but returned no match for.
    OpenSanctions is an aggregator: a clean response means "all underlying
    sources queried, none hit", NOT "those sources weren't checked".

    Args:
        matches: The raw matches list from sanctions screening
            (e.g., result["matches"] from fuzzy_screen()).
        screen_succeeded: False ONLY when the entire OpenSanctions call
            failed (network error, 401, 429). When True (the normal case),
            every canonical source is reported as either HIT or CLEAN.

    Returns:
        {
          "OFAC SDN": {
            "label": "US Treasury — Office of Foreign Assets Control · SDN List",
            "status": "CLEAN" | "HIT" | "UNAVAILABLE",
            "match_count": int,
            "matched_entities": [str, ...],  # candidate names that hit this list
          },
          ...
        }
    """
    # R-F3945 — ONE argument, not two. Pass the fuzzy_screen result as `screen`
    # and both halves of the coverage split are derived here. Requiring callers
    # to remember two co-dependent sets is how the next call site ends up
    # passing one of them: the failure mode would be a CLEAN row attributed to
    # an aggregate that never ran — quieter than the bug this fixes, and the
    # same class. Explicit sets still win, for callers that already have them.
    if screen is not None:
        _u, _l = _coverage_split(screen)
        if unavailable_sources is None:
            unavailable_sources = _u
        if covered_by_local is None:
            covered_by_local = _l

    out: dict[str, dict] = {}
    if not screen_succeeded:
        # Whole screen failed → every canonical source is unavailable
        for src_name, (label, _slugs) in _CANONICAL_SANCTIONS_SOURCES.items():
            out[src_name] = {
                "label": label,
                "status": "UNAVAILABLE",
                # R-F2843 — say WHY, so UNAVAILABLE is not mistaken for "we chose
                # not to check". The aggregate call itself did not answer.
                "via": "screen_failed",
                "match_count": 0,
                "matched_entities": [],
            }
        return out

    # Build a flat list of (lowercased dataset slug, match_dict) pairs
    matches_safe = matches or []
    for src_name, (label, slugs) in _CANONICAL_SANCTIONS_SOURCES.items():
        # R-F2840 — a source we could not reach is UNAVAILABLE: neither HIT nor CLEAN.
        # Rendering it CLEAN is a false clean by omission, and the grounded-rate
        # counter in dd_orchestrator already excludes UNAVAILABLE deliberately.
        if unavailable_sources and src_name in unavailable_sources:
            out[src_name] = {"label": label, "status": "UNAVAILABLE",
                             "via": "source_unavailable",
                             "match_count": 0, "matched_entities": []}
            continue
        # R-F3945 — attribute a CLEAN to the screen that actually produced it.
        # When the R-F3529 local floor served, "via: opensanctions_aggregate"
        # named a source that had REFUSED us, which is why the over-claim was
        # unauditable from the report alone.
        _via = ("local_canonical" if src_name in (covered_by_local or ())
                else "opensanctions_aggregate")
        hit_count = 0
        hit_entities: list[str] = []
        for m in matches_safe:
            if not isinstance(m, dict):
                continue
            # R-F2840 — list membership alone is NOT a hit. The live report asserted
            # "OFAC SDN: HIT" for SOCAR Trading SA on two acronym collisions
            # (similarity 0.235 and 0.455) because this loop counted any match
            # carrying the slug.
            if not is_corroborated_match(m):
                continue
            datasets = m.get("lists") or m.get("datasets") or []
            ds_lower = " ".join(str(d).lower() for d in datasets)
            for slug in slugs:
                if slug in ds_lower:
                    hit_count += 1
                    cand = m.get("name") or m.get("caption") or ""
                    if cand and cand not in hit_entities:
                        hit_entities.append(cand)
                    break  # one slug-hit per match per source is enough
        out[src_name] = {
            "label": label,
            "status": "HIT" if hit_count > 0 else "CLEAN",
            # R-F2843 — a bare CLEAN is not decision-grade. State the screen that
            # produced it, so "OFAC SDN: CLEAN" is readable as "the OpenSanctions
            # aggregate queried its OFAC dataset and found nothing" rather than an
            # unattributed pass (the competitor's "Score 0" shape).
            "via": _via,      # R-F3945 — names what actually ran
            "match_count": hit_count,
            "matched_entities": hit_entities[:5],  # cap renderer output
        }
    return out



# ── R-F2840 — THE CORROBORATION PREDICATE ────────────────────────────────────
#
# ONE rule, imported by every module that renders a BLOCKING verdict. Two existed
# before and they drifted, because each read a different field off the same match:
#   sanctions.py            blocked   <- m["score"] >= threshold        (score only)
#   _sanctions_classify.py  HIT       <- slug in m["lists"]             (no similarity)
# Neither consulted string_similarity, topics, or matched_via_variant — all of which
# were present on the match the whole time.
#
# Live consequence (run dd_06bdbcaaa866, SOCAR Trading SA): 8 matches, 5 of them
# collisions on the ACRONYM "STS" derived from the query, one at string similarity
# 0.000 scoring 0.74. The report rendered `blocked: True` and
# "US Treasury OFAC SDN: HIT" — while identity.data_gaps recorded that the OFAC SDN
# list was UNAVAILABLE that run. Matches both name-similar AND sanctioned: ZERO.
#
# THE TWO CONDITIONS, and why each is necessary:
#   1. string_similarity >= _MIN_BLOCK_SIMILARITY — the name must actually match.
#      A high `score` can come from an exact hit on a two-letter ALIAS; similarity is
#      what says the SUBJECT matched. 0.50 is not a new number: it is the floor the
#      acronym-collision guard in this module already uses.
#   2. a sanction/debarment TOPIC — an ownership or corporate-registry dataset is
#      evidence about the entity, not a designation against it. The SOCAR identity hit
#      (similarity 1.0, dataset gem_energy_ownership, topics []) is real and useful
#      ownership evidence, and must NOT be rendered as a sanctions block.
#
# SAFETY — this must never manufacture a clean. Failing corroboration removes a match
# from the BLOCKING set only; it stays in `matches`, stays counted, and neither
# `screened` nor source-availability semantics change. blocked=False is not "clear".
_MIN_BLOCK_SIMILARITY = 0.50
_BLOCKING_TOPICS = frozenset({"sanction", "debarment"})


def is_corroborated_match(match: dict, *, min_similarity: float = _MIN_BLOCK_SIMILARITY) -> bool:
    """True iff `match` may drive a BLOCKING verdict.

    Deliberately strict and deliberately shared. A match that fails this is still
    reported — as a related-name observation, not as a designation.
    """
    if not isinstance(match, dict):
        return False
    # R-F2840 — ABSENT similarity is not LOW similarity. Coercing a missing field to
    # 0.0 dropped a genuine OFAC SDN designation ({"name":"Rosoboronexport JSC",
    # "score":0.95,"lists":["us_ofac_sdn"],"topics":["sanction"]} carries no
    # string_similarity) — i.e. it manufactured a FALSE CLEAN, which is strictly
    # worse than the false positive being fixed.
    #
    # So: only a MEASURED-low similarity may exclude a match. If we cannot measure
    # it, we cannot disprove the match, and the honest response is to let it stand.
    # This is the same discipline as `pass=None` on the Phase A gates: "could not
    # measure" is never "measured and clear". The real-world acronym collisions this
    # R-number targets all DO carry a similarity (0.000-0.455), so the fix still bites.
    raw_sim = match.get("string_similarity")
    if raw_sim is not None:
        try:
            if float(raw_sim) < min_similarity:
                return False
        except (TypeError, ValueError):
            pass  # unparseable → treat as unmeasurable, fall through
    # A designation is signalled by EITHER an explicit sanction/debarment topic OR
    # membership of a canonical sanctions list.
    #
    # R-F2840 follow-up: requiring the TOPIC alone was wrong and introduced a false
    # clean — caught by test_rf1696_sanctions_source_unavailable's real-hit fixture,
    # an exact-name OFAC SDN match ({"score":0.96,"caption":"Test Entity",
    # "datasets":["us_ofac_sdn"]}) that carries NO `topics` field at all.
    # OpenSanctions populates topics inconsistently, so it cannot be mandatory. The
    # LIST is the authoritative signal; the topic is a useful alternative for feeds
    # that carry it. Getting this wrong in the strict direction is worse than the
    # bug being fixed: a missed designation is a false clean.
    topics = match.get("topics") or []
    if any(str(t).lower() in _BLOCKING_TOPICS for t in topics):
        return True
    datasets = " ".join(str(d).lower() for d in (match.get("lists") or match.get("datasets") or []))
    for _label, _slugs in _CANONICAL_SANCTIONS_SOURCES.values():
        if any(slug in datasets for slug in _slugs):
            return True
    return False



# R-F2843 — direct-adapter slug -> canonical verified_sources key.
# ARIA runs SOME primary sources itself (ofac_sdn.py, fcdo_sanctions.py,
# un_sc_sanctions.py) IN ADDITION to the OpenSanctions aggregate. Those are
# different checks and can disagree about availability, which is exactly what
# happened on run dd_06bdbcaaa866: the aggregate cleared OFAC while our own OFAC
# snapshot failed to load.
_PRIMARY_ADAPTER_TO_SOURCE = {
    "ofac_sdn": "OFAC SDN",
    "uk_ofsi": "UK OFSI / HMT",
    "un_sc": "UN SC Consolidated",
}


def detect_screen_contradictions(findings, verified_sources) -> list[dict]:
    """Findings that assert a HIT on a list the screen record reports CLEAN.

    MEASURED — delivered run dd_29368fbb8b3d (2026-08-03). The report carried, in
    one document:

        Key findings : OFAC SDN match: D.G.D. INVESTMENTS LTD.  → HARD_STOP
        Compliance   : US Treasury — OFAC · SDN List — CLEAN

    Not a rendering bug. R-F3219 re-screens the REGISTERED legal name once
    Companies House resolves it, and overwrites `screen["verified_sources"]` with
    the re-screen's result (dd_orchestrator ~3328). The re-screen was clean, so
    the TABLE became clean — while the findings raised by the FIRST screen (on
    the customer-supplied name) stayed exactly where they were. The table
    describes screen #2, the findings describe screen #1, and nothing reconciles
    them. The report even states the re-screen found no matches, three lines from
    the HARD_STOP it contradicts.

    This DETECTS; it does not adjudicate. A sanctions HARD_STOP is never silently
    withdrawn on the strength of a second screen — the first screen may be the
    correct one, and suppressing it would be the more dangerous failure. What it
    ends is the report shipping both claims without saying they disagree.

    Args:
        findings: any iterable of Finding-likes (objects or dicts) carrying
            `severity` and `source`.
        verified_sources: the derive_verified_sources() dict,
            {canonical_name: {"status": "CLEAN"|"HIT"|"UNAVAILABLE", ...}}.

    Returns:
        [{"list": canonical, "finding": <detail>, "severity": <sev>}, ...]
    """
    if not isinstance(verified_sources, dict):
        return []

    clean = {
        str(name).strip().casefold()
        for name, rec in verified_sources.items()
        if isinstance(rec, dict) and str(rec.get("status", "")).upper() == "CLEAN"
    }
    if not clean:
        return []

    out: list[dict] = []
    for f in (findings or []):
        get = (lambda k: f.get(k)) if isinstance(f, dict) else (lambda k: getattr(f, k, None))
        sev = str(get("severity") or "").strip().lower()
        if sev not in ("hard_stop", "red"):
            continue
        source = str(get("source") or "")
        detail = str(get("detail") or get("title") or "")
        # Match the finding's source/detail against each list reported CLEAN.
        # Compared on tokens rather than substrings so "OFAC SDN" matches
        # "sources.ofac_sdn" without "sdn" alone matching an unrelated list.
        haystack = f"{source} {detail}".casefold()
        for canonical in clean:
            tokens = [t for t in _re_split_tokens(canonical) if len(t) >= 3]
            if tokens and all(t in haystack for t in tokens):
                out.append({
                    "list": canonical,
                    "finding": detail[:300],
                    "severity": sev,
                })
                break
    return out


def _re_split_tokens(text: str) -> list[str]:
    """Lowercase alphanumeric tokens of a canonical list name."""
    import re as _re
    return [t for t in _re.split(r"[^a-z0-9]+", str(text).casefold()) if t]


def annotate_primary_snapshots(verified_sources, snapshots) -> None:
    """Record whether ARIA's OWN primary snapshot for each list was live.

    R-F2843 — provenance only. This NEVER changes a status.

    Flipping a source to UNAVAILABLE because our separate adapter failed would
    reverse R-F287, which deliberately stopped the renderer fabricating "NOT
    CHECKED" on sources the aggregate HAD queried. Both facts are true and both
    are now visible: `status` says what the screen found, `via` says which screen
    found it, `primary_snapshot` says whether our own copy of that list was live.

    ABSENCE IS NOT AVAILABILITY. A source is stamped only when its adapter result
    is genuinely known; an unstamped source asserts nothing. Defaulting to "ok"
    would be a confident claim about a check we never observed.

    Mutates in place. Never raises — a provenance annotation must not be able to
    fail a report mid-render.
    """
    if not isinstance(verified_sources, dict) or not isinstance(snapshots, dict):
        return
    for slug, state in snapshots.items():
        key = _PRIMARY_ADAPTER_TO_SOURCE.get(str(slug).lower())
        if not key:
            continue  # unknown/new adapter: assert nothing rather than guess
        entry = verified_sources.get(key)
        if isinstance(entry, dict):
            entry["primary_snapshot"] = str(state)


def _defence_list_hits(datasets: list) -> list[tuple[str, str, str]]:
    """Return list of (slug, severity, label) tuples for defence-relevant
    list matches in `datasets`. Each unique label is reported once."""
    if not datasets:
        return []
    seen_labels: set[str] = set()
    hits: list[tuple[str, str, str]] = []
    for ds in datasets:
        ds_lower = str(ds).lower()
        for substr, (sev, label) in _DEFENCE_LIST_LABELS.items():
            if substr in ds_lower and label not in seen_labels:
                seen_labels.add(label)
                hits.append((str(ds), sev, label))
                break
    return hits


# Any match scoring below this floor is demoted to info regardless of
# topic. Below 0.75 is fuzzy noise — wrong-entity-same-name collisions.
SCORE_FLOOR_FOR_ESCALATION = 0.75

# Common corporate suffixes / legal-form indicators stripped from
# entity names before token comparison. Fuzzy screening often latches
# onto these short tokens (SRL, LTD, LLC, INC) because they're
# high-frequency across the database — a shared "LTD" token is not
# evidence that two companies are related. Source: most national
# company-law vocabularies.
_CORP_SUFFIXES: set[str] = {
    # Romanian / Italian
    "srl", "spa", "snc", "sas",
    # German / Austrian / Swiss
    "gmbh", "ag", "kg", "kgaa", "mbh", "ohg",
    # UK / Commonwealth
    "ltd", "limited", "plc", "llp", "lp", "pty",
    # US
    "llc", "inc", "corp", "corporation", "company", "co", "lllp",
    # R-F3806 — "incorporated" was MISSING while "inc" was present, and every other
    # long form here ("corporation", "limited", "company", "partnership") has its
    # short form alongside it, so this was an oversight rather than a decision.
    #
    # It is not cosmetic: at twelve characters it clears the ">=5 chars" bar in the
    # single-shared-token rules (usaspending._recipient_confirms:49, and the same
    # shape in _name_overlap), so two unrelated "<X> INCORPORATED" entities shared a
    # token strong enough to confirm each other on a purely grammatical word. That is
    # the BATSELA CAPITAL failure mode described below, in a word nobody listed.
    # Dropping it cannot lose identity signal — a legal form carries none — and an
    # exact-name hit still takes the R-F569 bypass before overlap is consulted.
    "incorporated",
    # French / Belgian / Lux
    "sa", "sarl", "sas", "scs", "scrl", "sprl", "eurl",
    # Spanish / Portuguese / LatAm
    "sl", "slu", "sau", "lda", "ltda", "sab", "cv",
    # Dutch / Belgian
    "bv", "nv", "cv", "vof",
    # Nordic
    "ab", "as", "oy", "oyj", "aps",
    # Post-Soviet / CIS
    "ooo", "zao", "oao", "pao", "too", "tov", "doo", "dd",
    # Slavic
    "sro", "as", "zrt", "kft", "nyrt", "sp",
    # Chinese / East Asian
    "gs",
    # Misc / descriptive
    "holdings", "holding", "group", "grupo", "gruppo", "group",
    "international", "intl", "global", "worldwide", "industries",
    "industry", "trading", "trade", "services", "service", "solutions",
    "systems", "technologies", "technology", "tech", "enterprise",
    "enterprises", "partners", "partnership", "associates",
    # Financial-sector descriptors. The list above covers industrial and tech
    # naming thoroughly and omitted the financial equivalents entirely — in a
    # product whose subjects are overwhelmingly investment vehicles.
    #
    # MEASURED, run dd_29368fbb8b3d (2026-08-03): "BATSELA CAPITAL INVESTMENTS
    # L.L.P" was HARD_STOP'd against OFAC SDN "D.G.D. INVESTMENTS LTD." at score
    # 0.85. The ONLY shared token was "investments". It survived the single-
    # overlap guard below purely because that guard demotes on token LENGTH
    # (<5, the R-F351 acronym rule) and "investments" is eleven characters —
    # length is not evidence, distinctiveness is. The report told the operator
    # to refuse the engagement and consider filing a SAR, which is exactly the
    # outcome the comment on that guard says must be avoided at almost any cost.
    #
    # Genuine matches are unaffected: an exact-name hit still scores >=0.95 and
    # takes the R-F569 bypass before overlap is consulted at all, and any real
    # entity carries at least one distinctive token beyond these.
    "investments", "investment", "invest", "capital", "capitals",
    "ventures", "venture", "equity", "equities", "asset", "assets",
    "fund", "funds", "finance", "financial", "financials", "management",
    "advisors", "advisers", "advisory", "securities",
    # Slovenian / Croatian / Serbian
    "d.o.o", "doo", "d.d", "d.n.o", "k.d",
    # Greek
    "ae", "epe",
    # Non-alphanumeric stripped variants
    "jsc", "ojsc", "cjsc", "pjsc",
}

# Generic English words that have no entity-identification value.
_STOPWORDS: set[str] = {
    "the", "and", "of", "for", "in", "on", "at", "to", "by",
    "a", "an", "with", "or", "new", "old",
}

# R-F277 (2026-05-11) — geographic / country tokens. Two entities sharing
# a country name in their corporate name is NOT evidence of relationship
# (both legally registered there, that's all). Pre-R-F277 a query like
# "LNG TRADING INTERNATIONAL PANAMA SA" hit "EBANO PETROLEUM PANAMA SA"
# via the shared "panama" token (everything else was stripped as a corp
# suffix or stopword), passing the token-overlap demotion gate and
# producing a false-positive HARD_STOP. Geographic tokens MUST be filtered
# the same way corp suffixes are: they are name-shape filler, not name-
# identity tokens.
#
# Conservative scope — country / region names + common geographic
# adjectives. Cities are NOT included (city names can be discriminating —
# "Belgrade Industries" vs "Sofia Industries" — keep them as evidence).
_GEOGRAPHIC_TOKENS: set[str] = {
    # Country names (ISO common forms, lowercase)
    "afghanistan", "albania", "algeria", "andorra", "angola", "argentina",
    "armenia", "australia", "austria", "azerbaijan", "bahamas", "bahrain",
    "bangladesh", "barbados", "belarus", "belgium", "belize", "benin",
    "bhutan", "bolivia", "bosnia", "botswana", "brazil", "brunei", "bulgaria",
    "burkina", "burundi", "cambodia", "cameroon", "canada", "chad", "chile",
    "china", "colombia", "comoros", "congo", "croatia", "cuba", "cyprus",
    "czech", "denmark", "djibouti", "dominica", "ecuador", "egypt",
    "eritrea", "estonia", "ethiopia", "fiji", "finland", "france", "gabon",
    "gambia", "georgia", "germany", "ghana", "greece", "grenada",
    "guatemala", "guinea", "guyana", "haiti", "honduras", "hungary",
    "iceland", "india", "indonesia", "iran", "iraq", "ireland", "israel",
    "italy", "jamaica", "japan", "jordan", "kazakhstan", "kenya", "kiribati",
    "korea", "kosovo", "kuwait", "kyrgyzstan", "laos", "latvia", "lebanon",
    "lesotho", "liberia", "libya", "lithuania", "luxembourg", "macedonia",
    "madagascar", "malawi", "malaysia", "maldives", "mali", "malta",
    "mauritania", "mauritius", "mexico", "micronesia", "moldova", "monaco",
    "mongolia", "montenegro", "morocco", "mozambique", "myanmar", "namibia",
    "nauru", "nepal", "netherlands", "nicaragua", "niger", "nigeria",
    "norway", "oman", "pakistan", "palau", "palestine", "panama", "papua",
    "paraguay", "peru", "philippines", "poland", "portugal", "qatar",
    "romania", "russia", "rwanda", "samoa", "saudi", "senegal", "serbia",
    "seychelles", "singapore", "slovakia", "slovenia", "somalia",
    "spain", "sudan", "suriname", "swaziland", "sweden", "switzerland",
    "syria", "taiwan", "tajikistan", "tanzania", "thailand", "togo",
    "tonga", "trinidad", "tunisia", "turkey", "turkmenistan", "tuvalu",
    "uganda", "ukraine", "uruguay", "uzbekistan", "vanuatu", "venezuela",
    "vietnam", "yemen", "zambia", "zimbabwe",
    # United Kingdom / United States / United Arab Emirates short forms
    "uk", "united", "usa", "uae", "kingdom", "states", "america", "emirates",
    "britain", "england", "scotland", "wales", "ireland",
    # Major regional descriptors
    "european", "african", "asian", "american", "atlantic", "pacific",
    "mediterranean", "caribbean", "balkan", "balkans", "baltic", "iberian",
    "nordic", "scandinavian", "eurasian", "eurasia", "latam", "mena",
    # Adjective forms of common DD jurisdictions
    "panamanian", "swiss", "russian", "chinese", "japanese", "korean",
    "iranian", "iraqi", "turkish", "german", "french", "italian", "spanish",
    "portuguese", "brazilian", "indian", "pakistani", "nigerian", "kenyan",
    "egyptian", "saudi", "emirati", "lebanese", "syrian", "yemeni",
    "ukrainian", "polish", "czech", "romanian", "bulgarian", "greek",
    "british", "english", "american", "canadian", "mexican", "venezuelan",
    "colombian", "argentinian", "chilean", "peruvian", "indonesian",
    "vietnamese", "malaysian", "thai", "filipino", "australian", "moroccan",
    "algerian", "libyan", "sudanese", "ethiopian", "somali", "south",
    "north", "east", "west", "central", "northern", "southern", "eastern",
    "western",
}


def _tokenize_entity_name(name: str) -> set[str]:
    """Split a company / person name into meaningful lowercase tokens.

    Drops corporate suffixes, stopwords, and tokens shorter than 3
    characters (which are almost always acronyms / particles with no
    discriminating power). Normalises diacritics and non-alphanumeric
    punctuation to whitespace so "São Tomé" and "Cote d'Ivoire" split
    cleanly.
    """
    if not name:
        return set()
    import re as _re
    import unicodedata as _ud
    # Normalise accents: "Moçambique" → "Mocambique"
    normalised = _ud.normalize("NFKD", name)
    normalised = "".join(c for c in normalised if not _ud.combining(c))
    # Non-alphanumeric → whitespace
    cleaned = _re.sub(r"[^a-zA-Z0-9]+", " ", normalised).lower()
    tokens = cleaned.split()
    return {
        t for t in tokens
        if len(t) >= 3
        and t not in _CORP_SUFFIXES
        and t not in _STOPWORDS
        and t not in _GEOGRAPHIC_TOKENS  # R-F277: shared country names ≠ identity evidence
        and not t.isdigit()
    }


def _name_overlap(query: str, candidate: str) -> int:
    """Number of meaningful tokens shared between query and candidate."""
    q_tokens = _tokenize_entity_name(query)
    c_tokens = _tokenize_entity_name(candidate)
    return len(q_tokens & c_tokens)


def classify_match(match: dict, query_name: str = "") -> str:
    """Classify one match into 'info' | 'amber' | 'red' | 'hard_stop'.

    If `query_name` is provided, the function additionally enforces a
    token-overlap check: the match must share at least one meaningful
    token (after stripping corporate suffixes + stopwords + <3-char
    tokens) with the queried name. Matches that fail this check are
    demoted to 'info' regardless of topic or score — they are fuzzy-
    Levenshtein noise, not real hits. This prevents "Serban Industries
    SRL" from being treated as an FBI-wanted person on the basis of a
    0.83 Levenshtein score on short tokens.
    """
    if not isinstance(match, dict):
        return "info"
    score = float(match.get("score") or 0.0)
    topics = match.get("topics") or []
    severity = "info"
    for t in topics:
        sev = _TOPIC_SEVERITY.get(t, "info")
        if SEVERITY_RANK[sev] > SEVERITY_RANK[severity]:
            severity = sev
    # Dataset-level ICC/Interpol detection — some matches have the dataset
    # name ("icc", "interpol_red_notices") but no explicit topic tag.
    # 2026-04-12: explicit routing so DD reports show "ICC warrant" clearly.
    datasets = match.get("lists") or match.get("datasets") or []
    ds_lower = " ".join(str(d).lower() for d in datasets)
    if "icc" in ds_lower and SEVERITY_RANK["hard_stop"] > SEVERITY_RANK[severity]:
        severity = "hard_stop"
    elif "interpol" in ds_lower and "red" in ds_lower and SEVERITY_RANK["hard_stop"] > SEVERITY_RANK[severity]:
        severity = "hard_stop"
    elif "interpol" in ds_lower and SEVERITY_RANK["red"] > SEVERITY_RANK[severity]:
        severity = "red"
    # R-F55: defence-DD-relevant lists (NDAA 1260H, DoD 1233, CMIC,
    # NS-CMIC, BIS Entity, EU FSF / Council, UK FCDO / OFSI, UN SC).
    # Escalation precedence matches the existing ICC/Interpol pattern:
    # if ANY defence list matches, take the highest severity from
    # _DEFENCE_LIST_LABELS, but only if it's higher than the current.
    for _, _list_sev, _ in _defence_list_hits(datasets):
        if SEVERITY_RANK[_list_sev] > SEVERITY_RANK[severity]:
            severity = _list_sev
    # Score floor — if escalation-worthy but fuzzy, demote to info
    if SEVERITY_RANK[severity] >= 1 and score < SCORE_FLOOR_FOR_ESCALATION:
        severity = "info"
    # Token-overlap check — if query is provided and the match doesn't
    # share any meaningful token with it, demote. Skipped when no query
    # is provided (backward-compat for callers that haven't been updated).
    #
    # R-F351 (2026-05-12) — strengthened: when the only shared token is
    # short (<5 chars, typically an acronym like "ADSM" / "ARMS" / "CORE"),
    # demote even on overlap=1. Live evidence: an earlier-turn DD on
    # "ADSM Saudi Arabia" produced a HARD_STOP on "SHAZAND PETROCHEMICAL
    # COMPANY" — a Levenshtein hit on an OFAC SDN entry from completely
    # different industry/jurisdiction. Even though THIS specific case has
    # zero token overlap and was already demoted by R-F277, the broader
    # class — single short-acronym overlap on otherwise unrelated multi-
    # token entities — was still passing. Real OFAC hits typically share
    # multiple tokens (e.g. "Vladimir Putin" → "Vladimir Vladimirovich
    # Putin" shares 2) or a single token ≥5 chars (e.g. "Modirum" → 7).
    # Cost of false-positive HARD_STOP (defamation, SAR mis-filing) >>
    # cost of false-negative demote-to-info (operator still sees match in
    # per_match[], can manually escalate).
    # R-F569 (2026-05-16) — near-exact score bypass. Real transliteration
    # variants (e.g. "Rosoboronexport" / "ROSOBORONEKSPORT OAO", differs
    # by one letter in the middle) tokenise to non-overlapping sets but
    # represent the SAME sanctioned entity. When the fuzzy score is
    # near-identical (≥0.95) the token-overlap discipline is what catches
    # spelling/punctuation/legal-form drift on a real match — we trust
    # the score and skip the demotion.
    #
    # R-F569.5 (2026-05-16 hotfix) — bypass also requires
    # string_similarity≥0.50. Live evidence: post-R-F569 deploy at 12:48
    # logged
    #   Aselsan A.S. (variant "AA") → "Abdelbassed Azouz" alias "AA (inisial)"
    #   score=1.0 (exact match on the AA acronym) but
    #   string_similarity=0.067 (between "Aselsan A.S." and the full
    #   Abdelbassed Azouz record).
    # The bypass was meant for real transliterations like
    # Rosoboronexport↔ROSOBORONEKSPORT (sim ≈ 0.9) — not for short-
    # acronym variant noise. Requiring sim≥0.50 keeps the Rosoboronexport
    # path open while closing the acronym-collision noise.
    _score = float(match.get("score") or 0.0)
    # R-F4177 (C-189) — a provider field that will not parse must not take the
    # whole classification with it. `is_corroborated_match` already guards this
    # exact field with a try/except; this sibling did not, so a non-numeric
    # `string_similarity` raised ValueError out of the sanctions classifier.
    # Unparseable is treated as UNMEASURABLE (0.0 here only suppresses the
    # R-F569 bypass, which is the safe direction: it keeps the overlap
    # discipline rather than skipping it).
    try:
        _sim = float(match.get("string_similarity") or 0.0)
    except (TypeError, ValueError):
        _sim = 0.0
    _bypass_overlap_check = _score >= 0.95 and _sim >= 0.50
    if query_name and SEVERITY_RANK[severity] >= 1 and not _bypass_overlap_check:
        candidate_name = match.get("name") or match.get("caption") or ""
        q_tokens = _tokenize_entity_name(query_name)
        c_tokens = _tokenize_entity_name(candidate_name)
        shared = q_tokens & c_tokens
        if len(shared) == 0:
            severity = "info"
        elif len(shared) == 1:
            # R-F351: short-acronym single-overlap is high false-positive risk
            only_token = next(iter(shared))
            if len(only_token) < 5:
                severity = "info"
    # ── R-F4177 (C-189) — A BLOCKING VERDICT MUST PASS THE BLOCKING GATE ────
    #
    # `is_corroborated_match()` opens with "True iff `match` may drive a BLOCKING
    # verdict. Deliberately strict and deliberately shared. A match that fails
    # this is still reported — as a related-name observation, not as a
    # designation." `derive_verified_sources` obeys it: an uncorroborated match
    # is not counted as a HIT, so the per-source table reports CLEAN. This
    # function — the one that actually decides `hard_stop` — never called it.
    #
    # MEASURED on the match in delivered report dd_0d94ba69f415 (2026-08-19):
    #     is_corroborated_match(m)                     -> False
    #     derive_verified_sources([m]) -> BIS Entity   -> CLEAN
    #     classify_match(m, "BLACK ROSE SECURITY LTD") -> hard_stop
    # The customer received both: "HARD STOP — mandatory refusal … File SAR" on
    # page 1, "BIS Entity List — CLEAN" on page 4. Same match, same run, same
    # module, opposite conclusions.
    #
    # This introduces NO new judgement and NO new number — it applies
    # `_MIN_BLOCK_SIMILARITY`, which this module already sets and already
    # enforces on the per-source path. It is therefore NOT an answer to the open
    # C-186 question (should a lone shared generic token ever compel a refusal?),
    # which is a compliance policy call with legal exposure both ways.
    #
    # It cannot manufacture a false clean, and that is load-bearing: R-F2840
    # deliberately made an ABSENT similarity non-excluding ("could not measure"
    # is never "measured and clear"). Every case the suite requires to escalate
    # — Modirum, Rosoboronexport, Putin — carries no similarity field and passes
    # untouched. That is why this separates the false positive from the real
    # hits where a token-shape rule could not: it separates on EVIDENCE.
    #
    # Capped at AMBER rather than demoted to `info`, in the gate's own words:
    # "still reported — as a related-name observation". Burying it would trade a
    # false refusal for a quiet miss, which C-39 records as the worse failure.
    if SEVERITY_RANK[severity] >= SEVERITY_RANK["red"] and not is_corroborated_match(match):
        severity = "amber"

    # R-F434 (2026-05-13): brandified-hostname origin cap. When the only
    # path to this match was a hostname (`ngast.com` → `ngast`, `armesavn.com`
    # → `armesavn`) without legal-name corroboration from the crawl/registry,
    # cap severity at AMBER regardless of topic. Two live false positives
    # this conversation: ngast.com flagged Oscar Noe MEDINA GONZALEZ (OFAC
    # SDN), armesavn.com flagged SHAZAND PETROCHEMICAL — both clean US
    # firms whose brandified stems collided with unrelated SDN entries by
    # OpenSanctions string similarity. The match still surfaces for
    # operator review (per_match[]) but no HARD STOP fires until the
    # orchestrator re-screens with the verified legal entity name from
    # the website crawl or company registry. Tags set by
    # sanctions.screen_with_aliases.
    # R-F444 — read both the renamed flag AND the deprecated one so
    # callers still on the old key keep working until the next release.
    # Use bool() to handle False-or-None correctly (False or None = None,
    # but we need False for the not-check below).
    _has_caller_aliases = bool(
        match.get("_has_caller_supplied_aliases")
        or match.get("_has_legal_name_corroboration")
    )
    if (
        match.get("_from_brandified_hostname")
        and not _has_caller_aliases
        and SEVERITY_RANK[severity] > SEVERITY_RANK["amber"]
    ):
        severity = "amber"
    # R-F996 — wire to brain
    from .engine_wiring import wire_success, wire_failure
    wire_success(
        module="_sanctions_classify",
        summary="Classify Match",
        source_id="_sanctions_classify:R-F996",
    )

    return severity


def classify_matches(matches: list[dict], query_name: str = "") -> dict:
    """Classify a set of sanctions matches.

    If `query_name` is provided, applies a token-overlap filter so
    fuzzy-match noise (matches whose caption shares zero meaningful
    tokens with the query) is demoted to 'info' before severity
    aggregation. This fixes the Serban Industries SRL false-positive
    pattern where Levenshtein-fuzzy matches on short strings produced
    score-0.8+ hits on wildly unrelated entities.

    Returns:
        {
          "worst_severity":  "info" | "amber" | "red" | "hard_stop" | "none",
          "summary":         "BAE Systems plc (score 1.00, topics: corp.state,corp.public)",
          "per_match":       [{name, score, topics, datasets, severity, token_overlap}, ...],
          "total_matches":   int,
          "noise_filtered":  int    # count of matches demoted by token-overlap check
        }
    """
    if not matches:
        return {
            "worst_severity": "none",
            "summary": "no matches",
            "per_match": [],
            "total_matches": 0,
            "noise_filtered": 0,
        }

    per_match: list[dict] = []
    worst = "info"
    worst_rank = -1
    noise_filtered = 0
    for m in matches:
        if not isinstance(m, dict):
            continue
        # Compute topic-based severity first (ignoring query)
        topic_severity = classify_match(m, query_name="")
        # Then apply query-aware severity which may demote due to token overlap
        final_severity = classify_match(m, query_name=query_name) if query_name else topic_severity
        candidate_name = m.get("name") or m.get("caption") or ""
        overlap = _name_overlap(query_name, candidate_name) if query_name else -1
        was_demoted = (
            query_name
            and SEVERITY_RANK[topic_severity] >= 1
            and SEVERITY_RANK[final_severity] < SEVERITY_RANK[topic_severity]
        )
        # R-F2362: the token-overlap demotion in classify_match is gated behind
        # severity>=amber, so an acronym-collision hit that carries an INFO
        # topic ("Modirum Gespi"→"MG"→"MG Corp", transparency/state-ownership)
        # never gets demoted and renders as a real match. Flag any match with
        # ZERO meaningful-token overlap as noise here, regardless of tier —
        # preserving the R-F569 near-exact bypass (real transliterations score
        # >=0.95 & sim>=0.50) so genuine hits are never suppressed.
        _score_m = float(m.get("score") or 0.0)
        _sim_m = float(m.get("string_similarity") or 0.0)
        _near_exact_m = _score_m >= 0.95 and _sim_m >= 0.50
        # R-F2362: a match on a KNOWN ALIAS of the sanctioned entity is
        # legitimate even though the candidate's PRIMARY name shares no token
        # with the query (query "Acme Holdings Ltd" → alias of "Banca Sospetta
        # SpA"). Detect it by the query actually overlapping the matched alias
        # token — never treat a real alias hit as noise (never-false-clean).
        # Acronym noise ("MG"→"MG Corp") fails this: its matched token "MG"
        # tokenises away, so overlap with the query is 0.
        _matched_tok = str(m.get("matched_token") or "")
        _alias_legit = (
            m.get("match_field") == "alias"
            and bool(_matched_tok)
            and _name_overlap(query_name, _matched_tok) >= 1
        )
        zero_overlap_noise = bool(
            query_name and overlap == 0 and not _near_exact_m and not _alias_legit
        )
        is_noise = bool((was_demoted or zero_overlap_noise) and not _alias_legit)
        if is_noise:
            noise_filtered += 1
        _ds = m.get("lists") or m.get("datasets") or []
        _list_labels = [label for _, _, label in _defence_list_hits(_ds)]
        # R-F434: detect brandified-hostname cap separately from token-
        # overlap demotion so the operator can see which gate fired.
        # A match is "hostname-capped" when the origin tag is set and there
        # was no legal-name corroboration. The cap already fired inside
        # classify_match (reducing severity from hard_stop/red to amber),
        # so we check the raw flags rather than topic_severity (which is
        # already post-cap and would be amber == amber → false negative).
        # R-F444 — same renamed-with-fallback pattern as classify_match
        _has_caller_aliases_m = bool(
            m.get("_has_caller_supplied_aliases")
            or m.get("_has_legal_name_corroboration")
        )
        hostname_capped = bool(
            m.get("_from_brandified_hostname")
            and not _has_caller_aliases_m
        )
        per_match.append({
            "name":     candidate_name,
            "score":    float(m.get("score") or 0.0),
            "topics":   m.get("topics") or [],
            "datasets": _ds,
            "list_labels": _list_labels,  # R-F55: human-readable list names
            "severity": final_severity,
            "token_overlap": overlap,
            "noise_filtered": is_noise,  # R-F2362: was_demoted OR zero-overlap noise
            "brandified_hostname_capped": hostname_capped,  # R-F434
            "brandified_stem": m.get("_brandified_stem") or "",  # R-F434
            # R-F335 (2026-05-11): match-path transparency for operator
            # verification. Without these the operator can't tell HOW the
            # query reached the sanctioned candidate (was it primary name,
            # alias, weak fuzzy, etc.) — the Swisscraft Aviation 22:29 DD
            # showed a Michele Zagaria HARD_STOP with no match-path
            # explanation, leaving the operator unable to verify.
            "sdn_entry_id": m.get("sdn_entry_id") or "",
            "match_field":  m.get("match_field") or "weak_match",
            "matched_token": m.get("matched_token") or candidate_name,
            "match_path":   m.get("match_path") or "",
            "match_url":    m.get("url") or "",
        })
        if SEVERITY_RANK[final_severity] > worst_rank:
            worst = final_severity
            worst_rank = SEVERITY_RANK[final_severity]

    # Compact human summary — show up to 3 worst-class matches
    # R-F2362: never list name-overlap noise in the summary — otherwise an
    # all-noise result reads as "MG Corp; MG Global; …" instead of the honest
    # "N fuzzy matches, all filtered as noise, no real hits" fallback below.
    worst_matches = [pm for pm in per_match if pm["severity"] == worst and not pm.get("noise_filtered")]
    parts: list[str] = []
    for pm in worst_matches[:3]:
        topics_str = ",".join(pm["topics"][:3]) or "untagged"
        # R-F55: prefer the human-readable list_labels when defence-DD
        # lists matched; fall back to raw dataset slugs otherwise.
        if pm.get("list_labels"):
            lists_str = "; ".join(pm["list_labels"][:2])
        else:
            lists_str = ",".join(pm["datasets"][:2]) if pm["datasets"] else ""
        # R-F335: include the match path so the operator can verify
        # WHY the candidate matched (primary name? alias? weak fuzzy?).
        # Without this, every HARD_STOP relies on operator trust.
        _mp_field = pm.get("match_field") or ""
        _mp_token = pm.get("matched_token") or pm["name"]
        _mp_url = pm.get("match_url") or ""
        _path_str = ""
        if _mp_field and _mp_token != pm["name"]:
            _path_str = f", matched_via={_mp_field}='{_mp_token}'"
        elif _mp_field:
            _path_str = f", matched_via={_mp_field}"
        if _mp_url:
            _path_str += f" [verify: {_mp_url}]"
        parts.append(
            f"{pm['name']} (score {pm['score']:.2f}, topics: {topics_str}"
            f"{', lists: ' + lists_str if lists_str else ''}"
            f"{_path_str})"
        )
    summary = "; ".join(parts)
    if len(worst_matches) > 3:
        summary += f" [+{len(worst_matches) - 3} more]"
    # R-F3053 — a counter-sanction needs its meaning stated, or an AMBER on a
    # Western defence executive reads as an accusation. Appended once, only when a
    # counter-sanctions topic is actually present among the driving matches.
    if any("sanction.counter" in (t or "") for pm in worst_matches for t in (pm.get("topics") or [])):
        summary += (
            " — NOTE: this is a COUNTER-SANCTIONS listing (a designation made by a state "
            "in retaliation for another state's sanctions, e.g. the Chinese or Russian "
            "counter-lists). It is NOT a UK/EU/US legal prohibition and does not by itself "
            "bar dealing with this party; for a Western defence counterparty it commonly "
            "reflects their legitimate defence work. It IS material to that party's banking, "
            "travel and ability to operate in the designating jurisdiction — verify the "
            "listing and assess exposure there."
        )
    if not summary:
        if noise_filtered > 0:
            summary = (
                f"{len(per_match)} fuzzy matches — {noise_filtered} filtered as name-overlap noise "
                f"(matched entities share no meaningful token with the query). No real hits."
            )
        else:
            summary = f"{len(per_match)} matches — all below action threshold"

    return {
        "worst_severity": worst,
        "summary": summary,
        "per_match": per_match,
        "total_matches": len(per_match),
        "noise_filtered": noise_filtered,
    }

# R-F2538: R-F2119 import-time wire_failure("module shutdown") block removed — it fired a FALSE engine_failure gap on every import (not at shutdown); do not re-add.
