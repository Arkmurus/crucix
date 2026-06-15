"""R-F1589 — demote history to summaries when a tool fired for the current msg.

Live WhatsApp failure 2026-06-15: the user sent "Aria, do a full DD on
https://www.deltaguard.org/en/" (a clear NEW request that correctly routed to
the DD/research path), but ARIA returned the PRIOR turn's self gap-analysis
almost verbatim — a long gap-analysis history out-weighed the current
tool-grounded request, and the R-F1384 comprehension directive alone didn't
hold against a full prior answer sitting in-context.

R-F1589: in the shared prompt builder (_format_history_user_prompt — used by
both aria_chat and aria_chat_stream, §13), when a tool already ran for the
current message (its [TOOL: ...] output is embedded in `message`), demote ALL
history to one-line summaries so no prior full answer can be lifted wholesale.

These tests assert the PROMPT STRUCTURE the builder produces (the real changed
code path). NOTE: this is a structural mitigation — confirming the live model
behaviour requires re-testing the deltaguard DD on WhatsApp.
"""
from __future__ import annotations

from aria_service.aria_engine import _format_history_user_prompt


# A long, salient prior answer (the kind that bled through on WA).
_PRIOR_GAP_ANALYSIS = (
    "ARIA SYSTEM GAP ANALYSIS — Build R-F401+. WHAT IS WORKING: knowledge base "
    "90,001 facts, 55,513 ledger signals. WHAT IS NOT WORKING: SUPERVISED mode, "
    "autonomous engine status unknown, document reading failures, context drift. "
    "GAPS: component-level health, error logs, latency metrics. " * 6  # make it long
)

_HISTORY = [
    {"role": "user", "content": "do a full gap analysis of your system"},
    {"role": "assistant", "content": _PRIOR_GAP_ANALYSIS},
]

# What routes/aria.py embeds in the message after a tool runs.
_DD_MESSAGE_WITH_TOOL = (
    "Aria, do a full DD on https://www.deltaguard.org/en/\n\n"
    "[I have already run the appropriate tool on your request. Use its output:]\n"
    "[TOOL: dd_orchestrate]\nDelta Guard Ltd — Bulgarian security firm ..."
)


def test_rf1589_tool_fired_drops_history_entirely():
    """R-F1590 (escalated R-F1589): when a tool fired, history is DROPPED
    entirely — not summarised — so there is zero prior-answer material in
    context to bleed from (deterministic, not reliant on the model heeding a
    directive it already overrode once)."""
    prompt = _format_history_user_prompt(_HISTORY, "", _DD_MESSAGE_WITH_TOOL, "")
    # NONE of the prior gap-analysis may survive — not even a snippet.
    assert "knowledge base" not in prompt, (
        "R-F1590: prior answer content leaked — history not fully dropped."
    )
    assert "ledger signals" not in prompt
    assert "latency metrics" not in prompt
    # The binding answer-scope directive is present.
    assert "[ANSWER SCOPE" in prompt
    assert "NO conversation history" in prompt
    # The current tool-grounded message + its output are authoritative.
    assert "[Current message]" in prompt
    assert "dd_orchestrate" in prompt
    assert "deltaguard.org" in prompt


def test_rf1589_no_tool_keeps_full_history():
    """Regression: a normal multi-turn chat (no tool) must keep full history so
    legitimate follow-ups still have context."""
    plain_message = "and what about its directors?"
    prompt = _format_history_user_prompt(_HISTORY, "", plain_message, "")
    # Full prior answer retained (not summary-demoted) for continuity.
    assert "55,513 ledger signals" in prompt
    assert "brief context only" not in prompt


def test_rf1589_tool_fired_with_no_history_is_unaffected():
    prompt = _format_history_user_prompt([], "", _DD_MESSAGE_WITH_TOOL, "")
    assert "[Current message]" in prompt
    assert "dd_orchestrate" in prompt
