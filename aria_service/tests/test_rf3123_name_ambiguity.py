"""R-F3123 — a confident report on a silently-chosen entity.

MEASURED on two real runs of the SAME query (Mitie, 2026-07-26):

    "MITIE FACILITIES MANAGEMENT LIMITED" -> 6 Companies House records
      07281729  dissolved  2010-06-11  MITIE FACILITIES MANAGEMENT LIMITED
      02938041  active     1994-06-13  MITIE LIMITED
      00906936  active     1967-05-24  MITIE TECHNICAL FACILITIES MANAGEMENT LIMITED
      + 3 more

One run resolved 07281729 (exact name, DISSOLVED). An earlier report resolved
02938041 — matched on a FORMER name, and actually MITIE LIMITED, ACTIVE. Two
different legal entities from one query, and NEITHER report said the name was
ambiguous.

The RANKING is not the defect — an exact distinctive-name match SHOULD win. The
defect is SILENCE. Identity is the field every other section hangs off: press,
financials, sanctions and ownership all describe "the entity named here". Asserting
an identity that was merely inferred fabricates the subject, which is worse than a
thin report because it is confidently wrong.
"""
from aria_service.intel import companies_house as ch

# R-F3782/§16 — NOT inspect.getsource: it slices at line numbers captured
# AT IMPORT, so a mid-run edit silently returns a DIFFERENT function's body.
from ._source_probe import function_source, module_source


MITIE = [
    {"company_number": "07281729", "title": "MITIE FACILITIES MANAGEMENT LIMITED",
     "company_status": "dissolved", "date_of_creation": "2010-06-11"},
    {"company_number": "02938041", "title": "MITIE LIMITED",
     "company_status": "active", "date_of_creation": "1994-06-13"},
    {"company_number": "00906936", "title": "MITIE TECHNICAL FACILITIES MANAGEMENT LIMITED",
     "company_status": "active", "date_of_creation": "1967-05-24"},
]


def test_rf3123_the_live_mitie_case_is_flagged_ambiguous():
    d = {}
    ch._pick_best_company("MITIE FACILITIES MANAGEMENT LIMITED", MITIE, d)
    assert d["resolved"] == "07281729", "exact distinctive-name match still wins"
    assert d["ambiguous"] is True, (
        "R-F3123 REGRESSION: a dissolved pick beside active alternatives is silent again")
    joined = " ".join(d["reasons"])
    assert "dissolved" in joined and "ACTIVE" in joined
    assert "02938041" in joined, "name the active alternative the reader must consider"


def test_rf3123_ranking_behaviour_is_unchanged():
    """This must disclose, not re-rank. Changing the winner would silently move every
    existing case to a different subject."""
    assert ch._pick_best_company("MITIE FACILITIES MANAGEMENT LIMITED",
                                 MITIE)["company_number"] == "07281729"
    # and R-F3014's overseas-entity guard still holds
    cohort = [
        {"company_number": "OE003509", "title": "COHORT PLC", "company_status": "active"},
        {"company_number": "05684823", "title": "COHORT PLC", "company_status": "active"},
    ]
    assert ch._pick_best_company("Cohort plc", cohort)["company_number"] == "05684823"


def test_rf3123_an_unambiguous_single_match_raises_no_alarm():
    """The disclosure must not fire on every company, or it becomes noise."""
    d = {}
    ch._pick_best_company("ACME WIDGETS LIMITED", [
        {"company_number": "01234567", "title": "ACME WIDGETS LIMITED",
         "company_status": "active", "date_of_creation": "2001-01-01"}], d)
    assert d["ambiguous"] is False and d["reasons"] == []


def test_rf3123_tied_top_scores_are_disclosed():
    d = {}
    ch._pick_best_company("ACME LIMITED", [
        {"company_number": "01", "title": "ACME LIMITED", "company_status": "active"},
        {"company_number": "02", "title": "ACME LIMITED", "company_status": "active"},
    ], d)
    assert d["ambiguous"] is True
    # R-F3461 — the PROPERTY this guard owns is that a tie is DISCLOSED, not the exact
    # sentence used. These two rows share an exact LEGAL NAME, not merely a top score, so
    # that case now gets the sharper message; both wordings satisfy the guard, and the
    # ambiguity assertion above is unchanged. Pinning the old phrasing would have blocked
    # a strictly more accurate disclosure.
    _r = " ".join(d["reasons"])
    assert ("share the top name match" in _r
            or "registered under this exact legal name" in _r), _r


def test_rf3123_inexact_match_is_disclosed():
    d = {}
    ch._pick_best_company("NORTHWIND TRADING LIMITED", [
        {"company_number": "09", "title": "NORTHWIND HOLDINGS LIMITED",
         "company_status": "active"}], d)
    assert d["ambiguous"] is True
    assert "no candidate is an exact" in " ".join(d["reasons"])


def test_rf3123_no_candidates_is_recorded_not_silent():
    d = {}
    assert ch._pick_best_company("NOBODY LTD", [], d) == {}
    assert d["resolved"] is None and d["candidates"] == []


def test_rf3123_decision_is_optional_so_callers_are_unaffected():
    """Additive out-parameter — every existing call site keeps working."""
    assert ch._pick_best_company("MITIE FACILITIES MANAGEMENT LIMITED",
                                 MITIE)["company_number"] == "07281729"


def test_rf3123_investigate_returns_the_resolution_key():
    import inspect
    src = function_source(ch, "investigate_uk_entity")
    assert '"resolution": _resolution' in src, (
        "the DD cannot disclose what the adapter does not return")
    assert "_resolution: dict = {}" in src, (
        "must be defined on every path, including the number-supplied one")


def test_rf3123_the_dd_surfaces_it_as_a_finding_AND_a_gap():
    """A prose-only disclosure never reaches the decision scorecard."""
    import inspect
    from aria_service.intel import dd_orchestrator as ddo
    src = module_source(ddo)
    assert 'Company name is AMBIGUOUS' in src
    assert "Subject identity INFERRED from an ambiguous name" in src
    assert 'source="companies_house.resolve:R-F3123"' in src
