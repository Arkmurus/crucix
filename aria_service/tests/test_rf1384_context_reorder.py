"""R-F1384 — context-assembly reorder: context BEFORE current message.

Live failure (2026-06-06 20:07 UTC): the NDA review answered the WRONG
question — the model latched onto a DO-228 spare-parts query from recalled
context (live_intel/recall) instead of the user's current NDA review request.

Root cause: _format_history_user_prompt built
  '[Previous conversation]...[Current message] User: {message}{context}'
where context (recalled intel, live intel, RAG) was APPENDED AFTER the
current message. The model's "what is the specific question" instruction
latched onto the LAST thing it read (old recalled material with question-
shaped text), not the current message.

Fix:
1. Reorder all 3 return branches so context comes BEFORE [Current message]
2. Add a [COMPREHENSION DIRECTIVE] block right before [Current message]
   instructing the model to answer ONLY the current message
3. Honest footer: when grounded_rate=0.0 and sources=0, say "Reviewing
   attached document" instead of "Grounded in: attached document"

This test verifies the reorder + directive in all three branches.
"""
from __future__ import annotations

import pytest

from aria_service.aria_engine import _format_history_user_prompt


# A context block that looks like recalled intel with a question-shaped text
# — the exact hijacker pattern from the NDA incident.
HIJACKER_CONTEXT = (
    "\n\n[LIVE INTELLIGENCE]\n"
    "DO-228 aircraft spare parts: RUAG Aviation supplies Dornier 228 "
    "components. The Nigerian Air Force operates 5 DO-228s. "
    "Question: what are the spare parts lead times for the Nigerian Air Force?\n\n"
    "[MEMORY RECALL]\n"
    "Earlier query about DO-228 spare parts for Nigeria returned "
    "RUAG lead times of 12-16 weeks.\n"
)

NDA_MESSAGE = "review this NDA for confidentiality and non-circumvention clauses"


# ── Empty history (no prior turns) ──────────────────────────────────────


def test_empty_history_context_before_current():
    """Empty history: context must come before [Current message]."""
    result = _format_history_user_prompt([], "", NDA_MESSAGE, HIJACKER_CONTEXT)

    assert "[Current message]" in result
    assert "User: review this NDA" in result

    # Context must appear BEFORE [Current message]
    ctx_pos = result.find("DO-228 aircraft spare parts")
    cur_pos = result.find("[Current message]")
    assert ctx_pos < cur_pos, (
        f"Context (at {ctx_pos}) must appear before [Current message] (at {cur_pos})"
    )

    # Comprehension prefix must be present (R-F1775: dynamic 'UNDERSTOOD AS' block)
    assert "COMPREHENSION PASS" in result or "COMPREHENSION DIRECTIVE" in result, (
        "Comprehension prefix missing from user prompt"
    )
    assert "UNDERSTOOD AS" in result or "Answer ONLY the question" in result, (
        "Comprehension directive missing from user prompt"
    )

    # The NDA question must be the last user-facing text
    last_user = result.rfind("User: review this NDA")
    assert last_user > ctx_pos, "NDA question must be after context"


def test_empty_history_nda_is_last():
    """The NDA question must be the LAST user message the model reads."""
    result = _format_history_user_prompt([], "", NDA_MESSAGE, HIJACKER_CONTEXT)

    last_user_idx = result.rfind("User: review this NDA")
    last_do228_idx = result.rfind("DO-228")

    assert last_user_idx > last_do228_idx, (
        f"NDA question (at {last_user_idx}) must be after DO-228 context "
        f"(at {last_do228_idx})"
    )


# ── Short history (<20 exchanges) ───────────────────────────────────────


