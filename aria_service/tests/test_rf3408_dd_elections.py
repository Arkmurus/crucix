"""R-F3408 — an ordered DD section must actually be searched.

THE OPERATOR'S REQUIREMENT, verbatim: "once those selections are made the DD MUST search
those, we cannot have issues."

WHY THIS NEEDS ITS OWN MODEL. The New DD form offers optional sections, some metered — a
CCJ search is £6-£10 a time. Selecting one is a purchase. A selection that silently does
not run is WORSE than never offering the section: the buyer believes they have coverage
they do not have, and no row in the report says otherwise. That is a false clean the
customer PAID for, and it is invisible precisely because the UI made it look deliberate.

Four properties are therefore pinned here:

  1. an election PULLS THE QUESTION INTO SCOPE even when the tier excludes it — without
     this, ticking a box on a Simplified run changes nothing at all
  2. an unfulfilled election flips `elections_honoured` False for the whole run, so a
     caller cannot present the report as complete
  3. an unfulfilled election is NEVER billable — a metered search that did not reach the
     register must not reach an invoice
  4. `no_adapter` (ours: we sold what we cannot deliver) and `source_failed` (theirs:
     searched, no answer) are distinguished, because they have different owners and only
     one is retryable

Together with R-F3406's Waiver these are the two halves of scope selection: declining a
check is WAIVED and never reads as clean; ordering one creates an obligation that is
reported on explicitly.
"""
from __future__ import annotations

import pytest

from aria_service.intel import dd_standard as S


def _company(**identity) -> dict:
    base = {"entity_type": "company", "entity_name": "Testco Ltd"}
    base.update(identity)
    return {"identity": base}


def _elected(a, qid):
    return next(e for e in a["elections"] if e["question_id"] == qid)


# ── 1. an election widens scope ──────────────────────────────────────────────

def test_election_pulls_a_higher_tier_question_into_scope():
    """IS-17b (CCJ) is STANDARD tier. Ticking it on a SIMPLIFIED run must add it."""
    assert S.QUESTIONS_BY_ID["IS-17b"].tier == S.Tier.STANDARD.value
    plain = S.assess(_company(), tier="SIMPLIFIED")
    assert not any(r["question_id"] == "IS-17b" for r in plain["resolutions"])

    elected = S.assess(_company(), tier="SIMPLIFIED", elections=["IS-17b"])
    assert any(r["question_id"] == "IS-17b" for r in elected["resolutions"]), (
        "a ticked paid section did not enter the run — the selection did nothing"
    )


def test_election_cannot_make_an_inapplicable_question_applicable():
    """Widening the tier is legitimate; asking a company question of a person is not."""
    a = S.assess({"identity": {"entity_type": "person"}}, tier="SIMPLIFIED",
                 elections=["FS-12"])
    e = _elected(a, "FS-12")
    assert e["fulfilled"] is False
    assert e["failure_kind"] == "not_applicable"
    assert e["billable"] is False


def test_election_for_an_unknown_question_is_ignored():
    """A form/API typo must not look like a purchased check."""
    a = S.assess(_company(), tier="STANDARD", elections=["NOT-A-QUESTION"])
    assert a["elections"] == []
    assert a["elections_honoured"] is True


# ── 2. an unfulfilled election is loud ───────────────────────────────────────

def test_unfulfilled_election_flips_elections_honoured():
    a = S.assess(_company(), tier="SIMPLIFIED", elections=["IS-17b"])
    assert a["elections_honoured"] is False
    assert a["elections_unfulfilled"], "an ordered-but-unsearched section left no record"


def test_ordered_but_unsearched_is_classified_as_our_failure():
    a = S.assess(_company(), tier="SIMPLIFIED", elections=["IS-17b"])
    e = _elected(a, "IS-17b")
    assert e["failure_kind"] == "no_adapter"
    assert "ORDERED BUT NOT SEARCHED" in e["detail"]
    assert "must not be presented as covered" in e["detail"]


def test_source_failure_is_distinguished_from_no_adapter():
    """Searched-and-failed is retryable and is the register's failure; never-searched is
    ours. Collapsing them sends the operator to the wrong remedy."""
    rep = _company(sanctions_screen={"matches": [], "source_unavailable": True,
                                     "verified_sources": []})
    a = S.assess(rep, tier="SIMPLIFIED", elections=["IS-13"])
    e = _elected(a, "IS-13")
    assert e["fulfilled"] is False
    assert e["failure_kind"] == "source_failed"
    assert e["failure_kind"] != "no_adapter"


