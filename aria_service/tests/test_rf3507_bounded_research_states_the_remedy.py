"""R-F3507 — the bounded-research gap said what happened, not what fixes it.

From the delivered Babcock report::

    deep research was bounded at 37s and stopped after search fan-out (2 of 6 angles)
    — 0 article(s) analysed, 0 fact(s) retained

True, specific, and unusable. A reader could not tell whether ARIA was broken, whether
the subject was unusually hard, or whether something they could DO would change the
answer.

WHAT I EXPECTED TO FIND, AND DID NOT. My first instinct was that the stage should not run
at all when it cannot finish — spend nothing rather than spend and retain nothing. Reading
R-F3093 and R-F3131 showed that would have been wrong twice over:

  * The small standard/quick budget is DELIBERATE. The 660s total is what guarantees a
    WhatsApp async push inside the 15-minute poll window; R-F3131 states plainly that
    moving it is not available. So "cannot reach article analysis in standard mode" is a
    designed tier boundary, not a defect.
  * The fan-out is what produces the CITED SOURCES. Skipping it to save cost would have
    deleted the citations — including, on the Babcock run, the FRC investigation that
    R-F3455 now surfaces as a contradiction. I would have removed evidence to save a cost
    that was buying something.

So the remaining defect was narrow and honest: per R-F3456 a gap owes the reader WHY and
WHAT CLOSES IT. This adds the remedy and distinguishes the two cases — a tier boundary in
standard mode, versus a genuinely incomplete sweep in deep mode, where the larger budget
was already in play and "run it deeper" is NOT the answer.
"""
from __future__ import annotations

import pathlib
import re

import pytest

SRC = (pathlib.Path(__file__).resolve().parents[1]
       / "intel" / "dd_orchestrator.py").read_text(encoding="utf-8", errors="replace")


def _gap_block() -> str:
    m = re.search(r"R-F3507 — say what CLOSES it.{0,2600}", SRC, re.S)
    assert m, "the R-F3507 gap block is gone"
    return m.group(0)


def test_the_gap_still_states_what_actually_happened():
    """The remedy must not replace the facts — both are needed."""
    b = _gap_block()
    assert "article(s) analysed" in b
    assert "fact(s) retained" in b
    assert "NOT an exhaustive sweep" in b


def test_standard_mode_names_the_remedy():
    """THE DEFECT: a reader could not tell that a Deep run is the answer."""
    b = _gap_block()
    assert "DEEP mode" in b, "the gap does not tell the reader what closes it"
    assert "STANDARD-mode research budget" in b


def test_it_says_the_citations_gathered_are_still_valid():
    """Otherwise 'bounded' reads as 'discard everything here', and the reader throws away
    real evidence — on the Babcock run that included the FRC citation."""
    assert "citations gathered here are real and remain valid" in _gap_block()


def test_deep_mode_is_not_told_to_run_deeper():
    """The wrong remedy is worse than none. In deep mode the larger budget was already
    in play, so the honest reading is an incomplete sweep, not a tier limit."""
    b = _gap_block()
    assert "already the larger one" in b
    assert "genuinely incomplete rather than tier-limited" in b


def test_the_two_cases_are_actually_branched():
    """Not one sentence covering both — the advice differs and must be selected."""
    b = _gap_block()
    assert "if not _mode_is_deep else" in b, (
        "the remedy is not conditioned on mode, so one of the two readings is wrong")


def test_the_designed_tradeoff_is_recorded_where_the_next_reader_looks():
    """R-F3093/R-F3131 decided this deliberately. Without the reason in place, the next
    person 'fixes' the budget and breaks the WhatsApp delivery window."""
    b = _gap_block()
    assert "async-push window" in b or "poll window" in b
    assert "designed trade-off" in b or "designed" in b


def test_the_layer_is_still_marked_partial():
    """R-F3502 must survive: the header cannot go back to COMPLETED."""
    assert "report.digital.meta.status = LayerStatus.PARTIAL.value" in SRC
