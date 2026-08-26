"""R-F4324 / C-272 — a mastery floor breach reported a comparison its own
numbers deny: "sanctions (50% < 50%)".

THE MECHANISM, and it is not rounding. R-F796 clamps a negative update so a
topic cannot be driven below its floor:

    student.py:  m["score"] = max(topic_hard_floor, proposed)
                 m["_rf796_proposed_breach"] = (proposed < topic_hard_floor)

so on that branch the score is set EXACTLY to the floor. The post-update check
then fires on `m["score"] < floor or rf796_breach`, and both the WARNING and
the recorded gap render

    f"{score:.0%} < {floor:.0%}"

with score == floor. The clamped case can therefore NEVER render a true
inequality — it prints "X% < X%" for every topic, every time, by construction.
Live evidence for the shape is already in this file's own history: R-F800's
comment records `BREACH: sanctions (41% < 50%)`, a real breach with real
numbers. The clamped variant looks identical in form but asserts something that
did not happen.

TWO DIFFERENT EVENTS WERE SHARING ONE SENTENCE, AND THEY DEMAND OPPOSITE
READINGS:

    score < floor          mastery IS below the floor — a genuine breach
    clamp held at floor    an update WOULD have gone below; the floor held

The second is downward PRESSURE, contained. Reporting it as "dropped below hard
floor ... Remediation: re-inject domain knowledge" tells the weekly remediation
loop and the §21e gap consumer that mastery has failed, when the defence
worked. An operator reading the log cannot tell which happened, and the only
clue that anything is wrong is that the numbers contradict the words.

WHY THIS MATTERS BEYOND TIDINESS. CLAUDE.md §1 records three Phase A gates
"certified by an absence" — claims whose own evidence could not support them.
This is the same class inverted: a claim its own evidence actively denies,
emitted on the learning system's remediation path. A reader who trusts the
sentence acts on a breach that is not there; a reader who trusts the numbers
concludes the instrument is broken and stops reading it.

THE FIX IS TO SAY WHICH EVENT HAPPENED, not to suppress either. Both are worth
reporting — a held floor under repeated pressure is a real signal, and naming
the proposed score makes it MORE informative than the false version was.
"""
from __future__ import annotations

import re

import pytest

from aria_service.intel import student


def _renderable_pairs(text: str) -> list[tuple[int, int]]:
    """Every 'A% < B%' comparison asserted in a message."""
    return [(int(a), int(b))
            for a, b in re.findall(r"(\d+)%\s*<\s*(\d+)%", text)]


# ---------------------------------------------------------------- unit

def test_a_held_floor_is_not_described_as_having_dropped_below_it():
    """THE DEFECT, at the one place both messages are built.

    score == floor is the EXACT state the R-F796 clamp produces, so this is
    not an edge case — it is the whole clamped branch.
    """
    detail = student.floor_event_detail(
        topic="sanctions", score=0.50, floor=0.50, held=True, proposed=0.43)
    assert "dropped below" not in detail.lower(), (
        "a clamped update is reported as an actual breach: " + detail)
    for a, b in _renderable_pairs(detail):
        assert a < b, (
            "asserts a comparison its own numbers deny: " + detail)


def test_a_held_floor_still_reports_the_pressure(self=None):
    """Do NOT fix this by going quiet. A floor holding under repeated
    downward pressure is a real signal and the reason R-F796 flagged it."""
    detail = student.floor_event_detail(
        topic="sanctions", score=0.50, floor=0.50, held=True, proposed=0.43)
    assert "sanctions" in detail
    assert "43%" in detail, (
        "the proposed score is what makes the held case informative — without "
        "it the reader cannot see how hard the floor was pushed: " + detail)
    assert "50%" in detail


def test_a_genuine_breach_still_reads_as_a_breach():
    """THE OTHER HALF. R-F800's recorded live line was 'sanctions (41% < 50%)'
    — a true breach with a true comparison. That must survive unchanged, or
    the fix has traded a false positive for a false negative."""
    detail = student.floor_event_detail(
        topic="sanctions", score=0.41, floor=0.50, held=False, proposed=None)
    assert "below" in detail.lower()
    pairs = _renderable_pairs(detail)
    assert pairs, "a real breach no longer states its comparison: " + detail
    for a, b in pairs:
        assert a < b, detail