def test_fulfilled_election_is_marked_and_honoured():
    rep = _company(registration_number="04300718", registration_status="active")
    a = S.assess(rep, tier="SIMPLIFIED", elections=["EI-1"])
    e = _elected(a, "EI-1")
    assert e["fulfilled"] is True
    assert e["failure_kind"] == ""
    assert a["elections_honoured"] is True


# ── 3. billing follows the search, not the order ─────────────────────────────

@pytest.mark.parametrize("qid,rep", [
    ("IS-17b", _company()),                                   # never searched
    ("IS-13", _company(sanctions_screen={"matches": [], "source_unavailable": True,
                                         "verified_sources": []})),  # searched, failed
])
def test_an_unfulfilled_election_is_never_billable(qid, rep):
    a = S.assess(rep, tier="SIMPLIFIED", elections=[qid])
    assert _elected(a, qid)["billable"] is False, (
        f"{qid} did not run but was marked billable — a metered search that never "
        f"reached the register must never reach an invoice"
    )


def test_only_a_search_that_answered_is_billable():
    rep = _company(registration_number="1", registration_status="active")
    a = S.assess(rep, tier="SIMPLIFIED", elections=["EI-1"])
    assert _elected(a, "EI-1")["billable"] is True


# ── 4. an order beats a decline, and the contradiction is recorded ───────────

def test_election_beats_waiver_for_the_same_question():
    a = S.assess(_company(), tier="STANDARD", elections=["IS-13"],
                 waivers=[{"question_id": "IS-13", "waived_by": "X", "reason": "skip"}])
    state = next(r["state"] for r in a["resolutions"] if r["question_id"] == "IS-13")
    assert state != S.EvidenceState.WAIVED.value, (
        "a check that was ORDERED was skipped because it had also been waived"
    )


def test_election_waiver_contradiction_is_recorded_not_silently_resolved():
    a = S.assess(_company(), tier="STANDARD", elections=["IS-13"],
                 waivers=[{"question_id": "IS-13", "waived_by": "X", "reason": "skip"}])
    assert a["election_waiver_conflicts"] == ["IS-13"]


def test_waiver_still_applies_to_questions_that_were_not_elected():
    a = S.assess(_company(), tier="STANDARD", elections=["EI-1"],
                 waivers=[{"question_id": "IS-13", "waived_by": "A. Correa",
                           "reason": "domestic contract"}])
    assert a["election_waiver_conflicts"] == []
    state = next(r["state"] for r in a["resolutions"] if r["question_id"] == "IS-13")
    assert state == S.EvidenceState.WAIVED.value


# ── shape + purity ───────────────────────────────────────────────────────────

def test_no_elections_means_honoured_and_empty():
    a = S.assess(_company(), tier="STANDARD")
    assert a["elections"] == []
    assert a["elections_unfulfilled"] == []
    assert a["elections_honoured"] is True


def test_elections_accept_strings_dicts_and_objects():
    for form in (["EI-1"],
                 [{"question_id": "EI-1", "elected_by": "A"}],
                 [S.Election(question_id="EI-1", elected_by="A")]):
        a = S.assess(_company(registration_number="1", registration_status="active"),
                     tier="SIMPLIFIED", elections=form)
        assert _elected(a, "EI-1")["fulfilled"] is True


def test_assess_remains_pure_with_elections():
    rep = _company(registration_number="1", registration_status="active")
    kw = dict(tier="STANDARD", elections=["IS-17b"], waivers=[])
    assert S.assess(rep, **kw) == S.assess(rep, **kw)


def test_paid_sections_are_identifiable_for_the_form():
    """The form needs to know WHICH sections cost money, and that must come from the
    resolver registry rather than being hardcoded in the UI where it would drift."""
    paid = [q.id for q in S.QUESTIONS
            if S.Access.PAID_PER_SEARCH.value in S.resolver_status(q)["blocked_on"]]
    assert "IS-17b" in paid, "the CCJ question is no longer flagged as metered"
    for qid in paid:
        specs = [S.RESOLVERS[r] for r in S.QUESTIONS_BY_ID[qid].resolvers]
        assert any(s.access == S.Access.PAID_PER_SEARCH.value for s in specs)
