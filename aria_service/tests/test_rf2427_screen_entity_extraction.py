"""R-F2427 (Blocker 1) — capability test: screen/compliance entity extraction
must pull the REAL entity out of "Screen X for sanctions" / "Run a sanctions
screen on X" phrasings, never the trailing intent keyword.

Root cause (proven live, a99e smoke 2026-07-05):
  "Screen Tactical Missiles Corporation KTRV for sanctions" made the screen
  tool screen the literal word "sanctions" (not "KTRV"). The R-F1749 compliance
  NLU (`_COMPLIANCE_FOR_RE`) matched the trailing "for sanctions" clause and
  used "sanctions" as the entity; the plain has_screen path left the trailing
  keyword attached to the entity. Both grab whatever follows the last
  for/on/of/against, so a trailing PURPOSE clause is mistaken for the entity.

Fix (`_INTENT_PURPOSE_TAIL_RE` in `_detect_tool_intent`): strip a trailing
"for <intent-noun>" purpose clause before entity extraction, leaving the real
entity in place. §14 never-false-clean is preserved — a bare "screen for
sanctions" with no entity honestly falls through to no-entity (no fabricated
subject).

These tests drive the REAL `_detect_tool_intent` (the entry point the chat /
WA / TG handlers call), not a proxy classifier.
"""
from __future__ import annotations

import pytest

from aria_service.routes.aria import _detect_tool_intent as detect


SCREEN_TOOLS = {"screen", "fuzzy_sanctions"}


@pytest.mark.parametrize(
    "message,expected_entity",
    [
        ("Screen Tactical Missiles Corporation KTRV for sanctions",
         "Tactical Missiles Corporation KTRV"),
        ("Run a sanctions screen on Kalashnikov Concern", "Kalashnikov Concern"),
        ("sanctions screen on KTRV", "KTRV"),
        ("Check export-control / compliance for Rosoboronexport", "Rosoboronexport"),
        ("Screen KTRV for a sanctions check", "KTRV"),
        ("/screen KTRV for sanctions", "KTRV"),
        ("compliance check on Sberbank for sanctions exposure", "Sberbank"),
    ],
)
def test_entity_is_the_subject_not_the_intent_keyword(message, expected_entity):
    intent = detect(message)
    assert intent is not None, f"{message!r} produced no tool intent"
    entity = (intent.get("entity") or "").strip()
    # The literal intent keyword must NEVER be the entity.
    assert entity.lower() not in (
        "sanctions", "sanction", "compliance", "adverse media",
        "sanctions check", "a sanctions check",
    ), f"{message!r} extracted the intent keyword as the entity: {entity!r}"
    assert entity == expected_entity, (
        f"{message!r} => entity {entity!r}, expected {expected_entity!r}"
    )


def test_screen_phrasing_still_routes_to_a_screen_or_research_tool():
    # "Screen X for sanctions" must reach a compliance/screen path (not None).
    intent = detect("Screen Tactical Missiles Corporation KTRV for sanctions")
    assert intent is not None
    assert intent["tool"] in SCREEN_TOOLS


def test_legit_entity_ending_in_intent_noun_not_stripped():
    # "Compliance" is part of the company NAME here (not a trailing purpose
    # clause after for/against), so it must survive.
    intent = detect("Screen Compliance Solutions Inc")
    assert intent is not None
    assert intent.get("entity") == "Compliance Solutions Inc"


def test_bare_screen_no_entity_falls_through_honestly():
    # No entity present → never fabricate one (§14). Either no tool, or a
    # screen tool with an empty/too-short entity is unacceptable; assert it did
    # NOT screen the word "sanctions".
    intent = detect("screen for sanctions")
    if intent and intent.get("tool") in SCREEN_TOOLS:
        assert (intent.get("entity") or "").lower() not in ("sanctions", "sanction")


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
