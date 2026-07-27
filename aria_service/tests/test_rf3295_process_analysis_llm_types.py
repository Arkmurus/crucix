"""R-F3295 - the real cause of "deep_research failed: 'list' object has no attribute 'lower'".

    researcher.py:2111
        validates = parsed.get("validates")
        if validates:
            for h in hypotheses:
                if validates.lower() in h.get("hypothesis", "").lower():

`parsed` is the LLM's article analysis. `validates` and `challenges` are used as
strings with no type check, so when the model answers with a LIST (a perfectly
ordinary shape for "which hypotheses does this validate?") the call raises
AttributeError.

WHY IT COSTS SO MUCH. This runs inside investigate()'s ARTICLE LOOP, before
synthesis, so the exception escapes investigate() entirely.
dd_orchestrator.py:6692 catches ANY exception from that call and downgrades it to
a data-gap string, discarding every article read and every fact learned. The live
AZURE PARKING LTD report shows exactly that: the gap present, and
articles_read/facts_learned both absent.

HOW I GOT HERE, because two earlier attempts were wrong and the record matters:
  * R-F3258 guarded `topic` at investigate()'s boundary. The live run disproved it:
    `topic_coerced_from` never fired, so topic was a clean string all along.
  * R-F3268 extended that to six entity_name assignment sites. Sound hardening,
    same wrong hypothesis.
  * A nested list in the QUERY list was also ruled out by reproduction: that path
    dies at `_chunk_long_query`'s `.strip()`, raising 'no attribute strip', not
    'lower'.
Only the article loop produces this exact message and escapes.

The fix coerces at the point of use rather than trusting the model's shape: a list
is joined, a scalar is stringified, anything unusable is skipped. A hypothesis
match is a best-effort signal, so it must never be able to destroy a DD's research.
"""
from __future__ import annotations

import pytest

from aria_service.intel import researcher as res


def _hyps() -> list[dict]:
    return [{"hypothesis": "Azure Parking Ltd operates car parks in London",
             "evidence_count": 0, "status": "OPEN"}]


def _parsed(**over) -> dict:
    base = {"facts": [], "hypotheses": [], "validates": None, "challenges": None}
    base.update(over)
    return base


@pytest.mark.asyncio
async def test_a_list_validates_does_not_raise() -> None:
    """THE LIVE CRASH. A model answering with a list must not kill the run."""
    hyps = _hyps()
    await res._process_analysis(
        _parsed(validates=["Azure Parking Ltd operates car parks in London",
                           "something else"]),
        "investigation:AZURE PARKING LTD", hyps,
    )


@pytest.mark.asyncio
async def test_a_list_challenges_does_not_raise() -> None:
    hyps = _hyps()
    await res._process_analysis(
        _parsed(challenges=["Azure Parking Ltd operates car parks in London"]),
        "investigation:AZURE PARKING LTD", hyps,
    )


@pytest.mark.asyncio
async def test_a_list_still_matches_its_hypothesis() -> None:
    """Coerced, not merely swallowed: the signal must survive the fix.

    Skipping non-str input would stop the crash and silently lose every
    hypothesis match, which is the quiet-failure pattern this codebase keeps
    paying for.
    """
    hyps = _hyps()
    await res._process_analysis(
        _parsed(validates=["Azure Parking Ltd operates car parks in London"]),
        "investigation:AZURE PARKING LTD", hyps,
    )
    assert hyps[0]["evidence_count"] == 1, "a list-shaped validates must still count"


@pytest.mark.asyncio
async def test_a_plain_string_behaves_exactly_as_before() -> None:
    """Regression: the normal path is untouched."""
    hyps = _hyps()
    await res._process_analysis(
        _parsed(validates="Azure Parking Ltd operates car parks in London"),
        "investigation:AZURE PARKING LTD", hyps,
    )
    assert hyps[0]["evidence_count"] == 1


@pytest.mark.asyncio
async def test_unusable_shapes_are_skipped_not_fatal() -> None:
    """A dict or a number is not a hypothesis reference. Skip, never raise."""
    for bad in ({"a": 1}, 12345, [None, {}]):
        hyps = _hyps()
        await res._process_analysis(
            _parsed(validates=bad), "investigation:X", hyps,
        )
        assert hyps[0]["evidence_count"] == 0
