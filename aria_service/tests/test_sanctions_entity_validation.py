"""Regression guard for Batch A sanctions API hygiene (F1, F2, F4, F18).

Live observation 2026-04-27 18:24:50–18:25:24: a single autonomous-task
cycle fired ~80 POSTs to OpenSanctions /match/default plus 13 GETs to
/search/default in 34 seconds. Root cause: tasks.yaml passes
search-query strings as the `entity:` parameter to deep_research, which
forwards them to sanctions.screen_with_aliases. Strings like:

  - "sanctions update OFAC SDN EU UN Security Council embargo 2026"
  - "Arkmurus weekly intelligence summary Angola Mozambique Guinea-Bissau Turkey Gulf procurement compliance 2026"
  - "ALADA's full legal identity, country of incorporation, or man"

are not entity names. They produce dozens of bogus variant queries each.

Fix: defensive _looks_like_entity_name() validator that rejects inputs
that don't have an entity-name shape BEFORE they hit OpenSanctions.
Heuristics: <= 100 chars, <= 8 words, no commas, no trailing year.
"""
from __future__ import annotations

import asyncio
from unittest.mock import patch

from aria_service.intel.sanctions import _looks_like_entity_name, screen_with_aliases


# ── _looks_like_entity_name positive cases ────────────────────────────────

def test_real_entity_names_pass():
    """Entity names from real DD work must all pass the validator."""
    valid = [
        "Northrop Grumman",
        "BAE Systems",
        "Lockheed Martin Corporation",
        "Hanwha Aerospace",
        "Rosoboronexport",
        "ALADA",
        "GAMI",
        "SIMPORTEX",
        "Wagner",
        "Vladimir Putin",
        "Edward Omane Boamah",
        "MBDA Missile Systems",
    ]
    for name in valid:
        assert _looks_like_entity_name(name), f"expected pass for: {name!r}"


# ── _looks_like_entity_name negative cases (the live bugs) ───────────────

def test_search_query_strings_rejected():
    """The exact garbage queries from the 2026-04-27 18:24:50 burst."""
    bad = [
        "sanctions update OFAC SDN EU UN Security Council embargo 2026",
        "Arkmurus weekly intelligence summary Angola Mozambique Guinea-Bissau Turkey Gulf procurement compliance 2026",
        "ALADA's full legal identity, country of incorporation, or man",
        "GAMI current leadership independently before any formal outre",
        "Without Direct SAM.gov Access",
        "NATO European defence procurement PESCO EDF force posture 2026",
        "North Africa Sahel Libya Algeria Morocco defence security Wagner Sahel 2026",
        "Angola defence procurement tender 2026 SIMPORTEX FAA",
        "new military platform announcement defence deal contract global 2026",
    ]
    for query in bad:
        assert not _looks_like_entity_name(query), f"expected reject for: {query!r}"


def test_empty_and_too_short_rejected():
    assert not _looks_like_entity_name("")
    assert not _looks_like_entity_name("a")
    assert not _looks_like_entity_name(" ")
    assert not _looks_like_entity_name(None)  # type: ignore[arg-type]


def test_too_long_rejected():
    assert not _looks_like_entity_name("x" * 101)


def test_too_many_words_rejected():
    """An entity name with 9+ words is almost certainly a description."""
    assert not _looks_like_entity_name("one two three four five six seven eight nine")


def test_comma_rejected():
    """Commas indicate sentences/lists, not entity names."""
    assert not _looks_like_entity_name("Acme Corp, formerly known as Apex Inc")


def test_question_or_exclamation_rejected():
    assert not _looks_like_entity_name("Is BAE Systems sanctioned?")
    assert not _looks_like_entity_name("Watch out!")


def test_trailing_year_rejected():
    """Trailing 4-digit year is a strong search-query signal."""
    assert not _looks_like_entity_name("Angola defence procurement 2026")
    assert not _looks_like_entity_name("Some Company 1999")


def test_year_NOT_in_middle_still_passes():
    """A year embedded in the middle (e.g. as an issue number or model
    designator) shouldn't auto-reject. The rule is trailing year only."""
    assert _looks_like_entity_name("F-35A Lightning II")


# ── screen_with_aliases short-circuits on bad input ───────────────────────

def test_screen_with_aliases_rejects_search_query():
    """The whole point of the validator: a bad input must not result in
    any OpenSanctions HTTP call. We patch the HTTP layer to raise on use
    so any real call fails the test loudly."""
    with patch("aria_service.intel.sanctions._opensanctions_match",
               side_effect=AssertionError("HTTP call should have been short-circuited")):
        result = asyncio.run(screen_with_aliases(
            "sanctions update OFAC SDN EU UN Security Council embargo 2026"
        ))
    assert result["error"] == "not_entity_shaped"
    assert result["matches"] == []
    assert result["blocked"] is False


def test_screen_with_aliases_filters_bad_aliases():
    """Aliases array can contain a mix of good and bad. The good ones
    should still be screened; the bad ones silently dropped."""
    real_calls: list = []

    async def fake_match(name, *a, **k):
        real_calls.append(name)
        return []

    async def fake_search(name, *a, **k):
        return []

    with patch("aria_service.intel.sanctions._opensanctions_match", side_effect=fake_match), \
         patch("aria_service.intel.sanctions._opensanctions_search", side_effect=fake_search):
        result = asyncio.run(screen_with_aliases(
            "Acme Corp",
            known_aliases=[
                "Acme",                                # good
                "this is not an entity name 2026",     # bad — trailing year
                "Acme Holdings, formerly Apex",        # bad — comma
                "Apex Inc",                            # good
            ],
        ))

    # Real calls — only the good aliases (variants of which are generated
    # by _generate_variants — at minimum the literal names should appear).
    called_names = " | ".join(real_calls)
    assert "Acme" in called_names
    assert "this is not an entity name" not in called_names
    assert "formerly Apex" not in called_names
