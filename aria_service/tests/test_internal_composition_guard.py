"""Regression tests for the morning-digest misrouting bug observed 2026-04-21.

The bug: "Generate today's Arkmurus morning intelligence digest. Be concise
— max 15 lines. Include: 1. OVERNIGHT ALERTS — ... 2. MARKET WATCH — ..."
matched `_PROCUREMENT_KW` + `_TIME_SENSITIVE_KW` and fired deep_research
with the *entire prompt-with-sections* as the entity. The 4 parallel
web_search angles then searched for the prompt itself, returning 4×
Brave 402 Payment Required + 4× DDG noise, and DeepSeek had to compose
the digest from semantic garbage instead of the in-memory sweep data.

Fix: `_looks_like_internal_composition()` detects "compose me a digest"
prompts and short-circuits the intent classifier to the pure-LLM path,
which already has sweep + intel_ledger + memory context injected by
aria_engine.

These tests pin the discriminator logic so the guard can never regress.
"""
from __future__ import annotations

import pytest

from aria_service.routes.aria import _looks_like_internal_composition


# ── Must-trigger cases ────────────────────────────────────────────────

def test_morning_digest_with_sections_triggers_guard():
    """The original 2026-04-21 incident prompt must short-circuit."""
    msg = (
        "Generate today's Arkmurus morning intelligence digest. "
        "Be concise — max 15 lines.\n\nInclude:\n"
        "1. OVERNIGHT ALERTS — any sanctions, conflicts, or procurement signals\n"
        "2. MARKET WATCH — key developments in priority markets\n"
        "3. TODAY'S CULTURAL CALENDAR\n"
        "4. RE-ENGAGEMENT — contacts that need attention (45+ days cold)\n"
        "5. ACTION ITEMS — 2-3 specific things the team should do today\n\n"
        "Format for Telegram (use bold **text**, bullet points)."
    )
    assert _looks_like_internal_composition(msg)


def test_daily_briefing_format_directive_triggers_guard():
    msg = "Aria, produce a daily briefing for the team. Format for Telegram, max 10 lines."
    assert _looks_like_internal_composition(msg)


def test_compose_verb_plus_noun_triggers_guard():
    msg = "Please generate a digest with the latest pipeline status."
    assert _looks_like_internal_composition(msg)


def test_create_report_with_numbered_sections_triggers_guard():
    """Composition verb (create) + ≥2 numbered sections = 2 signals."""
    msg = (
        "Aria, create a report covering:\n"
        "1. Pipeline\n"
        "2. Risk\n"
        "3. Opportunities\n"
    )
    assert _looks_like_internal_composition(msg)


# ── Must-NOT-trigger cases — real research/DD/chat must still classify ──

def test_normal_research_question_does_not_trigger_guard():
    """A plain factual question must NOT be treated as internal composition."""
    msg = "Aria, who is the current defence minister of Angola?"
    assert not _looks_like_internal_composition(msg)


def test_procurement_tender_lookup_does_not_trigger_guard():
    """A procurement tender question MUST still reach the procurement classifier."""
    msg = "What tenders are open in Angola in 2026?"
    assert not _looks_like_internal_composition(msg)


def test_single_sentence_update_request_does_not_trigger():
    """'Give me an update' alone is one signal (verb only) — must flow through.

    "update" is deliberately NOT in the compose-noun regex because it's
    common in normal chat ("give me an update on Serban") and we don't
    want to short-circuit those into the pure-LLM path — they might
    need deep_research or DD. So this message has only the verb signal
    (1 of 4) and must not trigger.
    """
    msg = "Give me an update."
    assert not _looks_like_internal_composition(msg)


def test_dd_request_does_not_trigger_guard():
    """A due-diligence request must NOT be treated as internal composition."""
    msg = "Aria, run a deep DD on https://example.com/"
    assert not _looks_like_internal_composition(msg)


def test_short_chat_no_composition_intent_does_not_trigger():
    msg = "hello aria"
    assert not _looks_like_internal_composition(msg)
