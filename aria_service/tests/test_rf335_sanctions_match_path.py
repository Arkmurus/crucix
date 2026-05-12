"""R-F335 — sanctions match-path transparency.

Live failure 2026-05-11 22:29: operator ran DD on Swisscraft Aviation
Ltd. ARIA returned HARD_STOP based on an OFAC SDN match on Michele
Zagaria — but with no way to verify HOW the query reached Zagaria
(was the match on the entity name? a director? an alias? a fuzzy
weak link?). The operator had to manually re-search
sanctionssearch.ofac.treas.gov to validate.

R-F335 captures and surfaces:
  - sdn_entry_id        — raw OpenSanctions ID for direct lookup
  - match_field         — primary_name | alias | weak_match
  - matched_token       — the actual SDN field value that scored
  - match_path          — human-readable trace string
  - all_names_on_sdn    — every alias on the SDN entry for inspection

The classify_matches summary now includes "matched_via=<field>=
'<token>' [verify: <url>]" so chat output explains the path.
"""
from __future__ import annotations


# ───────────────────────── R-F335 — _normalise_match shape ─────────────────────

def test_rf335_normalise_match_captures_sdn_entry_id():
    """Every match must carry the raw OpenSanctions ID."""
    from aria_service.intel.sanctions import _normalise_match
    raw = {
        "id": "NK-12345abc",
        "caption": "Michele Zagaria",
        "score": 0.92,
        "schema": "Person",
        "datasets": ["us_ofac_sdn"],
        "properties": {
            "name": ["Michele Zagaria"],
            "alias": ["Capastorta", "Mezzanotte"],
            "topics": ["crime.boss", "crime.org"],
        },
    }
    out = _normalise_match(raw, queried_name="Swisscraft Aviation Ltd")
    assert out["sdn_entry_id"] == "NK-12345abc"
    assert "match_field" in out
    assert "matched_token" in out
    assert "match_path" in out


def test_rf335_match_field_primary_name_when_strong_overlap():
    """When the query closely matches the primary name on the SDN, the
    match_field is primary_name."""
    from aria_service.intel.sanctions import _normalise_match
    raw = {
        "id": "X-1",
        "caption": "Acme Holdings Ltd",
        "score": 0.95,
        "schema": "Company",
        "datasets": ["us_ofac_sdn"],
        "properties": {
            "name": ["Acme Holdings Ltd"],
            "alias": ["AHL", "Acme Group"],
        },
    }
    out = _normalise_match(raw, queried_name="Acme Holdings Ltd")
    assert out["match_field"] == "primary_name"
    assert out["matched_token"] == "Acme Holdings Ltd"


def test_rf335_match_field_alias_when_alias_overlap_higher():
    """When the query matches an alias more than the primary name, the
    match_field is alias."""
    from aria_service.intel.sanctions import _normalise_match
    raw = {
        "id": "X-2",
        "caption": "Banca Sospetta SpA",
        "score": 0.85,
        "schema": "Company",
        "datasets": ["us_ofac_sdn"],
        "properties": {
            "name": ["Banca Sospetta SpA"],
            "alias": ["Acme Holdings Ltd"],
        },
    }
    out = _normalise_match(raw, queried_name="Acme Holdings Ltd")
    assert out["match_field"] == "alias"
    assert out["matched_token"] == "Acme Holdings Ltd"


def test_rf335_match_path_contains_query_and_token_and_sdn_id():
    """The human-readable match_path string contains every piece an
    operator needs to verify manually."""
    from aria_service.intel.sanctions import _normalise_match
    raw = {
        "id": "NK-12345abc",
        "caption": "Michele Zagaria",
        "score": 0.83,
        "datasets": ["us_ofac_sdn"],
        "properties": {
            "name": ["Michele Zagaria"],
            "alias": ["Capastorta"],
        },
    }
    out = _normalise_match(raw, queried_name="Michele Zagaria")
    mp = out["match_path"]
    assert "query=" in mp
    assert "Michele Zagaria" in mp
    assert "NK-12345abc" in mp
    assert "us_ofac_sdn" in mp


def test_rf335_all_names_on_sdn_includes_aliases():
    """Operator-visible: every name/alias on the SDN so they can
    cross-check manually."""
    from aria_service.intel.sanctions import _normalise_match
    raw = {
        "id": "X-3",
        "caption": "Target Co",
        "score": 0.85,
        "datasets": ["us_ofac_sdn"],
        "properties": {
            "name": ["Target Co", "Target Company"],
            "alias": ["TGT", "T Co", "Target Corp"],
        },
    }
    out = _normalise_match(raw, queried_name="Target Co")
    assert "Target Co" in out["all_names_on_sdn"]
    assert "Target Corp" in out["all_names_on_sdn"]
    assert "TGT" in out["all_names_on_sdn"]


# ───────────────────────── R-F335 — classify_matches surface ───────────────────

