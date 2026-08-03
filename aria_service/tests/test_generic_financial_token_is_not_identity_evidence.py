"""A shared generic financial noun must never carry a sanctions HARD_STOP.

MEASURED INCIDENT — DD run dd_29368fbb8b3d, 2026-08-03.

    OFAC SDN match: D.G.D. INVESTMENTS LTD.
    Match score 0.85. Programme(s): GLOMAG.
    R-F569 gate: shared tokens: ['investments']
    HARD_STOP - PROBABLE

The subject was "BATSELA CAPITAL INVESTMENTS L.L.P". The sanctioned entity was
"D.G.D. INVESTMENTS LTD." — a different company. The ONLY thing they shared was
the word "investments".

The delivered report told the operator to refuse the engagement and "File SAR if
reporting thresholds are met", and simultaneously printed
"US Treasury - Office of Foreign Assets Control - SDN List - CLEAN" in its own
sanctions table. Both statements came from the same run.

WHY IT SURVIVED THE GUARD. classify_sanctions_match() demotes a single-token
overlap only when that token is SHORT:

    elif len(shared) == 1:
        only_token = next(iter(shared))
        if len(only_token) < 5:
            severity = "info"

That is the R-F351 acronym rule ("AA", "DGD"). "investments" is eleven
characters, so it passed — because the guard tests LENGTH, and length is not
what makes a token identifying. Distinctiveness is.

THE ACTUAL DEFECT was one level up, in _CORP_SUFFIXES. Its "Misc / descriptive"
block already drops holdings, group, international, industries, trading,
services, solutions, systems, technologies, partners — thorough on industrial
and tech naming, and empty of financial equivalents, in a product whose subjects
are overwhelmingly investment vehicles. Had "investments" been listed there, the
tokeniser would have dropped it, shared would have been 0, and the existing
zero-overlap rule would have demoted the match with no new logic at all.

The comment on that guard states the trade-off this violated:
    "Cost of false-positive HARD_STOP (defamation, SAR mis-filing) >> cost of
     false-negative demote-to-info (operator still sees match in per_match[])"

NOTE: intentionally carries no R-number — data/r_number_reservations.json is the
peer agent's ledger and reserving one here would collide.
"""
from __future__ import annotations

import pytest

from aria_service.intel._sanctions_classify import _tokenize_entity_name


GENERIC_FINANCIAL = [
    "investments", "investment", "capital", "ventures", "equity",
    "assets", "funds", "finance", "financial", "management",
    "advisory", "securities",
]


@pytest.mark.parametrize("token", GENERIC_FINANCIAL)
def test_generic_financial_nouns_are_not_identifying_tokens(token):
    """They must not survive tokenisation — same rule as holdings/group/trading."""
    assert _tokenize_entity_name(f"Acme {token} Ltd") == {"acme"}, (
        f"{token!r} is being treated as identity evidence; it is a descriptor "
        f"that appears in a large share of financial entity names"
    )


def test_the_batsela_collision_no_longer_shares_any_token():
    """The exact pair from run dd_29368fbb8b3d.

    Two unrelated companies. Once 'investments' is recognised as a descriptor
    there is NOTHING in common, so the existing zero-overlap rule demotes the
    match to info without any new branch.
    """
    subject = _tokenize_entity_name("BATSELA CAPITAL INVESTMENTS L.L.P")
    sdn = _tokenize_entity_name("D.G.D. INVESTMENTS LTD.")
    assert subject & sdn == set(), (
        f"still overlapping on {subject & sdn} — a HARD_STOP would still be "
        f"raised against an unrelated entity"
    )
    # And the distinctive part of each name is preserved: this fix removes noise,
    # not signal.
    assert "batsela" in subject
    assert "dgd" in sdn or "d" not in sdn


def test_a_real_match_still_overlaps():
    """Guard against over-correction — the fix must not blind genuine hits.

    A sanctioned entity with a distinctive name component still shares it, so
    nothing about the real-match path changes.
    """
    q = _tokenize_entity_name("Rosoboronexport Capital Ltd")
    c = _tokenize_entity_name("ROSOBORONEXPORT OAO")
    assert "rosoboronexport" in (q & c), (
        "the distinctive token must still match — only descriptors were removed"
    )


def test_a_name_made_only_of_descriptors_yields_no_tokens():
    """Documents the deliberate consequence rather than hiding it.

    "Capital Investments Ltd" reduces to nothing, so overlap can never justify a
    HARD_STOP on such a name. That is correct: the name carries no identifying
    information. An EXACT hit is still caught upstream — the R-F569 bypass fires
    at score>=0.95 before overlap is consulted — and the match stays visible to
    the operator in per_match[] either way.
    """
    assert _tokenize_entity_name("Capital Investments Ltd") == set()
