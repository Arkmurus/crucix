"""R-F863 — scratchpad strip must tolerate malformed close tags.

Live incident (2026-05-25 weekly brief): the LLM emitted
`<scratchpad>…</scratchmark>` — a wrong close tag. The strict `</scratchpad>`
regex matched nothing, so the ENTIRE chain-of-thought leaked into the
client-facing email (and the leaked copy was cached + re-served). A one-char
tag typo must never expose ARIA's internal reasoning.
"""
from __future__ import annotations

from aria_service.intel import scratchpad


def test_strips_mismatched_scratchmark_close():
    """The exact failure: <scratchpad>…</scratchmark> + the real brief after."""
    raw = (
        "<scratchpad>\n1. The question is X.\n3. Strongest counterargument: Y.\n"
        "5. Clause 27 applies.\n</scratchmark>\n\n"
        "## ARKMURUS WEEKLY BRIEF\n\nExecutive summary: real content here."
    )
    user_facing, sp = scratchpad.strip(raw)
    assert "ARKMURUS WEEKLY BRIEF" in user_facing
    assert "<scratchpad>" not in user_facing
    assert "counterargument" not in user_facing.lower()  # reasoning gone
    assert "Strongest counterargument" in sp              # but captured for audit


def test_strips_well_formed_close_still_works():
    raw = "<scratchpad>reasoning</scratchpad>\n\nVisible answer."
    user_facing, sp = scratchpad.strip(raw)
    assert user_facing == "Visible answer."
    assert sp == "reasoning"


def test_strips_other_malformed_variants():
    for close in ("</scratch>", "</scratchPad>", "</scratchpadx>"):
        raw = f"<scratchpad>secret reasoning{close}\n\nClean answer."
        user_facing, _ = scratchpad.strip(raw)
        assert "secret reasoning" not in user_facing, f"leaked with close={close}"
        assert "Clean answer." in user_facing


def test_unclosed_scratchpad_stripped_to_end():
    """LLM cut off mid-scratchpad (no close at all) — strip from open to end so
    a truncated reasoning block never ships."""
    raw = "<scratchpad>\n1. reasoning that got <cut> off with angle brackets"
    user_facing, _ = scratchpad.strip(raw)
    assert "reasoning that got" not in user_facing
    assert user_facing.strip() == ""


def test_no_scratchpad_unchanged():
    raw = "Just a normal answer with no scratchpad."
    user_facing, sp = scratchpad.strip(raw)
    assert user_facing == raw
    assert sp == ""


def test_content_after_close_preserved_with_angle_brackets():
    """Real content containing '<' after the block must survive (the
    unclosed-strip lookahead must not eat it)."""
    raw = "<scratchpad>reasoning</scratchmark>\n\nUse a < b comparison in code."
    user_facing, _ = scratchpad.strip(raw)
    assert "Use a < b comparison" in user_facing
