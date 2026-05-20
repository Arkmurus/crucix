"""R-F764 — system prompt Clause 37: fire tools, don't ask permission.

LIVE BUG captured by other-agent 5-turn transcript review 2026-05-20:

  Turn 3 end: "Want me to run a web search on Efdal Colpan now?"
  Turn 4 end: "I can search Turkish MERSIS for company directorships,
              Estonian news archives for the name, and adverse media
              queries in Turkish and English. Want me to run that?"

User had EXPLICITLY asked an OSINT question on a named person tied
to a foreign defence procurement body. ARIA wasted 2 turns asking
permission for tools that already exist in the R-F603 inventory.
Violates Rule Zero ("ALWAYS find a path").

R-F764 adds Clause 37 to ARIA_SYSTEM_PROMPT mandating that when the
user asks an investigative question on a named entity AND a relevant
tool exists, ARIA must fire the tool in the SAME TURN — not ask
permission. Documents the trigger patterns, the banned anti-pattern
phrases, the decision table, and the relationship to Clause 21
(comprehension gate).

Source-level test confirms the clause is present + carries the
right hard-banned phrases so a future system-prompt edit can't
silently strip the rule.
"""
from __future__ import annotations

from pathlib import Path


def _engine_src() -> str:
    p = Path(__file__).resolve().parents[1] / "aria_engine.py"
    return p.read_text(encoding="utf-8")


def test_rf764_clause_37_header_present():
    src = _engine_src()
    assert "37. FIRE TOOLS, DON'T ASK PERMISSION (R-F764" in src, (
        "R-F764 regression: Clause 37 header missing from "
        "ARIA_SYSTEM_PROMPT. Restore the rule before next deploy."
    )


def test_rf764_clause_37_within_aria_system_prompt():
    """The clause must sit INSIDE the ARIA_SYSTEM_PROMPT triple-quoted
    string, not after it (otherwise the LLM never sees it)."""
    src = _engine_src()
    prompt_start = src.find('ARIA_SYSTEM_PROMPT = """')
    prompt_end = src.find('"""', prompt_start + 30)  # first closing """
    assert prompt_start > 0 and prompt_end > prompt_start
    prompt_body = src[prompt_start:prompt_end]
    assert "37. FIRE TOOLS" in prompt_body, (
        "R-F764: Clause 37 leaked outside the ARIA_SYSTEM_PROMPT triple-"
        "quoted string. Move it inside before the closing triple-quote "
        "or the LLM never receives the directive."
    )


def test_rf764_banned_phrases_listed():
    """The clause must explicitly list the anti-pattern phrases so the
    LLM has a concrete checklist to compare against before composing
    a turn-end."""
    src = _engine_src()
    for phrase in (
        '"Want me to run a web search on',
        '"I can search <Y> — want me to run that?"',
        '"Shall I dig deeper / continue / proceed?"',
    ):
        assert phrase in src, (
            f"R-F764: banned anti-pattern phrase {phrase!r} missing from "
            f"Clause 37. Without the explicit list the LLM may not "
            f"recognise the variants it should avoid."
        )


def test_rf764_trigger_patterns_listed():
    """The clause must enumerate the trigger patterns that REQUIRE
    immediate tool-fire — otherwise an over-cautious LLM may default
    to asking permission on borderline cases."""
    src = _engine_src()
    for pattern in (
        '"Investigate <name>"',
        '"Run DD on <X>"',
        '"Screen <X>"',
        '"Sanctions check <X>"',
    ):
        assert pattern in src, (
            f"R-F764: trigger pattern {pattern!r} missing from Clause 37"
        )


def test_rf764_rule_zero_anchor_present():
    """The clause must explicitly anchor to Rule Zero ('ALWAYS find a
    path') so the LLM doesn't treat Clause 37 as standalone advice."""
    src = _engine_src()
    assert 'Rule Zero ("ALWAYS find a path")' in src, (
        "R-F764: Rule Zero anchor missing from Clause 37. The whole "
        "point is that asking-permission is the failure mode Rule Zero "
        "exists to prevent."
    )


def test_rf764_clause_21_relationship_documented():
    """Clause 37 interacts with Clause 21 (comprehension gate). The
    relationship must be made explicit so the LLM doesn't see them as
    contradictory and pick the safer/passive interpretation."""
    src = _engine_src()
    assert "Clause 21" in src and "comprehension gate" in src.lower(), (
        "R-F764: Clause 37 doesn't reference Clause 21 (comprehension "
        "gate). Without the explicit relationship, the LLM may use "
        "Clause 21's clarification path to justify permission-asking — "
        "the opposite of what Clause 37 mandates."
    )


def test_rf764_past_incident_anchor():
    """Audit-trail discipline (matches Clauses 11/12/13/14): each new
    constitutional rule should name the past-incident pattern it closes."""
    src = _engine_src()
    assert "Efdal Colpan" in src and "Turkish MERSIS" in src, (
        "R-F764: past-incident anchor (Efdal Colpan / Turkish MERSIS) "
        "missing from Clause 37. Without the anchor a future contributor "
        "won't know why the rule exists."
    )