def test_rf335_per_match_includes_match_path_fields():
    """classify_matches per_match output must carry the R-F335 fields."""
    from aria_service.intel._sanctions_classify import classify_matches
    matches = [
        {
            "name": "Michele Zagaria",
            "score": 0.85,
            "topics": ["crime.boss"],
            "lists": ["us_ofac_sdn"],
            "sdn_entry_id": "NK-12345abc",
            "match_field": "primary_name",
            "matched_token": "Michele Zagaria",
            "match_path": "query='Michele Zagaria' → score=0.85 → matched_field=primary_name='Michele Zagaria' → sdn_id=NK-12345abc (us_ofac_sdn)",
            "url": "https://www.opensanctions.org/entities/NK-12345abc/",
        },
    ]
    out = classify_matches(matches, query_name="Michele Zagaria")
    pm = out["per_match"][0]
    assert pm["sdn_entry_id"] == "NK-12345abc"
    assert pm["match_field"] == "primary_name"
    assert pm["matched_token"] == "Michele Zagaria"
    assert "match_path" in pm
    assert pm["match_url"].endswith("/NK-12345abc/")


def test_rf335_capability_summary_includes_verification_url():
    """The human summary string must include the verify URL so the
    operator can paste it directly. The Swisscraft 22:29 chat output
    didn't have this — operator had to manually open
    sanctionssearch.ofac.treas.gov."""
    from aria_service.intel._sanctions_classify import classify_matches
    matches = [
        {
            "name": "Michele Zagaria",
            "score": 0.85,
            "topics": ["crime.boss"],
            "lists": ["us_ofac_sdn"],
            "sdn_entry_id": "NK-12345abc",
            "match_field": "primary_name",
            "matched_token": "Michele Zagaria",
            "url": "https://www.opensanctions.org/entities/NK-12345abc/",
        },
    ]
    out = classify_matches(matches, query_name="Michele Zagaria")
    summary = out["summary"]
    # Verification URL is in the summary string for one-click verification
    assert "https://www.opensanctions.org" in summary
    assert "matched_via=primary_name" in summary


def test_rf335_capability_alias_match_explicitly_named_in_summary():
    """When the match is on an alias rather than the primary name, the
    summary explicitly says so — the operator should not mistake an
    alias-only match for an entity-name match."""
    from aria_service.intel._sanctions_classify import classify_matches
    matches = [
        {
            "name": "Banca Sospetta SpA",  # primary name on the SDN
            "score": 0.85,
            "topics": ["sanction"],
            "lists": ["us_ofac_sdn"],
            "sdn_entry_id": "BS-99",
            "match_field": "alias",
            "matched_token": "Acme Holdings Ltd",  # the query matched THIS alias
            "url": "https://www.opensanctions.org/entities/BS-99/",
        },
    ]
    out = classify_matches(matches, query_name="Acme Holdings Ltd")
    summary = out["summary"]
    assert "matched_via=alias" in summary
    assert "Acme Holdings Ltd" in summary
    assert "Banca Sospetta SpA" in summary


# ───────────────────────── Capability: Swisscraft 22:29 fixture ────────────────

def test_rf351_adsm_saudi_arabia_demoted_against_shazand_petrochemical():
    """R-F351 regression: query 'ADSM Saudi Arabia' MUST NOT produce a
    HARD_STOP on OFAC SDN entry 'SHAZAND PETROCHEMICAL COMPANY'.

    Live evidence (operator chat 2026-05-12): an earlier-turn DD on
    'ADSM Saudi Arabia' flagged HARD_STOP against SHAZAND PETROCHEMICAL
    via Levenshtein-fuzzy match. ARIA was epistemically disciplined
    enough to not propagate the verdict into the next turn, but the
    underlying matcher was still emitting it. This test pins the
    correct behaviour so future refactors can't regress."""
    from aria_service.intel._sanctions_classify import classify_matches
    matches = [
        {
            "name": "SHAZAND PETROCHEMICAL COMPANY",
            "score": 0.85,
            "topics": ["sanction"],
            "lists": ["us_ofac_sdn"],
            "sdn_entry_id": "OFAC-IRAN-SHAZAND",
            "match_field": "primary_name",
            "matched_token": "SHAZAND",
            "url": "https://www.opensanctions.org/entities/OFAC-IRAN-SHAZAND/",
        },
    ]
    out = classify_matches(matches, query_name="ADSM Saudi Arabia")
    assert out["worst_severity"] in ("info", "none"), (
        f"R-F351: ADSM Saudi Arabia must NOT HARD_STOP on SHAZAND; "
        f"got worst_severity={out['worst_severity']!r}"
    )
    assert out["noise_filtered"] >= 1


