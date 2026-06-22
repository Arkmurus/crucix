"""R-F1775 — Capability test: comprehension prefix forces LLM to restate the question.

This test proves that _format_history_user_prompt now includes a dynamic
'UNDERSTOOD AS:' prefix from comprehension.analyse() that forces the LLM
to restate what the user asked before answering.

The test:
1. Calls _format_history_user_prompt with a UAE law question
2. Verifies the output contains the comprehension prefix with 'UNDERSTOOD AS'
3. Verifies the output contains the actual user message
4. Verifies the comprehension prefix comes BEFORE [Current message]
"""
from __future__ import annotations

from aria_service import aria_engine


def test_rf1775_comprehension_prefix_in_user_prompt():
    """_format_history_user_prompt must include the comprehension prefix
    with 'UNDERSTOOD AS' for a non-trivial UAE law question."""
    message = (
        "Under the UAE law, once a contract is cancelled and "
        "promising to return payments several times and you don't "
        "what can be done?"
    )
    lang_hint = ""
    context = "[RECALL CONTEXT]\nSome recalled facts about UAE contract law.\n"
    history = []

    result = aria_engine._format_history_user_prompt(
        history, lang_hint, message, context
    )

    # The comprehension prefix must be present
    assert "COMPREHENSION PASS" in result, (
        "R-F1775: comprehension prefix missing from user prompt"
    )
    assert "UNDERSTOOD AS" in result, (
        "R-F1775: 'UNDERSTOOD AS' missing from comprehension prefix"
    )

    # The comprehension prefix must come BEFORE [Current message]
    comp_idx = result.find("COMPREHENSION PASS")
    msg_idx = result.find("[Current message]")
    assert comp_idx >= 0, "COMPREHENSION PASS marker not found"
    assert msg_idx >= 0, "[Current message] marker not found"
    assert comp_idx < msg_idx, (
        "R-F1775: comprehension prefix must come BEFORE [Current message]"
    )

    # The actual user message must be present
    assert "UAE law" in result, "User message content missing from prompt"
    assert "contract is cancelled" in result, "User message content missing from prompt"


def test_rf1775_trivial_message_no_comprehension_prefix():
    """Trivial messages should NOT get the comprehension prefix."""
    message = "hello"
    lang_hint = ""
    context = ""
    history = []

    result = aria_engine._format_history_user_prompt(
        history, lang_hint, message, context
    )

    # Trivial messages should not have the comprehension prefix
    assert "COMPREHENSION PASS" not in result, (
        "R-F1775: trivial messages should not get comprehension prefix"
    )


def test_rf1775_comprehension_prefix_with_history():
    """The comprehension prefix must work correctly with conversation history."""
    message = "Under UAE law, what happens if a seller cancels a contract?"
    lang_hint = ""
    context = "[RECALL CONTEXT]\nSome context.\n"
    history = [
        {"role": "user", "content": "I have a contract with a UAE company."},
        {"role": "aria", "content": "I can help with UAE contract law questions."},
    ]

    result = aria_engine._format_history_user_prompt(
        history, lang_hint, message, context
    )

    # The comprehension prefix must be present
    assert "COMPREHENSION PASS" in result, (
        "R-F1775: comprehension prefix missing with history"
    )
    assert "UNDERSTOOD AS" in result, (
        "R-F1775: 'UNDERSTOOD AS' missing with history"
    )

    # The comprehension prefix must come BEFORE [Current message]
    comp_idx = result.find("COMPREHENSION PASS")
    msg_idx = result.find("[Current message]")
    assert comp_idx >= 0, "COMPREHENSION PASS marker not found"
    assert msg_idx >= 0, "[Current message] marker not found"
    assert comp_idx < msg_idx, (
        "R-F1775: comprehension prefix must come BEFORE [Current message] with history"
    )

    # Previous conversation must be present
    assert "Previous conversation" in result, "History missing from prompt"
    assert "UAE company" in result, "History content missing from prompt"


def test_rf1775_comprehension_prefix_with_tool():
    """When a tool fired AND there is history, the tool directive replaces
    the comprehension prefix. When there is no history, the comprehension
    prefix is still used (the tool-fired check only applies with history)."""
    message = (
        "[TOOL: deep_research]\n"
        "Research results about UAE contract law.\n"
        "Under the UAE law, once a contract is cancelled and "
        "promising to return payments several times and you don't "
        "what can be done?"
    )
    lang_hint = ""
    context = ""
    # With history, the tool-fired check applies
    history = [
        {"role": "user", "content": "Previous question."},
        {"role": "aria", "content": "Previous answer."},
    ]

    result = aria_engine._format_history_user_prompt(
        history, lang_hint, message, context
    )

    # Tool-fired messages with history should get the ANSWER SCOPE directive
    assert "ANSWER SCOPE" in result, (
        "R-F1775: tool-fired messages with history should get ANSWER SCOPE directive"
    )
    assert "BINDING" in result, (
        "R-F1775: tool-fired messages with history should get BINDING directive"
    )
    # The comprehension prefix should NOT be present (replaced by tool directive)
    assert "COMPREHENSION PASS" not in result, (
        "R-F1775: tool-fired messages should NOT get comprehension prefix"
    )
