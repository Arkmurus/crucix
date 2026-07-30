"""R-F3451 — the disqualified-directors check accused real directors by tokenisation.

THE LIVE DEFECT, from a delivered report on Babcock International Group PLC. The officer
string Companies House returns is ``COMISKEY, Aedamar Ita, Dr``. That whole string —
honorific included — was sent verbatim to ``/search/disqualified-officers?q=``, an endpoint
that matches tokens independently, and EVERY returned row was rendered as a
"NAME MATCH on the disqualified-directors register" against a sitting FTSE director:

    Officer COMISKEY, Aedamar Ita, Dr: 4 NAME MATCH(ES) ...
    Candidates: DREAM HOME TRAVELS AND TOURS LTD (Dhaka); DREX TECHNOLOGIES S.A.;
                NATIONAL IRANIAN DRILLING COMPANY

The token doing that work was ``Dr`` — it prefix-matches **DR**EAM, **DR**EX and
**DR**ILLING. Two more officers were hit the same way: ``LOCKWOOD, David Charles`` matched
Emma Louise **CHARLES**, and ``MELLORS, David Anthony`` matched Paul **ANTHONY** KELLY.

NOT ONE candidate carried the officer's surname, so not one could be that person. This is
the R-F3089 name-coincidence class at its most damaging: a fabricated adverse finding about
a named individual, next to Iranian and Syrian entities, in a document a customer relies on.

THE FIX has two halves, and both are needed. Normalising the QUERY stops the register being
asked the wrong question; filtering the RESULTS on surname stops a coincidence being
reported as a hit. Filtering alone would still burn the call; normalising alone would still
return rows for common forenames.

DIRECTION OF THE FILTER. Surname match is required, forename agreement is only reported.
Dropping on forename too would risk a false NEGATIVE on a genuine disqualification, and on
this check a missed hit is the dangerous error while a spurious hit is the defamatory one.
"""
from __future__ import annotations

import asyncio

import pytest

from aria_service.intel import companies_house as ch


# The rows exactly as the register returned them on the Babcock run.
_BABCOCK_ROWS = {
    "total_results": 4,
    "items": [
        {"title": "DREAM HOME TRAVELS AND TOURS LTD",
         "address_snippet": "H-86 Bir Uttam Ziaur Rahman Road, Banani, Dhaka, Bangladesh"},
        {"title": "DREX TECHNOLOGIES S.A.", "address_snippet": "Not Available"},
        {"title": "NATIONAL IRANIAN DRILLING COMPANY",
         "address_snippet": "Pasdaran Blvd, Ahvaz, Iran"},
        {"title": "DRAKE SERVICES LIMITED", "address_snippet": "London"},
    ],
}


def test_the_honorific_is_not_treated_as_a_name():
    surname, forenames = ch._person_name_parts("COMISKEY, Aedamar Ita, Dr")
    assert surname == "COMISKEY"
    assert "Dr" not in forenames and "dr" not in [f.lower() for f in forenames], (
        "'Dr' is an honorific; sending it as a search token is what pulled back "
        "DREAM/DREX/DRILLING")
    assert forenames == ["Aedamar", "Ita"]


def test_plain_and_suffixed_names_parse():
    assert ch._person_name_parts("LOCKWOOD, David Charles") == ("LOCKWOOD", ["David", "Charles"])
    assert ch._person_name_parts("PARKER, Andrew David, Sir")[0] == "PARKER"
    assert "Sir" not in ch._person_name_parts("PARKER, Andrew David, Sir")[1]
    # No comma: the trailing token is the surname.
    assert ch._person_name_parts("Jane Bronwen Moriarty") == ("Moriarty", ["Jane", "Bronwen"])


@pytest.mark.parametrize("title", [
    "DREAM HOME TRAVELS AND TOURS LTD",
    "DREX TECHNOLOGIES S.A.",
    "NATIONAL IRANIAN DRILLING COMPANY",
])
def test_rows_without_the_surname_are_not_this_person(title):
    keeps, _ = ch._disq_candidate_is_same_name(title, "COMISKEY", ["Aedamar", "Ita"])
    assert keeps is False, f"{title!r} does not carry the surname and cannot be this officer"


def test_the_real_person_is_still_found():
    """The filter must not become a false-negative machine."""
    keeps, fore = ch._disq_candidate_is_same_name(
        "Aedamar Ita COMISKEY", "COMISKEY", ["Aedamar", "Ita"])
    assert keeps is True and fore is True
    # Surname matches, forename does not -> still KEPT, flagged as forename-mismatched.
    keeps2, fore2 = ch._disq_candidate_is_same_name("Brian COMISKEY", "COMISKEY", ["Aedamar"])
    assert keeps2 is True and fore2 is False


def test_capability_the_babcock_finding_no_longer_occurs(monkeypatch):
    """CAPABILITY: drive the real search function with the real rows.

    Asserts the user-visible outcome — the three fabricated candidates are gone and the
    check reports ZERO hits, which is what turns the amber finding back into a clean one.
    """
    sent: dict[str, str] = {}

    async def _fake_get_outcome(path: str, _attempt: int = 0):
        sent["path"] = path
        return _BABCOCK_ROWS, ch.OUTCOME_OK

    monkeypatch.setattr(ch, "_get_outcome", _fake_get_outcome)

    res = asyncio.run(ch.search_disqualified_officers("COMISKEY, Aedamar Ita, Dr"))

    assert res["checked"] is True
    assert res["total_results"] == 0, (
        "the register returned only name coincidences; reporting them as matches is the "
        f"defect: {res['candidates']}")
    assert res["candidates"] == []
    assert res["raw_results"] == 4, "the unfiltered count must stay auditable"
    assert res["discarded_name_coincidence"] == 4
    # The query must not carry the honorific that caused the DR* prefix matches.
    assert "Dr" not in sent["path"], f"honorific still sent: {sent['path']}"
    assert "COMISKEY" in sent["path"]


