"""R-F3538 — the assessment prescribed a CONTRACTING process to every reader.

FROM THE DELIVERED Bidvest Noonan report (dd_75d996233394). The NOT CLEARED assessment
closed with "the standard contracting path is NOT available", and the recommendation read
"Do not rely on this report for counterparty clearance ... obtain independent commercial
DD."

A DD is run for many reasons — investment, acquisition, supplier onboarding, KYC/AML,
insurance underwriting, partnership, journalism, internal audit. A report that prescribes
a CONTRACTING process is telling most of its readers about a decision they are not making,
and it invites the mirror error: a reader who is not contracting may conclude the caveat
does not apply to them at all. That is the opposite of what a coverage warning is for.

THE SPLIT THIS ENFORCES. What ARIA can attest is identical in every case: which
decision-critical questions are unanswered, and that the evidence therefore does not
support a clearance decision. What to DO about that depends on a purpose ARIA was never
told, and belongs to the reader.

The wording must stay STRONGER, not softer — "for any purpose" widens the caveat rather
than hedging it.
"""
from __future__ import annotations

import pathlib

import pytest

_RAW = (pathlib.Path(__file__).resolve().parents[1]
        / "intel" / "dd_orchestrator.py").read_text(encoding="utf-8", errors="replace")

#: Comment lines STRIPPED. The R-F3538 comment necessarily quotes the phrases it removed
#: in order to explain the change, and scanning raw source flagged that prose as if it
#: were live wording — a scanner that cannot tell code from commentary about code.
SRC = "\n".join(ln for ln in _RAW.splitlines() if not ln.lstrip().startswith("#"))

#: Whitespace-collapsed AND literal-joined. The strings are assembled from adjacent
#: literals split across lines — `"depends on why the " "check was run"` — so collapsing
#: whitespace alone leaves the `" "` separators sitting inside the phrase and no single
#: occurrence exists to match. Removing them reconstructs what Python actually builds.
FLAT = " ".join(SRC.split()).replace('" "', "")


@pytest.mark.parametrize("phrase", [
    "the standard contracting path is NOT available",
    "standard contracting path is NOT",
    "for counterparty clearance",
    "obtain independent commercial DD",
])
def test_the_purpose_assuming_phrases_are_gone(phrase):
    assert phrase not in SRC, (
        f"{phrase!r} assumes the reader is contracting; a DD is run for investment, "
        "KYC, onboarding, insurance, journalism and audit too")


def test_the_evidence_statement_survives_and_is_wider():
    """Removing the purpose must not soften the warning — it must widen it."""
    assert "does not support a clearance decision" in FLAT
    assert "for any purpose" in FLAT, (
        "the caveat must apply to every reader, not just contracting ones")
    assert "This is not a clean bill" in FLAT, "the plain-language warning was lost"


def test_the_recommendation_hands_the_decision_back_to_the_reader():
    assert "depends on why the check was run" in FLAT
    assert "states the evidence position, not the" in FLAT, (
        "the report must say what it CAN attest and stop there")


def test_both_not_cleared_branches_were_updated():
    """There are two NOT CLEARED branches — sanctions-open and coverage-short. Fixing
    one and leaving the other is how half a defect ships."""
    assert FLAT.count("does not support a clearance decision") >= 2, (
        "only one branch carries the neutral wording")


def test_the_scorecard_pointer_is_retained():
    """The reader still needs to be told WHERE the unresolved items are explained —
    neutrality must not cost navigability."""
    assert "decision-readiness scorecard" in FLAT
