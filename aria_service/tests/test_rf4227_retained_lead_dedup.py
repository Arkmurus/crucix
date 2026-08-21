"""R-F4227 / C-207: one fact rendered as two retained leads.

Found in a delivered report — ARIA_DD_Penfold_Savings_Limited_dd_9b3bc17a15f4.pdf,
page 5. Two of its five "Retained research lead" items say the same thing:

    "Penfold Savings Limited IS registered at Companies House with company
     number 11668244"                                    [Company Registration and Legal Identity]
    "PENFOLD SAVINGS LIMITED registered at Companies House with company
     number 11668244"                                    [Company Registration]

One fact, two entries, each telling the reader to "verify this claim against the
cited source before relying on it" — so the report manufactures two verification
tasks out of one, in a section whose whole purpose is telling a customer what
still needs checking.

THE CAUSE. `_retained_research_findings` dedups on
`" ".join(content.lower().split())` — an EXACT string after whitespace
normalisation. Lowercasing makes the two agree on case; the word "is" makes them
disagree on everything else.

Worse, the subject-relevance filter directly above SELECTS for this: a fact is
kept when it names the subject or carries its registration number, so facts whose
entire content is the subject's identity are exactly what passes.

THE KEY IS ORDER-AWARE ON PURPOSE. `_distinctive_tokens` returns a SET, and a set
cannot tell "Acme acquired Broadwing" from "Broadwing acquired Acme" — R-F3579
wrote `_distinctive_sequence_dd` for precisely that hazard and this reuses it
rather than inventing a third comparison. Measured on the five real leads: the
sequence key merges exactly the one duplicate pair and leaves the address, board
composition and incorporation-date leads untouched.
"""

from __future__ import annotations

import pytest

from aria_service.intel.dd_orchestrator import (
    _distinctive_sequence_dd,
    _distinctive_tokens,
    _retained_research_findings,
)

REG = "11668244"


def _result(*contents):
    return {"facts": [
        {"content": c, "source_url": f"https://find-and-update.company-information.service.gov.uk/company/{REG}/p{i}",
         "confidence": "PROBABLE", "topic": f"Topic {i}"}
        for i, c in enumerate(contents)
    ]}


# ── the live duplicate ───────────────────────────────────────────────────────

def test_the_two_penfold_registration_leads_collapse_to_one():
    res = _result(
        f"Penfold Savings Limited is registered at Companies House with company number {REG}",
        f"PENFOLD SAVINGS LIMITED registered at Companies House with company number {REG}",
    )
    out = _retained_research_findings(res, limit=10, subject_names=["Penfold Savings Limited"], registration_number=REG)
    assert len(out) == 1, (
        "one fact rendered as two retained leads — each asking the reader to "
        f"verify it separately: {[f.title for f in out]}"
    )


def test_distinct_leads_are_all_kept():
    """The other three real leads from the same report say different things."""
    res = _result(
        f"PENFOLD SAVINGS LIMITED registered at Companies House with company number {REG}",
        "PENFOLD SAVINGS LIMITED registered office: The Ministry, 79-81 Borough Road, London, SE1 1DN, England",
        "PENFOLD SAVINGS LIMITED has 4 total officers on record: 2 active directors (Eastwood, Happe) and 2 resignations (Galer, Hykin).",
        "First director appointment date was 8 November 2018, indicating PENFOLD SAVINGS LIMITED incorporated on or shortly before this date",
    )
    out = _retained_research_findings(res, limit=10, subject_names=["Penfold Savings Limited"], registration_number=REG)
    assert len(out) == 4, (
        f"a distinct lead was dropped — dedup must never delete evidence: "
        f"{[f.title[:60] for f in out]}")


# ── the guard must not over-merge ────────────────────────────────────────────

def test_word_order_is_not_ignored():
    """A SET key would merge these; the ordered key must not.

    "Acme acquired Broadwing" and "Broadwing acquired Acme" share every
    distinctive token and mean opposite things. R-F3579 exists for this.
    """
    a = "Acme Industries acquired Broadwing Systems in 2024"
    b = "Broadwing Systems acquired Acme Industries in 2024"
    assert _distinctive_tokens(a) == _distinctive_tokens(b), "precondition: sets collide"
    assert _distinctive_sequence_dd(a) != _distinctive_sequence_dd(b)

    out = _retained_research_findings(_result(a, b), limit=10, subject_names=["Acme Industries", "Broadwing Systems"])
    assert len(out) == 2, "a reversal was merged — opposite claims collapsed into one"


def test_a_lead_that_adds_a_fact_is_not_merged_away():
    res = _result(
        f"PENFOLD SAVINGS LIMITED registered at Companies House with company number {REG}",
        f"PENFOLD SAVINGS LIMITED registered at Companies House with company number {REG} and is FCA authorised under FRN 826097",
    )
    out = _retained_research_findings(res, limit=10, subject_names=["Penfold Savings Limited"], registration_number=REG)
    assert len(out) == 2, "the second carries FCA authorisation — a different claim"


# ── evidence is never lost, only un-duplicated ───────────────────────────────

def test_the_surviving_lead_keeps_its_source_and_label():
    res = _result(
        f"Penfold Savings Limited is registered at Companies House with company number {REG}",
        f"PENFOLD SAVINGS LIMITED registered at Companies House with company number {REG}",
    )
    out = _retained_research_findings(res, limit=10, subject_names=["Penfold Savings Limited"], registration_number=REG)
    assert out[0].source.startswith("https://"), "provenance must survive dedup"
    assert out[0].confidence == "UNVERIFIED", (
        "a retained lead is a LEAD, not an adjudicated conclusion — the honest "
        "label must not be upgraded by deduplication")


@pytest.mark.parametrize("n", [0, 1])
def test_empty_and_single_inputs_are_safe(n):
    res = _result(*([f"PENFOLD SAVINGS LIMITED registered at Companies House {REG}"] * n))
    assert len(_retained_research_findings(res, limit=10, subject_names=["Penfold Savings Limited"], registration_number=REG)) == n


def test_content_with_no_distinctive_tokens_falls_back_to_the_exact_string():
    """A sequence key can be EMPTY — all tokens generic or two chars or fewer.

    Two such leads must not collapse into one just because both keys are empty.
    The fallback keeps the old exact-string behaviour for that case, so dedup can
    never merge on an absence.
    """
    a, b = "Ltd plc SA", "Inc GmbH AB"
    assert _distinctive_sequence_dd(a) == [] and _distinctive_sequence_dd(b) == [], (
        "precondition: both produce an empty sequence")
    out = _retained_research_findings(
        _result(a, b), limit=10, subject_names=None, registration_number="")
    assert len(out) == 2, "two different leads merged on an empty key"