def test_rf351_short_acronym_single_overlap_demoted():
    """R-F351 strengthening: when the only shared token is <5 chars
    (typically a short acronym like ADSM/ARMS/CORE), demote even if
    overlap is 1. Single short-token overlap on otherwise unrelated
    multi-token entities is a high false-positive risk."""
    from aria_service.intel._sanctions_classify import classify_matches
    matches = [
        {
            "name": "ADSM Trading PJSC",  # fictional unrelated entity
            "score": 0.85,
            "topics": ["sanction"],
            "lists": ["us_ofac_sdn"],
        },
    ]
    out = classify_matches(matches, query_name="ADSM Advanced Defence Saudi")
    assert out["worst_severity"] in ("info", "none"), (
        f"R-F351: 4-char single-overlap 'adsm' must NOT HARD_STOP; "
        f"got worst_severity={out['worst_severity']!r}"
    )


def test_rf351_long_token_single_overlap_preserved():
    """R-F351: single-overlap on a LONG distinctive token (>=5 chars)
    is preserved — these are real signals (e.g. unique surname,
    distinctive corporate name). Demoting these would create false
    negatives that hide real OFAC matches."""
    from aria_service.intel._sanctions_classify import classify_matches
    matches = [
        {
            "name": "Vladimir Modirum",
            "score": 0.88,
            "topics": ["sanction"],
            "lists": ["us_ofac_sdn"],
        },
    ]
    # Query shares "modirum" (7 chars) — distinctive token; keep hard_stop.
    out = classify_matches(matches, query_name="Modirum Gespi Industries")
    assert out["worst_severity"] == "hard_stop", (
        f"R-F351: 7-char distinctive single-overlap 'modirum' must "
        f"PRESERVE hard_stop; got {out['worst_severity']!r}"
    )


def test_rf351_documented_trade_off_short_surname_demoted():
    """R-F351 documented trade-off — short Latinised surnames
    (Park/Kim/Lee/Igor/Olga/Omar — all 3-4 chars) ARE demoted when
    they are the ONLY shared token. Operator can still see the match
    in per_match[] and escalate manually. Cost calculus: false-positive
    HARD_STOP (defamation / SAR mis-filing) >> demote-to-info (operator
    review surfaces real hits).

    This test EXISTS to make the trade-off explicit so future devs
    don't 'fix' the short-token demote thinking it's a bug.
    """
    from aria_service.intel._sanctions_classify import classify_matches
    matches = [
        {
            "name": "Park In-bae",  # different person, same short surname
            "score": 0.85,
            "topics": ["sanction"],
            "lists": ["us_ofac_sdn"],
        },
    ]
    out = classify_matches(matches, query_name="Park Geun-hye")
    # Demoted by design. If a real Park family OFAC hit is on the
    # primary screen, operator-side review of per_match[] surfaces it.
    assert out["worst_severity"] in ("info", "none")
    # And the match still appears in per_match[] for operator review.
    assert len(out["per_match"]) == 1
    assert out["per_match"][0]["name"] == "Park In-bae"


def test_rf351_multi_token_overlap_preserved():
    """R-F351: 2+ token overlap always preserves severity — real OFAC
    matches typically share multiple tokens."""
    from aria_service.intel._sanctions_classify import classify_matches
    matches = [
        {
            "name": "Vladimir Vladimirovich Putin",
            "score": 0.95,
            "topics": ["sanction"],
            "lists": ["us_ofac_sdn"],
        },
    ]
    out = classify_matches(matches, query_name="Vladimir Putin")
    assert out["worst_severity"] == "hard_stop", (
        f"R-F351: 2-token overlap must preserve hard_stop; "
        f"got {out['worst_severity']!r}"
    )


def test_rf335_capability_swisscraft_fixture_emits_actionable_summary():
    """Capability — recreate the Swisscraft Aviation Ltd 22:29 failure
    shape. Operator should see (a) which field matched, (b) the
    verification URL, (c) the matched token vs primary name."""
    from aria_service.intel._sanctions_classify import classify_matches

    matches = [
        {
            "name": "Michele Zagaria",
            "score": 0.85,
            "topics": ["crime.boss", "crime.org"],
            "lists": ["us_ofac_sdn"],
            "sdn_entry_id": "OFAC-SDN-MZAGARIA",
            "match_field": "primary_name",
            "matched_token": "Michele Zagaria",
            "url": "https://www.opensanctions.org/entities/OFAC-SDN-MZAGARIA/",
        },
    ]
    out = classify_matches(matches, query_name="Swisscraft Aviation Ltd")
    # In the Swisscraft case, query is "Swisscraft Aviation Ltd" and
    # the matched name is "Michele Zagaria" — token overlap is zero,
    # so the noise-overlap demoter should kick in. The point of R-F335
    # is that even if the verdict is hard_stop, the summary now
    # explains the path.
    summary = out["summary"]
    # Either the match was demoted (noise filtered) OR it stayed and
    # the summary includes the match path. Both are operator-actionable.
    assert ("matched_via" in summary
            or "noise" in summary.lower()
            or "below action threshold" in summary), (
        f"R-F335 capability regression: summary opaque. Got: {summary!r}"
    )
