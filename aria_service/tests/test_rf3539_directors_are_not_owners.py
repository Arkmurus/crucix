"""R-F3539 — a company's own DIRECTORS satisfied "Ownership and control: ANSWERED".

THE CATEGORY ERROR. `network_walker.walk_ubo_chain` is named for ownership, but its own
docstring describes a DIRECTORSHIP walk:

    "1. Fetch the entity's officers/directors
     2. Sanctions-screen each new officer
     3. For each officer, fetch their OTHER corporate appointments
     4. Add discovered entities to the graph, recurse into them"

Every node it emits carries role "director" or "cross_linked". Not one is an ownership
relationship. Yet `ubo_chain` is one of the three sources the ownership gate consults, and
the holder test excluded only the SUBJECT — so a company's own directors answered the
ownership question.

OBSERVED ON FOUR CONSECUTIVE DELIVERED REPORTS. On Bidvest Noonan (dd_75d996233394) the
corporate PSC Crane Midco Limited (06648599) was never traversed to its own parent, while
four people who are CRANE MIDCO'S OFFICERS were listed as traversed parties and counted as
holders. R-F3027 saw exactly this — its comment reads "what the report called a 3-deep UBO
chain was the subject plus its two DIRECTORS, who are not beneficial owners at all" — and
fixed one symptom (untraversed controllers) without removing the category error beneath it.

WHY THIS IS MISREPRESENTATION, NOT A DISPLAY BUG. Someone who runs a company may own none
of it; someone who owns all of it may hold no office. Answering an OWNERSHIP question with
OFFICER names asserts a fact about control that the evidence does not contain — and it
does so on the scorecard line a reader uses to decide whether ownership is known.

EXCLUSION, NOT ALLOW-LIST. PSC entries arrive from `identity.shareholders` (Companies
House `psc`) often with no `role` at all. An allow-list would silently discard genuine
owners, trading a false ANSWERED for a false UNANSWERED — the opposite error, equally
dishonest. Only relationships PROVEN not to be ownership are removed.
"""
from __future__ import annotations

import pytest

from aria_service.intel.dd_schema import (
    _NON_OWNERSHIP_ROLE_MARKERS,
    _has_named_holder,
    _is_non_ownership_node,
    _named_holders,
)


# ── the defect ──────────────────────────────────────────────────────────────

def test_capability_directors_alone_do_NOT_answer_ownership():
    """THE BIDVEST SHAPE: the walk returned officers of a corporate holder, and they
    were counted as beneficial owners."""
    holders = [
        {"name": "Rajesh Gupta", "role": "director"},
        {"name": "Ann Kennedy", "role": "director"},
        {"name": "Brian Lawlor", "role": "director"},
        {"name": "Caoimhe Ní Mhurchú", "role": "director"},
    ]
    assert _has_named_holder(holders) is False, (
        "four directors answered the ownership question — they own nothing")
    assert _named_holders(holders) == []


@pytest.mark.parametrize("role", [
    "director", "Director", "corporate-director", "secretary",
    "corporate-nominee-secretary", "officer", "cross_linked", "cross-linked",
    "appointment", "manager",
])
def test_every_non_ownership_role_is_rejected(role):
    assert _is_non_ownership_node({"name": "Someone", "role": role}) is True
    assert _has_named_holder([{"name": "Someone", "role": role}]) is False


# ── the direction that must NOT break ───────────────────────────────────────

def test_a_real_PSC_with_no_role_still_answers():
    """PSC rows from `identity.shareholders` frequently carry no `role`. Excluding them
    would trade a false ANSWERED for a false UNANSWERED — the opposite lie."""
    assert _has_named_holder([{"name": "Crane Midco Limited"}]) is True


def test_a_psc_kind_is_never_mistaken_for_an_officer_role():
    """`kind` values like corporate-entity-person-with-significant-control contain none
    of the officer markers, and `_is_non_ownership_node` reads `role` only."""
    psc = {"name": "The Bidvest Group Limited",
           "kind": "corporate-entity-person-with-significant-control"}
    assert _is_non_ownership_node(psc) is False
    assert _has_named_holder([psc]) is True


def test_an_unknown_relationship_is_not_assumed_to_be_a_directorship():
    """Absent role means UNKNOWN, not "officer". Guessing would drop real owners."""
    assert _is_non_ownership_node({"name": "Someone"}) is False


# ── display and decision may never disagree ─────────────────────────────────

def test_the_displayed_holders_match_the_gate_exactly():
    """R-F3463 exists so what is shown cannot disagree with what was decided. If the
    gate now rejects directors, the display must stop naming them too — otherwise the
    reader sees officer names under a heading that says ownership."""
    mixed = [
        {"name": "A Director", "role": "director"},
        {"name": "Real Holder Ltd"},
        {"name": "A Secretary", "role": "secretary"},
    ]
    assert _has_named_holder(mixed) is True
    assert _named_holders(mixed) == ["Real Holder Ltd"], (
        "the display still lists officers as holders")


def test_the_subject_is_still_excluded():
    """R-F2793/R-F3027 behaviour must survive: a subject cannot evidence its own
    ownership."""
    assert _has_named_holder([{"name": "Subject Ltd", "role": "subject"}]) is False
    assert _has_named_holder([{"name": "Subject Ltd", "hop_depth": 0}]) is False


def test_subject_plus_its_officers_is_the_original_false_answer():
    """R-F3027's own description of the defect, asserted directly."""
    chain = [
        {"name": "Subject Ltd", "role": "subject", "hop_depth": 0},
        {"name": "Director One", "role": "director", "hop_depth": 1},
        {"name": "Director Two", "role": "director", "hop_depth": 1},
    ]
    assert _has_named_holder(chain) is False, (
        "'a 3-deep UBO chain' that is the subject plus two directors is not ownership")


def test_the_marker_list_stays_conservative():
    """A marker that matched a PSC kind would silently delete real owners. None of the
    Companies House PSC kinds may contain any marker."""
    psc_kinds = [
        "individual-person-with-significant-control",
        "corporate-entity-person-with-significant-control",
        "legal-person-person-with-significant-control",
        "super-secure-person-with-significant-control",
    ]
    for kind in psc_kinds:
        assert not any(m in kind for m in _NON_OWNERSHIP_ROLE_MARKERS), (
            f"marker collides with the PSC kind {kind!r} — real owners would be dropped")
