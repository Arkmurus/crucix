"""R-F614 — Dialogue-act response classifier.

Foundation test for Phase 1 of the ARIA dialogue spec (v2.1). Pins:
  - COMMAND wins on slash + imperatives
  - REPORT wins on explicit "give me a brief"
  - DIALOGUE is the default for short conversational turns
  - Tool-block context forces COMMAND
  - Length heuristic only triggers REPORT in absence of DIALOGUE hints
  - Empty / None inputs → DIALOGUE (defensive default)
"""
from __future__ import annotations

import pytest


# ══════════════════════════════════════════════════════════════════
# COMMAND classification
# ══════════════════════════════════════════════════════════════════

_COMMAND_PHRASES = (
    "/screen Lukoil",
    "/dd La Scala SpA",
    "/investigate modirumgespi.com",
    "DD on La Scala",
    "DD of Swisscraft Aviation",
    "dd for FAA Angola",
    "due diligence on Hikvision",
    "run a DD on La Scala",
    "Run DD on Lukoil",
    "kick a DD on the new buyer",
    "start due diligence on Modirum",
    "launch sanctions screen on Hikvision",
    "screen Lukoil",
    "investigate modirumgespi.com",
    "verify the Saudi entity",
    "check the OFSI list for La Scala",
    "deep_research La Scala",
    "deep research Modirum",
    "crawl modirumgespi.com",
    "extract_url https://example.com/doc",
)


@pytest.mark.parametrize("msg", _COMMAND_PHRASES)
def test_rf614_command_phrases_resolve_to_command(msg):
    from aria_service.intel.dialogue_router import (
        classify_dialogue_intent, DialogueIntent,
    )
    assert classify_dialogue_intent(msg) == DialogueIntent.COMMAND, (
        f"R-F614: {msg!r} must classify COMMAND"
    )


def test_rf614_tool_block_in_context_forces_command():
    """When a [TOOL: ...] block is already in the current turn, the
    user message is interpreting tool output — keep it COMMAND."""
    from aria_service.intel.dialogue_router import (
        classify_dialogue_intent, DialogueIntent,
    )
    # Even a short greeting-shaped message becomes COMMAND when a tool
    # block is in flight
    assert classify_dialogue_intent(
        "hi", has_tool_block=True,
    ) == DialogueIntent.COMMAND


# ══════════════════════════════════════════════════════════════════
# REPORT classification
# ══════════════════════════════════════════════════════════════════

_REPORT_PHRASES = (
    "give me a brief on La Scala",
    "give me a full brief on Modirum",
    "Give me the report on the Angola FAA opportunity",
    "send me a summary of Lukoil",
    "share an overview of the Bulgarian counterparty",
    "provide a comprehensive analysis of Modirum",
    "I need a full DD on La Scala",
    "I want a detailed report on Swisscraft",
    "write a brief on the Mozambique tender",
    "prepare a memo on the new buyer",
    "compose a write-up on Modirum",
    "draft a report covering Hikvision",
    "produce a profile on the Saudi entity",
    "full DD on the new counterparty",
    "Full due diligence on La Scala",
    "detailed analysis of the Bulgarian firm",
    "comprehensive report on Modirum",
    "thorough brief on the Mozambique tender",
)


@pytest.mark.parametrize("msg", _REPORT_PHRASES)
def test_rf614_report_phrases_resolve_to_report(msg):
    from aria_service.intel.dialogue_router import (
        classify_dialogue_intent, DialogueIntent,
    )
    assert classify_dialogue_intent(msg) == DialogueIntent.REPORT, (
        f"R-F614: {msg!r} must classify REPORT"
    )


def test_rf614_long_unstructured_message_classifies_report():
    """No explicit REPORT verb but >60 words and no DIALOGUE hint → REPORT.
    This is the catch-all for users pasting a long context block and
    asking ARIA to think about it."""
    from aria_service.intel.dialogue_router import (
        classify_dialogue_intent, DialogueIntent,
    )
    msg = " ".join(["alpha"] * 70)  # 70 words, no DIALOGUE hint
    assert classify_dialogue_intent(msg) == DialogueIntent.REPORT


def test_rf614_long_with_dialogue_hint_stays_dialogue():
    """Even long messages that START with a DIALOGUE marker (greeting /
    'what do you think') stay DIALOGUE — that's the user signalling
    conversational intent regardless of length."""
    from aria_service.intel.dialogue_router import (
        classify_dialogue_intent, DialogueIntent,
    )
    msg = "what do you think — " + " ".join(["beta"] * 70)
    assert classify_dialogue_intent(msg) == DialogueIntent.DIALOGUE


