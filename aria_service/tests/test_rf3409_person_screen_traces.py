"""R-F3409 — a name match on a person is NOT an identification.

The mirror image of the false clean, and the way an innocent person gets
flagged. Screening a company and screening a person are different task shapes,
and the corpus only ever taught the first.

MEASURED against the live endpoint 2026-07-29, a person match carries exactly
three fields:

    {"name": "DERIPASKA, Oleg Vladimirovich", "list": "ofac_sdn", "score": 0.667}

No date of birth. No nationality. No passport or national id. Nothing that
identifies a human being. The payload therefore cannot establish that the
person in front of you IS the person on the list — it can only say that a
listed NAME resembles the string searched, and at 0.667 it does not even say
that strongly.

An answer that turns that into "X is sanctioned" has invented the identity
step. It happens to be true for Deripaska and would be catastrophic for the
next person who shares a surname — which is precisely the name-coincidence
failure this repo has already shipped once against companies.

So the target answer reports the matched RECORD, reports the score, states
plainly which identifiers were NOT returned, and refuses to confirm identity.
It also must not swing the other way: a match is a real signal and must be
escalated, not dismissed.
"""
from __future__ import annotations

import pytest

from scripts.train import build_tooluse_corpus as B


# The real shape, copied from the live probe — three fields, no identifiers.
MATCH_PAYLOAD = {
    "status": "OK", "entity": "Oleg Deripaska",
    "sanctions": {"screened": True, "sources": ["ofac_sdn", "uk_ofsi"], "matches": [
        {"name": "DERIPASKA, Oleg Vladimirovich", "list": "ofac_sdn", "score": 0.667},
    ]},
}

NO_MATCH_PAYLOAD = {
    "status": "OK", "entity": "John Smith",
    "sanctions": {"screened": True, "sources": ["ofac_sdn", "uk_ofsi"], "matches": []},
}

# Hypothetical richer payload — if the source ever returns identifiers, the
# honest answer changes, and the validator must not forbid the stronger claim.
IDENTIFIED_PAYLOAD = {
    "status": "OK", "entity": "Oleg Deripaska",
    "sanctions": {"screened": True, "sources": ["ofac_sdn"], "matches": [
        {"name": "DERIPASKA, Oleg Vladimirovich", "list": "ofac_sdn", "score": 0.99,
         "dob": "1968-01-02", "nationality": "RU", "passport": "REDACTED"},
    ]},
}


def _final(t: dict) -> str:
    return t["messages"][-1]["content"]


# --------------------------------------------------------------------------
# the trace
# --------------------------------------------------------------------------

def test_trace_is_valid_and_labelled():
    t = B.build_person_screen_trace("Oleg Deripaska", MATCH_PAYLOAD)
    assert B.validate_trace(t) == []
    assert t["label"] == "tooluse_person"
    assert t["subject"] == "Oleg Deripaska"


def test_answer_reports_the_matched_record_and_the_score():
    """"A match" is unusable without knowing WHAT matched and how well."""
    t = B.build_person_screen_trace("Oleg Deripaska", MATCH_PAYLOAD)
    final = _final(t)
    assert "DERIPASKA, Oleg Vladimirovich" in final
    assert "0.667" in final


def test_answer_names_the_identifiers_that_were_not_returned():
    t = B.build_person_screen_trace("Oleg Deripaska", MATCH_PAYLOAD)
    final = _final(t).lower()
    assert "date of birth" in final or "dob" in final
    assert "nationality" in final


def test_answer_refuses_to_confirm_identity_on_a_name_match():
    """Asserted via the validator's own denial pattern, not a wording guess."""
    t = B.build_person_screen_trace("Oleg Deripaska", MATCH_PAYLOAD)
    final = _final(t)
    assert B._IDENTITY_DENIAL_RE.search(final)
    assert not B._IDENTITY_CLAIM_RE.search(final)


def test_answer_does_not_dismiss_the_match_either():
    """Over-correcting into "so it's nothing" is the other way to fail a user."""
    t = B.build_person_screen_trace("Oleg Deripaska", MATCH_PAYLOAD)
    final = _final(t).lower()
    assert "escalat" in final or "must be resolved" in final or "before proceeding" in final


def test_no_match_on_a_person_is_not_a_clearance_of_the_person():
    t = B.build_person_screen_trace("John Smith", NO_MATCH_PAYLOAD)
    assert B.validate_trace(t) == []
    final = _final(t).lower()
    assert "common name" in final or "does not" in final


# --------------------------------------------------------------------------
# the validator
# --------------------------------------------------------------------------

def test_validator_rejects_asserting_identity_without_identifiers():
    t = B.build_person_screen_trace("Oleg Deripaska", MATCH_PAYLOAD)
    t["messages"][-1]["content"] = (
        "Oleg Deripaska is a designated individual on the OFAC SDN list. "
        "Do not proceed with this counterparty."
    )
    errs = B.validate_trace(t)
    assert errs, "identity asserted from a 0.667 name similarity and no identifiers"
    assert any("identif" in e.lower() for e in errs)


@pytest.mark.parametrize("phrasing", [
    "This is a NAME match only; identity is not confirmed.",
    "I cannot confirm this is the same individual — no date of birth was returned.",
    "The record 'DERIPASKA, Oleg Vladimirovich' matched at 0.667, which does not "
    "establish that this is the same person.",
    "A name similarity is not an identification and must not be treated as one.",
])
def test_validator_allows_the_honest_refusal(phrasing):
    """The correct answer necessarily discusses identity while denying it."""
    t = B.build_person_screen_trace("Oleg Deripaska", MATCH_PAYLOAD)
    # A complete answer does BOTH: refuses the identity claim AND flags the hit.
    # Refusing identity is not licence to bury a live match.
    t["messages"][-1]["content"] = (
        phrasing + " There is a match found on ofac_sdn. Escalate for identity resolution."
    )
    assert B.validate_trace(t) == [], f"false positive on: {phrasing!r}"


def test_validator_permits_a_confirmed_claim_when_identifiers_ARE_present():
    """The rule is about evidence, not about never confirming anything."""
    t = B.build_person_screen_trace("Oleg Deripaska", IDENTIFIED_PAYLOAD)
    t["messages"][-1]["content"] = (
        "Oleg Deripaska is a designated individual on ofac_sdn: the record "
        "'DERIPASKA, Oleg Vladimirovich' matched at 0.99 with date of birth "
        "1968-01-02 and nationality RU."
    )
    assert B.validate_trace(t) == []


def test_validator_rejects_a_list_the_screen_never_reported():
    t = B.build_person_screen_trace("Oleg Deripaska", MATCH_PAYLOAD)
    t["messages"][-1]["content"] = (
        "Name match only, identity not confirmed. The record appears on the "
        "eu_cfsp list. Escalate for identity resolution."
    )
    assert B.validate_trace(t), "citing a list the payload never returned is fabrication"


def test_refusing_identity_does_not_licence_burying_the_match():
    """The other way to fail a user: refuse the identity claim, hide the hit.

    The generic screen rule already demands a match be reported as a hit. This
    pins that the person axis does not become an exemption from it - an answer
    that only says "identity unconfirmed" and never flags the live match is
    rejected.
    """
    t = B.build_person_screen_trace("Oleg Deripaska", MATCH_PAYLOAD)
    t["messages"][-1]["content"] = (
        "Identity is not confirmed. Nothing further is needed here."
    )
    errs = B.validate_trace(t)
    assert errs, "an unflagged live match must not validate"
