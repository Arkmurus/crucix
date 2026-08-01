"""R-F3588 — ARIA refused a question the server could answer.

Live on WhatsApp, 2026-07-31 21:20 UK. Operator: "What time is it in the UK?"
ARIA: "I don't have a live clock in front of me, so I can't honestly give you the
exact current time in the UK without checking." — then a correct GMT/BST
explanation and an offer to "run a quick live time check".

That answer was HONEST AND USELESS, and the honesty layer was NOT the bug. An
LLM's only notion of "now" is its training cutoff, and neither ARIA_SYSTEM_PROMPT
nor ARIA_SYSTEM_PROMPT_COMPACT carried a date or a time. Given what she was told,
refusing was correct behaviour. The defect is that the server knows the time and
never told her.

The class this silently broke is bigger than clocks: what day is it, how old is
this filing, is this licence expired, how long until the deadline, is my sanctions
snapshot stale. Every one is a compliance question where the date is load-bearing.
"""

from __future__ import annotations

import ast
import asyncio
import pathlib
import re

import pytest

from aria_service.aria_engine import _ambient_now_block


_ENGINE = pathlib.Path(__file__).resolve().parents[1] / "aria_engine.py"


def test_the_block_states_a_real_utc_time():
    block = _ambient_now_block()
    assert "CURRENT CONTEXT" in block
    assert re.search(r"UTC now: \d{4}-\d{2}-\d{2} \d{2}:\d{2} UTC", block), block


def test_the_block_tells_her_she_has_a_clock():
    """The prompt must contradict the exact refusal that was observed, or the
    model keeps defaulting to 'I can't know that' under the never-fabricate rule."""
    block = _ambient_now_block().lower()
    assert "you do have a clock" in block
    assert "do not say you cannot know" in block
    assert "do not offer to" in block, "she offered to 'run a quick live time check'"


def test_the_block_demands_the_answer_first():
    """The second half of the complaint: the reply buried a non-answer in a wall
    of text. Simple factual questions get one line."""
    block = _ambient_now_block().lower()
    assert "one line" in block
    assert "answer first" in block


def test_missing_tzdata_degrades_honestly_instead_of_raising(monkeypatch):
    """zoneinfo is stdlib but the IANA database is a SYSTEM package, and slim
    images often omit it. An exception here would break EVERY chat, not just
    time questions — prompt construction is on the hot path."""
    import builtins

    real_import = builtins.__import__

    def _no_zoneinfo(name, *a, **k):
        if name == "zoneinfo":
            raise ImportError("no tzdata")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", _no_zoneinfo)
    block = _ambient_now_block()
    assert "UTC now:" in block, "UTC must still be stated when tzdata is absent"
    assert "BST" in block, "the fallback must still let her derive UK time"


# ── R-F3630 — THESE TWO ARE NOW BEHAVIOURAL ─────────────────────────────────
#
# Both guards below used to assert on SOURCE TEXT: one AST-matched the literal
# name `_ambient_now_block` inside every `return`, the other indexed a log
# string and regex'd the exact expression `return final + _ambient_now_block(`.
#
# They have now broken TWICE on refactors that left the property untouched —
# R-F3590 threading `speaker=` through (see the note it left in this file), and
# R-F3630 reserving the appendix so it is computed into a local first. Twice is
# the signal: they were pinning the WORDING, not the property.
#
# A source grep also cannot see the thing that actually matters here — whether
# the clock SURVIVES the trim. Building the prompt can. So both are driven
# through the real builder now, including the over-cap path.
_CLOCK_MARKERS = ("UTC now:", "CURRENT CONTEXT")

_DOC_MSG = ("Please review this agreement. [ATTACHED DOCUMENT: c.docx]\n"
            "body text\n[END ATTACHED DOCUMENT]")
_PLAIN_MSG = "Give me an export-control assessment of this deal"


def _has_clock(prompt: str) -> bool:
    return all(m in prompt for m in _CLOCK_MARKERS)


def test_every_return_path_of_the_prompt_builder_carries_the_clock(monkeypatch):
    """Drive each SERVING path and assert the clock is really in the prompt.

    The compact/doc path short-circuits before the addenda, so it is a distinct
    return; missing the clock on any of them leaves that path answering
    "I don't have a live clock".
    """
    import aria_service.aria_engine as ae

    async def _cal():
        return {}
    monkeypatch.setattr(ae, "_get_cached_calibration", _cal)

    for label, msg in (("document mode", _DOC_MSG), ("plain chat", _PLAIN_MSG)):
        prompt = asyncio.run(ae._build_calibrated_system_prompt(msg))
        assert _has_clock(prompt), f"{label} prompt reached the model with no clock"
        assert prompt.rstrip().endswith(
            ae._ambient_now_block(speaker=None).rstrip()[-60:]
        ), f"{label}: the clock must be the TAIL of the prompt"


