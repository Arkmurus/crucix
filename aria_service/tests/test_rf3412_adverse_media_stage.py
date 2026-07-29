"""R-F3412 — grade the procedural stage; never escalate beyond the evidence.

Adverse coverage is not uniform, and flattening it fails users in both
directions. An allegation is not a charge, a charge is not a conviction, and a
matter that was CLOSED WITHOUT FINDINGS is not a live risk. Reporting all four
as "adverse media" is unfair to the subject and useless to the reader, who needs
to know where in a process a matter sits before deciding anything.

MEASURED against the live search 2026-07-29, one query for Danske Bank returned
BOTH "pleaded guilty ... agreed to forfeit $2 billion" (resolved) and an
investigation "closed early ... did not publish the findings" (a different
matter at a different stage), alongside a DOI paper and a `memory://` hook that
are not coverage at all. The stage therefore has to be graded from each item's
OWN text — an entity-level label would be wrong for at least one item every
time.

THE CHECKABLE PROPERTY is escalation. The stage an answer claims may not exceed
the highest stage the payload's own words support. "Investigated" must not
become "convicted", and an allegation must never be stated as established fact.
The reverse matters too: where the payload says a matter was CLEARED, an answer
that omits that is presenting a stale risk.
"""
from __future__ import annotations

import pytest

from scripts.train import build_tooluse_corpus as B


def _sr(*items) -> dict:
    return {"results": [
        {"title": t, "url": u, "snippet": s} for t, u, s in items
    ]}


ALLEGED = _sr(("Acme accused of overbilling", "https://www.reuters.com/a",
               "Campaigners alleged that Acme overbilled the ministry."))
INVESTIGATION = _sr(("Acme under investigation", "https://www.reuters.com/b",
                     "Prosecutors opened a probe into Acme's contracts."))
CONVICTED = _sr(("Acme pleads guilty", "https://www.reuters.com/c",
                 "Acme pleaded guilty and agreed to forfeit $2 billion."))
CLEARED = _sr(("Acme cleared", "https://www.reuters.com/d",
               "The regulator closed its investigation and Acme was cleared of wrongdoing."))


def _final(t: dict) -> str:
    return t["messages"][-1]["content"]


# --------------------------------------------------------------------------
# the ladder
# --------------------------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("Campaigners alleged that Acme overbilled.", "alleged"),
    ("Prosecutors opened a probe into Acme.", "investigation"),
    ("Acme was charged with bank fraud conspiracy.", "charged"),
    ("Acme pleaded guilty and agreed to forfeit $2 billion.", "resolved_adverse"),
    ("Acme was acquitted of all counts.", "resolved_cleared"),
    ("Acme opened a new factory in Leeds.", ""),
])
def test_stage_is_graded_from_the_texts_own_words(text, expected):
    assert B._grade_stage(text) == expected


def test_the_highest_supported_stage_wins_within_one_item():
    """A conviction piece necessarily also mentions the investigation."""
    assert B._grade_stage(
        "After a long probe, Acme pleaded guilty and was fined."
    ) == "resolved_adverse"


def test_cleared_is_not_ranked_as_adverse():
    """An entity cleared is not a risk; ranking it as one is the unfair failure."""
    assert B._STAGE_RANK["resolved_cleared"] < B._STAGE_RANK["charged"]


# --------------------------------------------------------------------------
# the trace
# --------------------------------------------------------------------------

def test_trace_is_valid_and_labelled():
    t = B.build_adverse_media_trace("Acme Holdings", INVESTIGATION)
    assert B.validate_trace(t) == []
    assert t["label"] == "tooluse_adverse"
    assert t["subject"] == "Acme Holdings"


def test_an_allegation_is_reported_as_an_allegation():
    t = B.build_adverse_media_trace("Acme Holdings", ALLEGED)
    final = _final(t).lower()
    assert "alleg" in final
    assert "convicted" not in final and "guilty" not in final


def test_a_cleared_matter_is_reported_as_cleared():
    t = B.build_adverse_media_trace("Acme Holdings", CLEARED)
    final = _final(t).lower()
    assert "cleared" in final or "closed" in final
    assert "not" in final  # it says what this does NOT establish


