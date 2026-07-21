"""R-F2845 — "Ownership and control" must not be ANSWERED by the subject itself.

THE DEFECT, on live run dd_86cce59b5bb1 (SOCAR Trading SA). decision_readiness
reported:

    ownership_control: ANSWERED

on this evidence:

    identity.directors     : []
    identity.shareholders  : []
    identity.ubo_chain     : []
    network.controlled_by  : []
    network.findings       : 0
    network.ubo_chain      : [ {name: "SOCAR Trading SA", role: "subject",
                                hop_depth: 0, id: "seed"} ]

Zero ownership information. The single "chain" node is the company pointing at
itself. dd_schema's ownership test is:

    ownership_present = any(_has_named_holder(src) for src in (
        ident["shareholders"], ident["ubo_chain"], network["ubo_chain"]))

and _has_named_holder passes on any entry with a substantive name >= 2 chars — which
the SEED node trivially satisfies, because its name is the subject's own name.

WHY THIS IS A USP DEFECT. It is a false ANSWERED: the mirror of a false clean, sitting
inside the very framework that exists to be honest about what we do not know. A false
clean says "no findings, therefore safe"; a false ANSWERED says "we looked, therefore
we know". Both convert an absence into a positive. It also inflates the headline metric
(3/5 answered when the truth is 2/5) and it does so on precisely the question where we
are weakest and a competitor's report on the same entity carried real data
(SOCAR Energy Holdings AG, 100%).

THE RULE ALREADY EXISTS ONE LAYER AWAY. dd_orchestrator.py:281, in the edge-writer:

    if not target or ":??:" in target or target == src:
        dropped += 1        # unanchorable, or the subject itself — never guess an id

The graph layer knows "the subject is not a relationship". The readiness layer did not.
Same architecture as R-F2821 (wire severed below the call site), R-F2832 (timeout
declared, not applied on the live path) and R-F2840 (acronym guard one layer from the
verdict): the protection exists, just not on the path that produces the answer.

EXPECTED EFFECT: the SOCAR report moves from 3/5 to 2/5 answered. Less flattering and
more true — which is the product.
"""
import pytest

from aria_service.intel.dd_schema import _has_named_holder


# The exact node from the live report.
SEED_ONLY = [{
    "id": "seed", "name": "SOCAR Trading SA", "type": "company",
    "jurisdiction": "CH", "registration_number": "CHE113990112",
    "role": "subject", "hop_depth": 0, "parent_id": None,
}]

REAL_HOLDER = [
    dict(SEED_ONLY[0]),
    {"id": "company:CH:CHE999", "name": "SOCAR Energy Holdings AG", "type": "company",
     "role": "shareholder", "hop_depth": 1, "parent_id": "seed",
     "registration_number": "CHE999", "jurisdiction": "CH"},
]


def test_the_subject_alone_is_not_ownership_evidence():
    """CAPABILITY: the exact live payload must not satisfy the ownership question."""
    assert _has_named_holder(SEED_ONLY) is False, (
        "a UBO chain containing only the subject (role='subject', hop_depth=0, "
        "id='seed') carries ZERO ownership information — it must not answer "
        "'Ownership and control'"
    )


def test_a_real_holder_still_counts():
    """ANTI-REGRESSION: the fix must not disarm the question.

    Rejecting the subject must not reject genuine holders, or we trade a false
    ANSWERED for a false UNRESOLVED and lose real evidence.
    """
    assert _has_named_holder(REAL_HOLDER) is True


@pytest.mark.parametrize("node,why", [
    ({"name": "X Ltd", "role": "subject"}, "role marks it as the subject"),
    ({"name": "X Ltd", "hop_depth": 0}, "hop 0 is the seed, not a holder"),
    ({"name": "X Ltd", "id": "seed"}, "the seed id marks the subject"),
])
def test_every_subject_discriminator_is_honoured(node, why):
    """Three independent markers exist; any one of them means 'this is the subject'."""
    assert _has_named_holder([node]) is False, why


def test_a_holder_at_depth_one_counts_even_without_a_role():
    """Do not over-reject: a hop-1 node is a holder even if `role` is missing."""
    assert _has_named_holder([{"name": "Parent AG", "hop_depth": 1}]) is True


def test_plain_string_holders_still_work():
    """Legacy shape: shareholders can be bare strings, which carry no role/hop."""
    assert _has_named_holder(["SOCAR Energy Holdings AG"]) is True


def test_empty_and_malformed_inputs_are_not_ownership():
    for bad in ([], None, "not-a-list", [{}], [{"name": ""}], [{"name": "  "}]):
        assert _has_named_holder(bad) is False, f"{bad!r} must not answer ownership"


def test_readiness_recomputes_to_two_of_five_on_the_live_shape():
    """End-to-end: the live report's answered count must drop 3/5 -> 2/5."""
    # ownership is the only question this R-number changes; assert the predicate
    # that drives it, which is what decision_readiness consumes.
    ownership_present = any(
        _has_named_holder(src) for src in ([], [], SEED_ONLY)
    )
    assert ownership_present is False, (
        "with directors/shareholders empty and the chain holding only the subject, "
        "ownership_control must be UNRESOLVED — the honest count is 2 of 5"
    )