def test_the_clock_survives_the_length_cap(monkeypatch):
    """THE TRAP, now proven by construction rather than by reading the source.

    The prompt is TAIL-trimmed (the base constitution is first and must survive),
    so a clock folded in before the trim is the first thing cut — and the
    regression would be invisible: ARIA reverts to "I can't know the time" on
    exactly the long, addendum-heavy conversations where the date matters most.

    Forces an addendum far larger than the cap and asserts the clock is STILL
    there afterwards.
    """
    import aria_service.aria_engine as ae
    import aria_service.intel.contract_review_principles as _cr

    async def _cal():
        return {}
    monkeypatch.setattr(ae, "_get_cached_calibration", _cal)
    monkeypatch.setattr(_cr, "detect_review_intent", lambda m: True)
    monkeypatch.setattr(_cr, "addendum", lambda: "Y" * 400_000)

    prompt = asyncio.run(ae._build_calibrated_system_prompt(_DOC_MSG))

    assert "truncated to preserve context-window room" in prompt, (
        "this test is only meaningful on the TRIMMED path — the cap did not fire"
    )
    assert _has_clock(prompt), (
        "the clock was trimmed away by the length cap — it must be appended after it"
    )
    # R-F3630 — and the trim must now bound the WHOLE prompt, appendix included.
    assert len(prompt) <= 20_000, (
        f"doc-mode prompt is {len(prompt)} chars against a 20,000 cap — the "
        f"post-cap appendix is escaping the bound again"
    )


def test_the_clock_is_at_the_end_not_the_front():
    """Prompt caching keys on a stable PREFIX. A timestamp at the top would bust
    the cache on every request and multiply input-token spend against the $300/mo
    cap (§17)."""
    src = _ENGINE.read_text(encoding="utf-8")
    for bad in ("_ambient_now_block() + ARIA_SYSTEM_PROMPT",
                "_ambient_now_block() + _base_prompt",
                "_ambient_now_block() + final"):
        assert bad not in src, f"clock prepended ({bad}) — this busts prompt caching every request"


def test_the_prompts_themselves_still_have_no_hardcoded_date():
    """A date baked into the prompt text would be worse than none: it goes stale
    silently and she would state it with confidence."""
    src = _ENGINE.read_text(encoding="utf-8")
    start = src.index("ARIA_SYSTEM_PROMPT = ")
    body = src[start:start + 8000]
    assert not re.search(r"20\d\d-\d\d-\d\d", body), (
        "a literal date is hardcoded in the system prompt — it will go stale and "
        "be asserted confidently"
    )


# ── The wider class: she refused things she knows, and engaged badly ─────────


def test_the_never_fabricate_rule_is_SCOPED_not_weakened():
    """THE DELICATE ONE.

    The compact prompt's rule 3 says "'I cannot verify X' is ALWAYS the better
    answer". That absolute is what made refusing the time correct behaviour — the
    strongest rule overrode rule 1 (ANSWER THE QUESTION) and rule 8 (short answers
    for short questions).

    The fix scopes never-fabricate to claims about the OUTSIDE WORLD. It must NOT
    soften it: zero fabrication is the north star and the product's whole moat.
    """
    block = _ambient_now_block()
    assert "NEVER-FABRICATE governs claims about the OUTSIDE WORLD" in block
    for external in ("registry numbers", "sources", "quotes", "contract values"):
        assert external in block, f"{external} must stay explicitly un-fabricatable"
    assert "only when you genuinely cannot" in block

    # And the binding rule itself is untouched in the prompt text.
    src = _ENGINE.read_text(encoding="utf-8")
    assert "NEVER FABRICATE — no invented registry numbers" in src, (
        "the binding never-fabricate rule has been edited — it must not be"
    )


def test_she_can_answer_what_can_you_do():
    """'What can you help with' was unanswerable: nothing in the prompt listed
    her capabilities, so she could only guess or hedge."""
    block = _ambient_now_block()
    assert "WHAT YOU CAN DO" in block
    for capability in ("due diligence", "Sanctions", "Export-control", "documents"):
        assert capability in block, f"{capability} missing from the capability inventory"


def test_the_engagement_rules_target_the_observed_failure():
    """The reply that prompted this buried a non-answer in five lines and ended
    with 'Want me to run a quick live time check? Just say the word.'"""
    block = _ambient_now_block()
    assert "Answer first" in block
    assert "one-line answer" in block
    assert "Never offer to do a thing you can just do" in block, (
        "the offer-instead-of-doing pattern is the exact observed failure"
    )
    assert "colleague, not a search box" in block, (
        "relationship-building was the operator's ask, not just correctness"
    )


def test_the_block_stays_cheap():
    """It is appended to EVERY request, so its size is recurring spend against
    the $300/mo cap (§17). A capability block that quietly grows to 10K chars
    would cost more than the answers it improves."""
    size = len(_ambient_now_block())
    assert size < 3000, f"ambient block is {size} chars — trim it or justify the cost"


def test_the_capability_list_does_not_promise_what_she_lacks():
    """A surface may not describe a capability the code does not have. Each claim
    here is checked against something real in the tree."""
    import pathlib as _p
    root = _p.Path(__file__).resolve().parents[2]
    block = _ambient_now_block()
    if "Export-control" in block:
        assert (root / "aria_service" / "intel" / "sources" / "eccn_lookup.py").exists(), (
            "the block claims export-control classification but eccn_lookup is gone"
        )
    if "Sanctions" in block:
        assert (root / "aria_service" / "intel" / "country_sanctions.py").exists()
    if "voice notes" in block:
        listener = (root / "services" / "wa-listener" / "aria_wa_listener.mjs").read_text(
            encoding="utf-8", errors="replace")
        assert "audioMessage" in listener, "voice-note handling claimed but not present"
