"""R-F3316 - a hard-cancelled DD op must say WHERE its budget went.

THE PROBLEM THIS SOLVES. dd_orchestrator bounds each layer op with
asyncio.wait_for. On timeout the coroutine is CANCELLED, which destroys its stack
and returns the default, so the callee can report nothing at all. The report got:

    digital: deep research did not complete within 300s (bounded) - partial
             result, NOT a clean check

True, and useless. Three consecutive attempts at that timeout (R-F3258, R-F3300,
R-F3306) were therefore hypotheses rather than diagnoses, and the live runs
disproved each in turn:

  R-F3258  guarded topic at the boundary        -> topic was never the problem
  R-F3300  guarded the retention loop           -> the cut moved, 0 still retained
  R-F3306  retained incrementally               -> back to the outer backstop

Every one of those cost a full deploy plus an ~8 minute live DD to disprove. The
missing thing was never a better guess; it was evidence.

THE MECHANISM. `progress` is owned by the CALLER. investigate() writes its
current stage into that object as it goes, and because the object belongs to the
caller, those writes SURVIVE the cancellation that discards everything else. A
module-level global would have been simpler and wrong: concurrent DDs would
clobber each other's stage.

This is the same lesson as R-F3296 (a failure that would not name its own line),
applied to the case R-F3296 could not reach: an exception has a traceback, a
cancellation has nothing.
"""
from __future__ import annotations

import asyncio

import pytest

from aria_service.intel import dd_orchestrator as ddo


class _Layer:
    """Minimal stand-in for a report layer: only data_gaps is touched."""

    def __init__(self) -> None:
        self.data_gaps: list[str] = []


@pytest.mark.asyncio
async def test_a_cancelled_op_reports_the_stage_it_died_in() -> None:
    """THE CAPABILITY TEST. Without this, the gap says only 'did not complete'."""
    layer = _Layer()
    progress: dict = {}

    async def _slow():
        progress["stage"] = "article read"
        progress["analysed"] = 17
        progress["jobs"] = 33
        await asyncio.sleep(30)          # never finishes inside the bound
        return {"done": True}

    out = await ddo._bounded_dd_op(
        _slow(), 0.25, layer, "deep research", default={}, progress=progress,
    )

    assert out == {}, "the bounded op must still return its default"
    assert layer.data_gaps, "a bounded-out op must leave a data gap"
    gap = layer.data_gaps[0]

    assert "last stage: article read" in gap, (
        f"the gap must name the stage that consumed the budget. got: {gap!r}"
    )
    assert "analysed=17" in gap and "jobs=33" in gap, (
        f"the gap must carry the counters, not just the stage name. got: {gap!r}"
    )
    assert "NOT a clean check" in gap, "the honesty wording must survive"


@pytest.mark.asyncio
async def test_no_progress_supplied_still_produces_the_old_honest_gap() -> None:
    """Callers that pass nothing must be unaffected.

    Every other _bounded_dd_op call site omits `progress`. If the absence of a
    dict changed the message, or raised, this fix would have broken the layers it
    was not aimed at.
    """
    layer = _Layer()

    async def _slow():
        await asyncio.sleep(30)

    out = await ddo._bounded_dd_op(_slow(), 0.25, layer, "sanctions screen", default=None)

    assert out is None
    assert layer.data_gaps
    gap = layer.data_gaps[0]
    assert "did not complete within" in gap and "NOT a clean check" in gap
    assert "last stage" not in gap, "no progress means no stage claim, not an empty one"


@pytest.mark.asyncio
async def test_an_empty_progress_dict_claims_nothing() -> None:
    """Cancelled before the callee published anything: say nothing, invent nothing.

    An empty dict must not produce 'last stage: ' or 'last stage: None'. Reporting
    a stage we never observed is the fabrication this codebase keeps paying for.
    """
    layer = _Layer()
    progress: dict = {}

    async def _slow():
        await asyncio.sleep(30)

    await ddo._bounded_dd_op(_slow(), 0.25, layer, "deep research",
                             default={}, progress=progress)
    gap = layer.data_gaps[0]
    assert "last stage" not in gap, f"claimed a stage that was never reached: {gap!r}"


@pytest.mark.asyncio
async def test_the_diagnostic_cannot_break_the_op_it_watches() -> None:
    """A hostile progress object must not turn a bounded timeout into a crash.

    _bounded_dd_op's contract is that a slow op DEGRADES to a data gap and the
    layer completes. A diagnostic that can raise would convert that into a layer
    ERROR, which is strictly worse than the missing information it exists to add.
    """
    class _Hostile(dict):
        def items(self):                      # noqa: D102
            raise RuntimeError("boom")

    layer = _Layer()
    hostile = _Hostile()
    hostile["stage"] = "article read"

    async def _slow():
        await asyncio.sleep(30)

    out = await ddo._bounded_dd_op(_slow(), 0.25, layer, "deep research",
                                   default={}, progress=hostile)
    assert out == {}, "a broken diagnostic must not stop the op returning its default"
    assert layer.data_gaps, "and the gap must still be recorded"


def test_investigate_accepts_and_publishes_progress() -> None:
    """The producer half: the parameter exists and is actually written to.

    Grepping for the field name would pass on a parameter nobody assigns, which is
    the producer-with-no-writer defect this repo has hit repeatedly. Assert the
    publisher exists and that the stages it emits are the ones that can burn the
    budget.
    """
    import inspect
    from aria_service.intel import deep_researcher as dr

    sig = inspect.signature(dr.investigate)
    assert "progress" in sig.parameters, "investigate() must accept a progress dict"
    assert sig.parameters["progress"].default is None, "it must stay optional"

    src = inspect.getsource(dr.investigate)
    assert 'progress["stage"] = name' in src, (
        "the helper must WRITE the stage; a parameter nobody assigns is the "
        "producer-with-no-writer defect"
    )
    for stage in ("search fan-out", "article read", "fact retention"):
        assert f'_stage("{stage}"' in src, f"no publisher for the {stage!r} stage"


def test_the_gap_wording_carries_no_ai_dashes() -> None:
    """Report copy is customer-facing; house style forbids em and en dashes."""
    import inspect
    src = inspect.getsource(ddo._bounded_dd_op)
    for line in src.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if "data_gaps.append" in line or "did not complete within" in line:
            assert "—" not in line and "–" not in line, f"dash in gap copy: {line!r}"
