"""R-F2656 / R-F2658 — DD reliability (deliver partial on timeout) + grade honesty.

R-F2656 — on the async fire-and-poll path a hard-deadline timeout CANCELLED _impl
before _persist_report ran, and _bg_dd DISCARDS orchestrate_dd's return → the
state_store kept the mark_dd_running placeholder → the poll hung on "running"
until the R-F2300 reconcile marked it 'failed' ~30 min later, losing every
completed layer. Now the timeout handler PERSISTS the honest time-boxed partial.

R-F2658 — the evidence-depth grade hard-caps to Grade D when the confidence gate
fires, but a registry/identity layer that ERRORED or was clamped under load looks
identical to a genuinely thin entity → a real company gets Grade D purely from
timing. Now the grade is relabelled INCOMPLETE when the identity/compliance layer
status is error/skipped/prereq_fail, leaving confidence_gate_triggered / the AMBER
verdict / the R-F409 re-run untouched.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from aria_service.intel.dd_schema import _dd_quality_assessment


# ── R-F2658 — grade honesty: INCOMPLETE vs a genuinely-thin Grade D ──────────

def _report(ident_status: str = "ok", *, substance: bool = False,
            gate: bool = True) -> dict:
    ident: dict[str, Any] = {"meta": {"status": ident_status}}
    if substance:
        ident["registration_status"] = "active"
        ident["incorporation_date"] = "2010-01-01"
    return {"identity": ident, "confidence_gate_triggered": gate}


def test_grade_incomplete_when_registry_layer_errored() -> None:
    """Registry check FAILED (didn't run) → grade WITHHELD, not a thin-evidence D."""
    qa = _dd_quality_assessment(_report(ident_status="error"))
    assert qa["grade"] == "INCOMPLETE"
    assert any("did not complete" in b for b in qa["blocking_reasons"])


@pytest.mark.parametrize("status", ["skipped", "prereq_fail"])
def test_grade_incomplete_for_skipped_and_prereq_fail(status: str) -> None:
    assert _dd_quality_assessment(_report(ident_status=status))["grade"] == "INCOMPLETE"


def test_grade_d_when_registry_ran_clean_but_entity_is_thin() -> None:
    """The layer RAN (status ok) and found nothing → genuinely thin → Grade D stands."""
    qa = _dd_quality_assessment(_report(ident_status="ok"))
    assert qa["grade"] == "D", "a cleanly-run empty registry is a real thin-entity D"


def test_registry_incomplete_false_when_substance_present() -> None:
    """Even a non-ok status is NOT 'incomplete' if registry substance was captured."""
    qa = _dd_quality_assessment(_report(ident_status="error", substance=True))
    assert qa["grade"] != "INCOMPLETE"
    assert qa["metrics"].get("registry_incomplete") is False


def test_incomplete_does_not_touch_the_verdict_flag() -> None:
    """R-F2658 relabels the GRADE only — confidence_gate_triggered is preserved."""
    qa = _dd_quality_assessment(_report(ident_status="error"))
    assert qa["metrics"].get("confidence_gate_triggered") is True


# ── R-F2656 — deliver the honest partial on a hard-deadline timeout ──────────

@pytest.mark.asyncio
async def test_hard_deadline_persists_partial_instead_of_stuck_running(monkeypatch) -> None:
    from aria_service.intel import dd_orchestrator as ddo
    from aria_service.intel.dd_schema import ARKDDReport

    # Simulate the impl being cancelled at the hard deadline AFTER it populated the
    # in-progress report holder (exactly the real timeout path).
    async def _fake_impl(target, *a, _report_holder=None, **k):
        r = ARKDDReport(target=target, orchestrator_mode="standard")
        r.identity.entity_name = "TimeboxCo"
        if _report_holder is not None:
            _report_holder["report"] = r
        raise asyncio.TimeoutError()  # the outer wait_for(hard) cancel

    persisted: dict[str, Any] = {}

    async def _fake_persist(rep):
        persisted["rep"] = rep

    failed: dict[str, Any] = {}

    async def _fake_mark_failed(run_id, error):
        failed["run_id"] = run_id

    async def _noop_finalize(rep, hard_deadline_hit=False):
        return None

    async def _noop_keepalive():
        return None

    monkeypatch.setattr(ddo, "_orchestrate_dd_impl", _fake_impl)
    monkeypatch.setattr(ddo, "_persist_report", _fake_persist)
    monkeypatch.setattr(ddo, "mark_dd_failed", _fake_mark_failed)
    monkeypatch.setattr(ddo, "_finalize_dd_run", _noop_finalize)
    monkeypatch.setattr(ddo, "_dd_interactive_keepalive", _noop_keepalive)

    await ddo.orchestrate_dd(target={"name": "TimeboxCo"}, llm=None, run_id="dd_timebox_test")

    # THE FIX: the partial was persisted (poll will deliver it), not left stuck 'running'.
    assert persisted.get("rep") is not None, "hard-deadline partial must be persisted"
    assert persisted["rep"].time_boxed is True
    assert "TIME-BUDGET REACHED" in (persisted["rep"].bottom_line or "")
    # persist succeeded → we did NOT fall back to marking it failed
    assert "run_id" not in failed


@pytest.mark.asyncio
async def test_hard_deadline_clears_running_when_persist_also_fails(monkeypatch) -> None:
    """Belt-and-braces: if even the partial persist fails, never leave it stuck 'running'."""
    from aria_service.intel import dd_orchestrator as ddo
    from aria_service.intel.dd_schema import ARKDDReport

    async def _fake_impl(target, *a, _report_holder=None, **k):
        if _report_holder is not None:
            _report_holder["report"] = ARKDDReport(target=target, orchestrator_mode="standard")
        raise asyncio.TimeoutError()

    async def _boom_persist(rep):
        raise RuntimeError("state_store wedged")

    async def _no_report(run_id):  # body genuinely did not land → fallback must fire
        return None

    cleared: dict[str, Any] = {}

    async def _fake_mark_failed(run_id, error):
        cleared["run_id"] = run_id

    monkeypatch.setattr(ddo, "_orchestrate_dd_impl", _fake_impl)
    monkeypatch.setattr(ddo, "_persist_report", _boom_persist)
    monkeypatch.setattr(ddo, "get_report", _no_report)
    monkeypatch.setattr(ddo, "mark_dd_failed", _fake_mark_failed)
    monkeypatch.setattr(ddo, "_finalize_dd_run", lambda *a, **k: asyncio.sleep(0))
    monkeypatch.setattr(ddo, "_dd_interactive_keepalive", lambda: asyncio.sleep(0))

    await ddo.orchestrate_dd(target={"name": "X"}, llm=None, run_id="dd_boom_test")

    assert cleared.get("run_id") == "dd_boom_test", "must clear the stuck 'running' placeholder"


@pytest.mark.asyncio
async def test_hard_deadline_does_not_clobber_a_landed_partial(monkeypatch) -> None:
    """Pass-2 edge: if the BODY persisted but a late non-critical step hung past 25s,
    the fallback must NOT overwrite the deliverable partial with 'failed'."""
    from aria_service.intel import dd_orchestrator as ddo
    from aria_service.intel.dd_schema import ARKDDReport

    async def _fake_impl(target, *a, _report_holder=None, **k):
        if _report_holder is not None:
            _report_holder["report"] = ARKDDReport(target=target, orchestrator_mode="standard")
        raise asyncio.TimeoutError()

    async def _persist_then_hang(rep):
        raise asyncio.TimeoutError()  # body already written, later step hung → wait_for fires

    async def _report_landed(run_id):  # get_report sees the persisted partial
        return {"run_id": run_id, "risk_classification": "AMBER_LIGHT"}

    marked: dict[str, Any] = {}

    async def _fake_mark_failed(run_id, error):
        marked["run_id"] = run_id

    monkeypatch.setattr(ddo, "_orchestrate_dd_impl", _fake_impl)
    monkeypatch.setattr(ddo, "_persist_report", _persist_then_hang)
    monkeypatch.setattr(ddo, "get_report", _report_landed)
    monkeypatch.setattr(ddo, "mark_dd_failed", _fake_mark_failed)
    monkeypatch.setattr(ddo, "_finalize_dd_run", lambda *a, **k: asyncio.sleep(0))
    monkeypatch.setattr(ddo, "_dd_interactive_keepalive", lambda: asyncio.sleep(0))

    await ddo.orchestrate_dd(target={"name": "Y"}, llm=None, run_id="dd_landed_test")

    assert "run_id" not in marked, "must NOT clobber a partial that already landed"