def test_short_history_context_before_current():
    """Short history: context before [Current message], directive present."""
    history = [
        {"role": "user", "content": "what do you know about DO-228?"},
        {"role": "assistant", "content": "The DO-228 is a twin-turboprop utility aircraft."},
    ]
    result = _format_history_user_prompt(history, "", NDA_MESSAGE, HIJACKER_CONTEXT)

    assert "[Current message]" in result
    assert "User: review this NDA" in result

    ctx_pos = result.find("DO-228 aircraft spare parts")
    cur_pos = result.find("[Current message]")
    assert ctx_pos < cur_pos, (
        f"Context (at {ctx_pos}) must appear before [Current message] (at {cur_pos})"
    )

    # Comprehension prefix must be present (R-F1775: dynamic 'UNDERSTOOD AS' block)
    assert "COMPREHENSION PASS" in result or "COMPREHENSION DIRECTIVE" in result, (
        "Comprehension prefix missing from user prompt"
    )
    assert "UNDERSTOOD AS" in result or "Answer ONLY the question" in result, (
        "Comprehension directive missing from user prompt"
    )

    # History must still be present
    assert "what do you know about DO-228?" in result


# ── Long history (>20 exchanges, triggers summary mode) ─────────────────


def test_long_history_context_before_current():
    """Long history (>20 exchanges): context before [Current message]."""
    history = []
    for i in range(15):
        history.append({"role": "user", "content": f"question {i} about various topics"})
        history.append({"role": "assistant", "content": f"answer {i} with some detail"})

    result = _format_history_user_prompt(history, "", NDA_MESSAGE, HIJACKER_CONTEXT)

    assert "[Current message]" in result
    assert "User: review this NDA" in result

    ctx_pos = result.find("DO-228 aircraft spare parts")
    cur_pos = result.find("[Current message]")
    assert ctx_pos < cur_pos, (
        f"Context (at {ctx_pos}) must appear before [Current message] (at {cur_pos})"
    )

    # Comprehension prefix must be present (R-F1775: dynamic 'UNDERSTOOD AS' block)
    assert "COMPREHENSION PASS" in result or "COMPREHENSION DIRECTIVE" in result, (
        "Comprehension prefix missing from user prompt"
    )


def test_very_long_history_triggers_summary():
    """Very long history (>20 exchanges): summary mode, context before current."""
    history = []
    for i in range(12):
        history.append({"role": "user", "content": f"question {i}"})
        history.append({"role": "assistant", "content": f"answer {i}"})

    result = _format_history_user_prompt(history, "", NDA_MESSAGE, HIJACKER_CONTEXT)

    assert "[Current message]" in result
    assert "User: review this NDA" in result

    ctx_pos = result.find("DO-228 aircraft spare parts")
    cur_pos = result.find("[Current message]")
    assert ctx_pos < cur_pos, (
        f"Context (at {ctx_pos}) must appear before [Current message] (at {cur_pos})"
    )

    # Comprehension prefix must be present (R-F1775: dynamic 'UNDERSTOOD AS' block)
    assert "COMPREHENSION PASS" in result or "COMPREHENSION DIRECTIVE" in result, (
        "Comprehension prefix missing from user prompt"
    )


# ── Context with no history (the exact NDA incident shape) ──────────────


def test_nda_context_hijack_prevented():
    """The exact NDA incident shape: context with DO-228 question-shaped text
    must NOT be the last thing the model reads — the NDA question must be."""
    result = _format_history_user_prompt([], "", NDA_MESSAGE, HIJACKER_CONTEXT)

    # The last occurrence of 'User:' should be the NDA question
    last_user_idx = result.rfind("User: review this NDA")
    last_do228_idx = result.rfind("DO-228")

    assert last_user_idx > last_do228_idx, (
        f"NDA question (at {last_user_idx}) must be after DO-228 context "
        f"(at {last_do228_idx})"
    )

    # The comprehension prefix must be between context and current message
    # (R-F1775: now uses 'COMPREHENSION PASS' from comprehension.build_prefix())
    directive_pos = result.find("COMPREHENSION PASS")
    if directive_pos == -1:
        directive_pos = result.find("COMPREHENSION DIRECTIVE")
    cur_pos = result.find("[Current message]")
    assert directive_pos >= 0, "Comprehension prefix not found"
    assert directive_pos < cur_pos, (
        f"Comprehension prefix (at {directive_pos}) must be before "
        f"[Current message] (at {cur_pos})"
    )
