"""R-F3592 — ARIA ran a paid web search to find out what time it was.

Live 2026-07-31. "What is the time in Portugal?" matched the factual-QA shape
(question word + linking verb, short, no specialist keyword) and was routed to
`brave_answer`. The search returned timeanddate.com snippets that did NOT contain
the current time — the page renders its clock in JavaScript, so a crawler sees
sunrise and sunset and nothing else — and ARIA then deadlocked between the tool
ANSWER SCOPE and her own clock, leaking her chain of thought (R-F3591).

The clock is in her system prompt (R-F3588). Searching for it spends money and
seconds to fetch something the process is already holding, and returns a worse
answer than the one it already has.

THE FIRST CUT OF THIS FIX WAS WRONG, and the way it was wrong is the point:
excluding ambient questions from the `brave_answer` branch alone just let the
question fall through to `deep_research`. Still a web search — the defect had
MOVED, not gone. Per-branch exclusions cannot work when there are a dozen routing
branches and the next one added inherits the hole. The guard therefore runs FIRST
in _detect_tool_intent and returns None outright.
"""

from __future__ import annotations

import pathlib

import pytest

from aria_service.routes.aria import _BRAVE_QA_AMBIENT_RE, _detect_tool_intent


_ROUTES = pathlib.Path(__file__).resolve().parents[1] / "routes" / "aria.py"


#: Every phrasing that asks for the CURRENT instant. None may reach a tool.
_AMBIENT = [
    "What is the time in Portugal?",
    "What time is it in the UK?",
    "what is the date today?",
    "What day is it?",
    "what is the current time in Lisbon?",
    "What year is it?",
    "what's the time?",
    "time right now?",
    "What is today's date?",
]

#: Genuine web questions. Over-excluding these would push ARIA to answer from
#: training data — the fabrication this product exists to prevent.
_NEEDS_A_SOURCE = [
    "What is the deadline for SITCL applications?",
    "Who is the CEO of Rheinmetall?",
    "What is the time limit for an appeal?",
    "Investigate Rheinmetall AG",
]


@pytest.mark.parametrize("question", _AMBIENT)
def test_an_ambient_question_runs_no_tool_at_all(question):
    """Not 'no brave_answer' — NO TOOL. Asserting the weaker property is what
    let the first fix pass while the question silently rerouted to
    deep_research."""
    intent = _detect_tool_intent(question)
    assert intent is None, (
        f"{question!r} routed to {intent.get('tool')!r} "
        f"(reason {intent.get('_reason')!r}). ARIA already holds the clock; any "
        f"tool here is spend and latency for a worse answer."
    )


@pytest.mark.parametrize("question", _NEEDS_A_SOURCE)
def test_a_real_question_still_reaches_a_tool(question):
    intent = _detect_tool_intent(question)
    assert intent is not None and intent.get("tool"), (
        f"{question!r} no longer routes to any tool — the ambient exclusion has "
        f"grown too broad and ARIA will answer it from training data"
    )


def test_the_guard_runs_before_every_routing_branch():
    """Position is the fix. Anywhere lower and the next branch inherits the hole."""
    src = _ROUTES.read_text(encoding="utf-8")
    fn = src.index("def _detect_tool_intent(message: str)")
    guard = src.index("if _BRAVE_QA_AMBIENT_RE.search(msg):", fn)
    first_tool = src.index('"tool":', fn)
    assert guard < first_tool, (
        "the ambient guard sits after a routing branch — a question can be "
        "claimed by that branch before the guard ever runs"
    )


def test_the_guard_returns_none_rather_than_rerouting():
    src = _ROUTES.read_text(encoding="utf-8")
    idx = src.index("if _BRAVE_QA_AMBIENT_RE.search(msg):")
    assert src[idx:idx + 120].count("return None") == 1, (
        "the guard must return None. Redirecting to a different tool is how the "
        "first attempt failed — the search moved from brave_answer to deep_research"
    )


def test_the_pattern_is_not_corrupted_by_control_characters():
    """The first version of this regex had its \\b anchors written as literal
    BACKSPACE characters (0x08) by a shell heredoc. It compiled, imported and
    silently matched nothing — the pattern required an invisible control char
    before every keyword. Same corruption class as the U+0001 incident earlier
    in this session."""
    src = _ROUTES.read_text(encoding="utf-8")
    control = [hex(ord(c)) for c in src if ord(c) < 9 or 13 < ord(c) < 32]
    assert not control, f"control characters in routes/aria.py: {set(control)}"
    assert _BRAVE_QA_AMBIENT_RE.search("What is the time in Portugal?"), (
        "the pattern matches nothing — verify the instrument before trusting it"
    )


def test_the_exclusion_is_narrow_enough_to_be_safe():
    """A time WORD is not a time QUESTION."""
    for benign in ("What time does the market open?",
                   "when did the sanctions take effect",
                   "what is the lead time for delivery"):
        assert not _BRAVE_QA_AMBIENT_RE.search(benign), (
            f"{benign!r} is matched by the ambient pattern — it needs a source"
        )
