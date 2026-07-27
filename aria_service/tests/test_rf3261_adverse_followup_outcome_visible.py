"""R-F3261 — the adverse-media follow-up must not finish in the dark.

WHAT THE CUSTOMER SEES TODAY. R-F2657 moved the adverse-media deep search OUT OF
BAND: the DD renders and DELIVERS at T+0 with `adverse_media.status="in_progress"`,
and the sweep merges into the stored report up to ~420s later. Decision-readiness
then counts the question UNRESOLVED -> 4/5 -> Grade C -> "NOT CLEARED", and the
report tells the reader to "re-open the report to pick up the completed sweep" —
with nothing anywhere telling them WHEN that has happened.

WHY IT MATTERS MORE THAN A COSMETIC LAG. R-F2780 lets the follow-up ESCALATE the
stored verdict on credible adverse findings. So a report delivered GREEN can become
adverse minutes later while the customer holds the clean copy they already acted on.

WHAT WAS ACTUALLY MISSING. On merge, `_run_adverse_media_followup` did exactly one
thing: `logger.info(...)`. By §21a's own definition that is DARK, not wired — no
brain signal, no delivery outcome, no gap. So ARIA could not answer "did this DD's
adverse-media question ever get answered?", and a follow-up that died left no trace
for the self-heal loop.

The honesty layer is already correct and is NOT touched here: the renderer's
"STILL RUNNING", the R-F2941 reconciler and the never-false-clean marking all stay
exactly as they are. What changes is that the merge now REPORTS ITSELF.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from aria_service.intel import dd_orchestrator as ddo


def _outcomes_for(am_result, esc=None, body=None, merge_ok=True):
    """Drive the REAL _run_adverse_media_followup and capture what it reported."""
    captured: list = []

    async def _fake_record(rec):
        captured.append(rec)
        return {"recorded": True}

    body = body if body is not None else {"adverse_media": {"status": "in_progress"}}

    async def _search(**kw):
        return dict(am_result)

    from aria_service.intel import outcome_wire as _ow
    from aria_service.intel import redis_store as _rs
    from aria_service.intel import researcher as _res

    ctxs = [
        patch.object(_res, "run_adverse_media_deep_search", new=_search),
        patch.object(ddo, "get_report",
                     new=AsyncMock(return_value=body if merge_ok else None)),
        patch.object(_rs, "set_json", new=AsyncMock(return_value=None)),
        patch.object(ddo, "_sync_report_surfaces_after_followup", new=AsyncMock(return_value=None)),
        patch.object(ddo, "_apply_adverse_media_to_verdict",
                     return_value=esc or {"escalated": False, "reason": ""}),
        patch.object(ddo, "_refresh_persisted_decision_readiness", return_value={}),
        patch.object(_ow, "record_outcome", new=_fake_record),
    ]
    for c in ctxs:
        c.__enter__()
    try:
        asyncio.run(ddo._run_adverse_media_followup(
            "dd_test123", entity_name="Azure Parking Ltd", director_names=[],
            ubo_names=[], sectors=["defence"], trigger_reason="green-screen",
        ))
    finally:
        for c in reversed(ctxs):
            c.__exit__(None, None, None)
    return captured


# ── THE CAPABILITY TESTS — drive the real follow-up ───────────────────────────
def test_a_completed_sweep_reports_a_real_answer() -> None:
    recs = _outcomes_for({"ok": True, "findings_count": 12})
    assert recs, "the merge finished and reported NOTHING — that is the dark path"
    r = recs[0]
    assert r.actual_outcome == "delivered_real_answer"
    assert "dd_test123" in r.request_id
    assert r.intended_result


def test_a_failed_sweep_reports_an_error_so_self_heal_can_act() -> None:
    """record_outcome files a capability gap on non-success — that is the trigger."""
    recs = _outcomes_for({"ok": False, "error": "source unreachable"})
    assert recs
    assert recs[0].actual_outcome == "error"
    assert "unreachable" in recs[0].detail.lower() or "incomplete" in recs[0].detail.lower()


def test_a_self_bounded_sweep_is_not_reported_as_a_full_answer() -> None:
    """A partial sweep answered SOME of its templates — honest, but not complete."""
    recs = _outcomes_for({"ok": True, "partial": True, "findings_count": 3})
    assert recs
    assert recs[0].actual_outcome == "timeout_fallback"


def test_a_verdict_escalation_after_delivery_is_flagged_loudly() -> None:
    """The customer is holding a report that is now WRONG — say so explicitly."""
    recs = _outcomes_for({"ok": True, "findings_count": 9},
                         esc={"escalated": True, "reason": "2 credible adverse findings"})
    assert recs
    detail = recs[0].detail.lower()
    assert "escalat" in detail, "a post-delivery verdict change must be named"
    assert "credible adverse findings" in detail


def test_a_report_that_vanished_is_reported_not_swallowed() -> None:
    recs = _outcomes_for({"ok": True, "findings_count": 1}, merge_ok=False)
    assert recs
    assert recs[0].actual_outcome == "error"
    assert "not" in recs[0].detail.lower()


def test_the_outcome_wire_never_breaks_the_merge() -> None:
    """Best-effort: a broken wire must not cost the customer the merged findings."""
    from aria_service.intel import outcome_wire as _ow
    from aria_service.intel import redis_store as _rs
    from aria_service.intel import researcher as _res

    async def _search(**kw):
        return {"ok": True, "findings_count": 4}

    body = {"adverse_media": {"status": "in_progress"}}
    with patch.object(_res, "run_adverse_media_deep_search", new=_search), \
         patch.object(ddo, "get_report", new=AsyncMock(return_value=body)), \
         patch.object(_rs, "set_json", new=AsyncMock(return_value=None)), \
         patch.object(ddo, "_sync_report_surfaces_after_followup", new=AsyncMock(return_value=None)), \
         patch.object(ddo, "_apply_adverse_media_to_verdict",
                      return_value={"escalated": False, "reason": ""}), \
         patch.object(ddo, "_refresh_persisted_decision_readiness", return_value={}), \
         patch.object(_ow, "record_outcome", new=AsyncMock(side_effect=RuntimeError("wire down"))):
        asyncio.run(ddo._run_adverse_media_followup(
            "dd_test456", entity_name="X", director_names=[], ubo_names=[],
            sectors=["defence"], trigger_reason="green-screen"))
    # the merge still happened despite the wire throwing
    assert body["adverse_media"]["status"] == "completed"
