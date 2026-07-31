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


def test_every_return_path_of_the_prompt_builder_carries_the_clock():
    """Three returns, and the compact one SHORT-CIRCUITS before the addenda.
    Missing any of them leaves that serving path with the original defect."""
    tree = ast.parse(_ENGINE.read_text(encoding="utf-8"))
    fn = next(
        n for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        and n.name == "_build_calibrated_system_prompt"
    )
    returns = [ast.unparse(r) for r in ast.walk(fn) if isinstance(r, ast.Return)]
    assert len(returns) >= 3, f"expected >=3 return paths, found {len(returns)}"
    missing = [r for r in returns if "_ambient_now_block" not in r]
    assert not missing, (
        f"these prompt return paths have no clock: {missing}. Every path that can "
        f"serve a chat must carry it, or that path keeps answering "
        f"'I don't have a live clock'."
    )


def test_the_clock_is_appended_after_the_length_cap():
    """THE TRAP. The system prompt is TAIL-trimmed to a cap (the base
    constitution is first and must survive), so a clock appended BEFORE the trim
    is the first thing cut on a long prompt — and the regression would be
    invisible: ARIA reverts to 'I can't know the time' on exactly the long,
    addendum-heavy conversations where the date matters most.
    """
    src = _ENGINE.read_text(encoding="utf-8")
    cap_idx = src.index("system prompt capped to %d chars")
    tail = src[cap_idx:cap_idx + 900]
    # R-F3590 — match the CALL, not its argument list. This pinned
    # `_ambient_now_block()` literally, so threading the speaker through
    # (`_ambient_now_block(speaker=speaker)`) broke it while the property it
    # guards — appended AFTER the cap — was completely untouched.
    assert re.search(r"return final \+ _ambient_now_block\(", tail), (
        "the clock is no longer appended after the truncation cap"
    )
    # and it must not be folded into `final` before the trim
    assert not re.search(r"final = _base_prompt \+ [^\n]*_ambient_now_block", src), (
        "the clock is being built into `final` before the cap — it will be trimmed away"
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