# ══════════════════════════════════════════════════════════════════
# DIALOGUE classification (the default)
# ══════════════════════════════════════════════════════════════════

_DIALOGUE_PHRASES = (
    "Hi Aria",
    "Hey",
    "Hello, are you there?",
    "Good morning",
    "Thanks!",
    "Thank you",
    "Cheers",
    "got it",
    "ok",
    "alright",
    "yes",
    "no",
    "sure",
    "maybe",
    "right",
    "correct",
    "yep",
    "nope",
    "What do you think?",
    "Any thoughts on this?",
    "Aria, how are you?",
    # Open-ended short questions
    "Did the OFSI fetch come back yet?",
    "Where did we land on Lukoil?",
    "Should I push the Angola brief to Friday?",
    "Could you double-check the Modirum date?",
)


@pytest.mark.parametrize("msg", _DIALOGUE_PHRASES)
def test_rf614_dialogue_phrases_resolve_to_dialogue(msg):
    from aria_service.intel.dialogue_router import (
        classify_dialogue_intent, DialogueIntent,
    )
    assert classify_dialogue_intent(msg) == DialogueIntent.DIALOGUE, (
        f"R-F614: {msg!r} must classify DIALOGUE"
    )


# ══════════════════════════════════════════════════════════════════
# Defensive handling
# ══════════════════════════════════════════════════════════════════

def test_rf614_empty_input_returns_dialogue():
    """None / "" / whitespace → DIALOGUE (safest default)."""
    from aria_service.intel.dialogue_router import (
        classify_dialogue_intent, DialogueIntent,
    )
    assert classify_dialogue_intent("") == DialogueIntent.DIALOGUE
    assert classify_dialogue_intent("   ") == DialogueIntent.DIALOGUE
    assert classify_dialogue_intent(None) == DialogueIntent.DIALOGUE  # type: ignore[arg-type]


def test_rf614_non_string_input_returns_dialogue():
    """Garbage types must not crash."""
    from aria_service.intel.dialogue_router import (
        classify_dialogue_intent, DialogueIntent,
    )
    assert classify_dialogue_intent(123) == DialogueIntent.DIALOGUE  # type: ignore[arg-type]
    assert classify_dialogue_intent([]) == DialogueIntent.DIALOGUE  # type: ignore[arg-type]


# ══════════════════════════════════════════════════════════════════
# Tie-break ordering — the priority chain
# ══════════════════════════════════════════════════════════════════

def test_rf614_command_beats_report_when_both_match():
    """A message containing both 'DD on X' AND 'give me a brief' should
    still classify COMMAND — actionable verbs win."""
    from aria_service.intel.dialogue_router import (
        classify_dialogue_intent, DialogueIntent,
    )
    assert classify_dialogue_intent(
        "DD on La Scala — give me a brief"
    ) == DialogueIntent.COMMAND


def test_rf614_command_beats_dialogue_for_short_imperatives():
    """Short imperatives like 'screen X' are COMMAND, not the literal-
    dialogue default — even though they're short."""
    from aria_service.intel.dialogue_router import (
        classify_dialogue_intent, DialogueIntent,
    )
    assert classify_dialogue_intent(
        "screen Lukoil"
    ) == DialogueIntent.COMMAND


# ══════════════════════════════════════════════════════════════════
# Enum values + helper surface
# ══════════════════════════════════════════════════════════════════

def test_rf614_enum_values_are_stable_strings():
    """Enum values must match string literals so downstream code can
    compare against `"dialogue"` / `"report"` / `"command"` if
    needed."""
    from aria_service.intel.dialogue_router import DialogueIntent
    assert DialogueIntent.DIALOGUE.value == "dialogue"
    assert DialogueIntent.REPORT.value == "report"
    assert DialogueIntent.COMMAND.value == "command"
    # Subclass of str — comparison with string literal works
    assert DialogueIntent.DIALOGUE == "dialogue"


def test_rf614_patterns_helper_exposes_all_regex():
    """patterns() must expose every regex source so future contributors
    can audit what triggers each bucket."""
    from aria_service.intel.dialogue_router import patterns
    p = patterns()
    for key in ("command", "report", "dialogue_hints"):
        assert key in p
        assert isinstance(p[key], str)
        assert p[key].strip()  # non-empty
