"""R-F4383 (C-328) — an answer that cites sources no tool fetched is 0% grounded, not unmeasured.

THE DEFECT, and it is the module contradicting its own documented contract.
`verify_response` states:

    grounded_rate : grounded / cited (None if no citations)

The `no_tool` branch ignored that. When no tool ran it returned
`grounded_rate: None` even when the response carried citations, while placing
those very URLs in `unverified` — so the code already classified them as
unverified and then declined to score it. Five citations, zero verifiable, is
`0/5`; the contract says 0.0 and only an ABSENCE of citations gives None.

Live evidence, aria-intel 2026-08-27, from the verification index:

    verdict "no_tool", cited_count 5, unverified_count 5, grounded_rate null

The worst possible grounding outcome — an answer asserting sources that nothing
backs — was recorded as NO SIGNAL, indistinguishable from a turn that simply
never needed a citation. 3 of 455 records were in this state, so this is a
narrow hole rather than a flood; it is worth closing because it is the exact
direction that flatters the honesty metric, which is the axis Phase A exists to
build.

CONSEQUENCE, STATED DELIBERATELY. `avg_grounded_rate` gates external delivery
(operating_modes degrades below 30%), so admitting these as real 0.0 samples can
only ever lower it. That is correct — they ARE ungrounded answers, and excluding
them flattered the gate — and R-F3764's minimum-sample floor already prevents a
lone sample from taking delivery offline.

Empty citations still yield None. This admits genuinely-measured zeros; it does
not invent a zero where nothing was claimed.

Run: python -m pytest aria_service/tests/test_rf4383_uncited_claims_are_zero_grounded_not_unmeasured.py -v
"""
from __future__ import annotations

import pytest


def _verify(response, tool_context=""):
    from aria_service.intel import source_verifier
    return source_verifier.verify_response(response, tool_context)


CITED = (
    "Per the filings, the entity is clear. Sources: https://example.com/a "
    "and https://example.org/b for the registry extract."
)


def test_the_live_shape_citations_with_no_tool_score_zero():
    """The recorded live defect: cited 2, verified 0, scored as unmeasured."""
    out = _verify(CITED, tool_context="")

    assert out["verdict"] == "no_tool"
    assert len(out["cited_urls"]) == 2, "precondition: the answer cited sources"
    assert out["unverified"] == out["cited_urls"], (
        "the branch already treats these as unverified"
    )
    assert out["grounded_rate"] == 0.0, (
        "an answer citing 2 sources that NOTHING backs is 0% grounded, not "
        f"unmeasured — got {out['grounded_rate']!r}. The module's own contract "
        f"says grounded/cited, with None reserved for 'no citations'"
    )


def test_no_citations_and_no_tool_is_still_unmeasured():
    """The other half of the contract: absence is not a zero.

    A turn answered from ARIA's own knowledge claimed nothing, so there is
    nothing to be wrong about. Scoring it 0.0 would invent a failure and would
    drag the delivery gate down on every ordinary conversational turn.
    """
    out = _verify("Lisbon is the capital of Portugal.", tool_context="")

    assert out["verdict"] == "no_tool"
    assert out["cited_urls"] == []
    assert out["grounded_rate"] is None, (
        "no claim was made, so there is no grounding to measure — this must "
        f"stay None; got {out['grounded_rate']!r}"
    )


def test_an_empty_response_is_not_scored_zero():
    out = _verify("", tool_context="")
    assert out["grounded_rate"] is None


@pytest.mark.parametrize("n", [1, 3, 7])
def test_the_rate_is_the_documented_arithmetic(n):
    """grounded / cited — with no tool, `grounded` is empty, so it is 0/n."""
    urls = " ".join(f"https://example.com/{i}" for i in range(n))
    out = _verify(f"Findings attached. Sources: {urls}", tool_context="")
    assert len(out["cited_urls"]) == n
    assert out["grounded"] == []
    assert out["grounded_rate"] == 0.0


def test_a_tool_backed_answer_is_unaffected():
    """The healthy path must not move — this closes a hole, it does not re-score."""
    out = _verify(
        "The registry lists it as active. Source: https://example.com/a",
        tool_context="fetched https://example.com/a — registry extract",
    )
    assert out["verdict"] == "grounded"
    assert out["grounded_rate"] == 1.0