def test_no_coverage_yields_no_trace():
    assert B.build_adverse_media_trace("Acme Holdings", {"results": []}) is None


def test_memory_only_results_yield_no_trace():
    """ARIA's own memory is not coverage."""
    mem = {"results": [{"title": "note", "url": "memory://x", "snippet": "y"}]}
    assert B.build_adverse_media_trace("Acme Holdings", mem) is None


# --------------------------------------------------------------------------
# the validator — escalation is the thing that must not survive
# --------------------------------------------------------------------------

def test_validator_rejects_escalating_an_investigation_to_a_conviction():
    t = B.build_adverse_media_trace("Acme Holdings", INVESTIGATION)
    t["messages"][-1]["content"] = (
        "Acme Holdings was convicted of fraud [from reuters.com]."
    )
    errs = B.validate_trace(t)
    assert errs, "a probe stated as a conviction must not survive"
    assert any("escalat" in e.lower() or "stage" in e.lower() for e in errs)


def test_validator_rejects_stating_an_allegation_as_established():
    t = B.build_adverse_media_trace("Acme Holdings", ALLEGED)
    t["messages"][-1]["content"] = (
        "Acme Holdings overbilled the ministry [from reuters.com]. This is established."
    )
    assert B.validate_trace(t), "an allegation stated as fact must not survive"


def test_validator_allows_reporting_the_stage_that_IS_supported():
    t = B.build_adverse_media_trace("Acme Holdings", CONVICTED)
    t["messages"][-1]["content"] = (
        "Acme Holdings pleaded guilty and agreed to forfeit $2 billion "
        "[from reuters.com]. This is a resolved adverse matter, not an allegation."
    )
    assert B.validate_trace(t) == []


@pytest.mark.parametrize("phrasing", [
    "This is an allegation, not a conviction [from reuters.com].",
    "No charge has been brought; the matter is at the investigation stage [from reuters.com].",
    "Acme has not been convicted of anything [from reuters.com].",
])
def test_validator_allows_denials_that_name_a_higher_stage(phrasing):
    """The honest answer necessarily names the stage it is ruling OUT.

    Same negation trap as clean/hit/corroboration/identity — the fifth in this
    module. A rank check on raw vocabulary flags every one of these.
    """
    t = B.build_adverse_media_trace("Acme Holdings", INVESTIGATION)
    t["messages"][-1]["content"] = phrasing
    assert B.validate_trace(t) == [], f"false positive on: {phrasing!r}"


def test_validator_rejects_omitting_that_a_matter_was_cleared():
    """Presenting a closed matter as live risk is the stale-risk failure."""
    t = B.build_adverse_media_trace("Acme Holdings", CLEARED)
    t["messages"][-1]["content"] = (
        "Acme Holdings was the subject of a regulatory investigation [from reuters.com]. "
        "Treat as an open risk."
    )
    assert B.validate_trace(t), "a cleared matter reported as open must not survive"


def test_a_clearance_outranks_the_investigation_it_closed():
    """The defect that produced stale risk from the grader itself.

    "The regulator closed its investigation and Acme was cleared" contains the
    word "investigation". Grading by SEVERITY made it rank as an open
    investigation, so a cleared matter was reported as live. WHICH stage this is
    and HOW SEVERE it is are different questions: the resolution decides the
    stage, the severity rank then decides how bad that stage is.
    """
    assert B._grade_stage(
        "The regulator closed its investigation and Acme was cleared of wrongdoing."
    ) == "resolved_cleared"
    # ...and it is still the LEAST severe outcome, which is the whole point.
    assert B._STAGE_RANK["resolved_cleared"] < B._STAGE_RANK["investigation"]
    assert B._STAGE_PRECEDENCE["resolved_cleared"] > B._STAGE_PRECEDENCE["investigation"]


def test_a_conviction_still_outranks_a_clearance_mention():
    """Precedence must not let a stray 'dropped' bury a guilty plea."""
    assert B._grade_stage(
        "One count was dropped but Acme pleaded guilty and was fined $2 billion."
    ) == "resolved_adverse"