def test_capability_a_genuine_disqualification_still_reports(monkeypatch):
    """The other half: a real hit must survive, or the fix has broken the check."""
    async def _fake_get_outcome(path: str, _attempt: int = 0):
        return ({"total_results": 2, "items": [
            {"title": "David Charles LOCKWOOD", "address_snippet": "London",
             "date_of_birth": {"year": 1963}},
            {"title": "Emma Louise CHARLES", "address_snippet": "Swansea"},
        ]}, ch.OUTCOME_OK)

    monkeypatch.setattr(ch, "_get_outcome", _fake_get_outcome)
    res = asyncio.run(ch.search_disqualified_officers("LOCKWOOD, David Charles"))

    assert res["total_results"] == 1, "the true surname match must survive the filter"
    assert res["candidates"][0]["title"] == "David Charles LOCKWOOD"
    assert res["candidates"][0]["forename_also_matches"] is True
    assert res["discarded_name_coincidence"] == 1  # Emma Louise CHARLES


def test_the_limitation_is_disclosed(monkeypatch):
    """An honest filter says what it can miss."""
    async def _fake_get_outcome(path: str, _attempt: int = 0):
        return ({"total_results": 1, "items": [{"title": "David Charles LOCKWOOD"}]},
                ch.OUTCOME_OK)

    monkeypatch.setattr(ch, "_get_outcome", _fake_get_outcome)
    res = asyncio.run(ch.search_disqualified_officers("LOCKWOOD, David Charles"))
    assert "former or married name" in res["filter_note"]
    # `match_basis` deliberately STAYS "name_only": filtering on surname is still name
    # matching, never identification, and R-F3404 guards that statement. The new fact is
    # carried alongside it rather than replacing it — my first cut renamed the field and
    # broke a guard that was asserting something still true.
    assert res["match_basis"] == "name_only"
    assert res["surname_filter_applied"] is True


# ── R-F3515 — the live Chemring run proved R-F3451 was INSUFFICIENT ──────────
#
# A real deep DD on Chemring Group PLC (dd_8bd7ac42a488, 2026-07-30) still produced
# fabricated disqualification matches, because R-F3451 required the officer's surname to
# appear ANYWHERE in the register row rather than in the SURNAME POSITION:
#
#   AMAR, Alpna              -> "Amar ISMAEL", "Amar NADEEM"   (AMAR is their FORENAME)
#   KING, Stephen Anthony    -> "KING ROYAL TECHNOLOGIES CO. LTD"  (a Myanmar COMPANY)
#
# Two unrelated individuals and a company, reported against named directors of a listed
# defence group. The unit tests passed the whole time: they were built from the Babcock
# rows, where the surname genuinely did not appear at all. Only real data on a NEW
# subject exposed the residual class.

@pytest.mark.parametrize("title,surname,forenames,expected,why", [
    ("Amar ISMAEL", "AMAR", ["Alpna"], False,
     "the officer's surname is this person's FORENAME"),
    ("Amar NADEEM", "AMAR", ["Alpna"], False,
     "same collision, second unrelated individual"),
    ("KING ROYAL TECHNOLOGIES CO. LTD", "KING", ["Stephen", "Anthony"], False,
     "a company is never the individual being screened"),
    ("David Charles LOCKWOOD", "LOCKWOOD", ["David", "Charles"], True,
     "a genuine surname match must still survive"),
    ("COMISKEY, Aedamar", "COMISKEY", ["Aedamar"], True,
     "the register's other rendering, SURNAME first"),
    ("Kevin GREGORY (AKA CHARLES HENRY)", "LOCKWOOD", ["David"], False,
     "an alias parenthetical must not smuggle in a match"),
])
def test_rf3515_surname_must_be_in_the_surname_position(title, surname, forenames,
                                                        expected, why):
    keeps, _ = ch._disq_candidate_is_same_name(title, surname, forenames)
    assert keeps is expected, f"{title!r} vs {surname!r}: {why}"


def test_rf3515_an_entity_has_no_person_surname():
    assert ch._candidate_surname("DREX TECHNOLOGIES S.A.") == ""
    assert ch._candidate_surname("KING ROYAL TECHNOLOGIES CO. LTD") == ""
    assert ch._candidate_surname("David Charles LOCKWOOD") == "lockwood"


def test_rf3515_the_entity_check_runs_before_the_rendering_branch():
    """Both halves of this were wrong on my first cut, and the tests found them.

    ``S.A.`` strips to ``s.a`` and never equalled ``sa``, so a company parsed as a person
    surnamed "s.a"; and the legal-form check sat inside the no-comma branch only, so a
    comma in the row ("DREX TECHNOLOGIES, S.A.") skipped it entirely and yielded "drex".
    Deciding is-it-an-entity on the WHOLE row, before any branch, closes both.
    """
    assert ch._candidate_surname("DREX TECHNOLOGIES, S.A.") == ""
    assert ch._candidate_surname("SMITH HOLDINGS, LTD") == ""
    # ...without swallowing a person whose row merely contains a comma.
    assert ch._candidate_surname("SMITH, John") == "smith"