def test_no_message_can_assert_an_untrue_comparison_for_any_floor():
    """Swept across every configured floor, because the defect was structural:
    the clamp sets score := floor, so EVERY topic rendered 'X% < X%'."""
    for topic, floor in student.HARD_FLOORS.items():
        held = student.floor_event_detail(
            topic=topic, score=floor, floor=floor, held=True,
            proposed=max(0.0, floor - 0.07))
        for a, b in _renderable_pairs(held):
            assert a < b, f"{topic}: {held}"
        assert "dropped below" not in held.lower(), f"{topic}: {held}"


def test_the_warning_summary_also_states_a_true_comparison():
    """THE OTHER COPY OF THE BUG. The rate-limited WARNING line and the gap
    detail were two INDEPENDENT copies of the same wrong format string — which
    is how the defect survived: fixing one would leave the other lying. This
    drives the short form directly.
    """
    held = student.floor_event_summary(
        topic="sanctions", score=0.50, floor=0.50, held=True, proposed=0.43)
    assert _renderable_pairs(held) == [], (
        "the held summary still asserts an inequality: " + held)
    assert "50%" in held and "43%" in held, held
    assert "held" in held.lower(), held


def test_the_warning_summary_keeps_a_real_breach_readable():
    """R-F800's recorded live line was 'sanctions (41% < 50%)'. That form is
    correct and must survive — the WARNING is what an operator scans."""
    real = student.floor_event_summary(
        topic="sanctions", score=0.41, floor=0.50, held=False)
    pairs = _renderable_pairs(real)
    assert pairs == [(41, 50)], real
    for a, b in pairs:
        assert a < b, real


def test_both_message_builders_agree_across_every_floor():
    """They are kept adjacent in student.py precisely so they cannot drift.
    This asserts the property that adjacency is meant to protect."""
    for topic, floor in student.HARD_FLOORS.items():
        for held, proposed in ((True, max(0.0, floor - 0.07)), (False, None)):
            score = floor if held else max(0.0, floor - 0.09)
            detail = student.floor_event_detail(
                topic, score, floor, held=held, proposed=proposed)
            summary = student.floor_event_summary(
                topic, score, floor, held=held, proposed=proposed)
            for msg in (detail, summary):
                for a, b in _renderable_pairs(msg):
                    assert a < b, f"{topic} held={held}: {msg}"
            # the two must describe the SAME event
            assert ("held" in detail.lower()) == ("held" in summary.lower()), (
                f"{topic} held={held}: detail and summary disagree\n"
                f"  {detail}\n  {summary}")


# -------------------------------------------- THE CAPABILITY TEST

@pytest.mark.asyncio
async def test_the_recorded_gap_does_not_claim_a_breach_the_clamp_prevented(monkeypatch):
    """§3c — drives the REAL update_mastery and reads what actually reaches
    the gap ledger, which is what the remediation loop and the §21e coder
    consume. A unit test on the formatter alone would not prove the wiring.
    """
    topic = "sanctions"
    floor = student.HARD_FLOORS[topic]

    recorded: list[str] = []
    warnings: list[str] = []

    class _Gaps:
        @staticmethod
        async def record_gap(**kw):
            recorded.append(kw.get("detail", ""))

    monkeypatch.setattr(student, "capability_gaps", _Gaps, raising=False)
    import sys
    sys.modules["aria_service.intel.capability_gaps"] = _Gaps  # the local import
    monkeypatch.setattr(student.logger, "warning",
                        lambda msg, *a: warnings.append(msg % a if a else msg))
    # never let this test touch the durable store
    monkeypatch.setattr(student, "_save_mastery", _noop, raising=False)
    monkeypatch.setattr(student, "_maybe_flush_mastery", _noop, raising=False)
    monkeypatch.setattr(student, "_mark_mastery_dirty", lambda *a, **k: None,
                        raising=False)
    monkeypatch.setattr(student, "_last_floor_warning", {}, raising=False)

    # Sit the topic exactly AT its floor, which is where the clamp puts it,
    # then drive a wrong answer hard enough that the unclamped update would
    # cross. The clamp holds; the flag fires; the message is built.
    cache = {topic: {"score": floor, "attempts": 5, "correct": 3}}
    monkeypatch.setattr(student, "_mastery_cache", cache, raising=False)

    async def _load():
        return cache
    monkeypatch.setattr(student, "_load_mastery", _load, raising=False)

    await student.update_mastery([topic], correct=False, weight=1.0)

    emitted = recorded + warnings
    assert emitted, "the breach path did not report at all — test is not driving it"
    for msg in emitted:
        for a, b in _renderable_pairs(msg):
            assert a < b, (
                "a message reaching the gap ledger asserts a comparison its "
                "own numbers deny: " + msg)


async def _noop(*a, **k):
    return None
