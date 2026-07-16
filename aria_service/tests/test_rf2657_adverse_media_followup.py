"""R-F2657 — adverse-media deep search decoupled into an out-of-band follow-up.

The 30×5×10y adverse-media search used to run INLINE at the end of the DD, competing
with synthesis for the last seconds of the 660s budget on slow/sparse targets — the
exact population that triggers it — so it starved the verdict or got budget-skipped on
the runs that needed it most. R-F2657: the DD marks it in_progress and delivers the
verdict fast; orchestrate_dd launches a detached follow-up (its OWN budget) that MERGES
the findings into the STORED report. A triggered target gets a fast verdict AND the
adverse-media depth, with neither starving the other.

These tests drive the REAL follow-up + launch paths with mocked search + state_store.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from aria_service.intel import dd_orchestrator as ddo


# ── the follow-up MERGES findings into the stored report (adverse_media only) ─

@pytest.mark.asyncio
async def test_followup_merges_findings_into_stored_report(monkeypatch) -> None:
    stored = {
        "run_id": "r1",
        "risk_classification": "RED",
        "adverse_media": {"status": "in_progress"},
        "bottom_line": "verdict already delivered",
    }
    written: dict[str, Any] = {}

    async def _fake_search(**kwargs):
        return {"ok": True, "findings_count": 3, "coverage_by_class": {"press": 1}}

    async def _fake_get_report(run_id):
        return dict(stored)

    async def _fake_set_json(key, value, *a, **k):
        written["key"] = key
        written["value"] = value

    import aria_service.intel.researcher as _res
    from aria_service.intel import redis_store
    monkeypatch.setattr(_res, "run_adverse_media_deep_search", _fake_search)
    monkeypatch.setattr(ddo, "get_report", _fake_get_report)
    monkeypatch.setattr(redis_store, "set_json", _fake_set_json)

    await ddo._run_adverse_media_followup(
        "r1", entity_name="Acme", director_names=[], ubo_names=[],
        sectors=["defence"], trigger_reason="RED",
    )

    assert written["key"] == ddo.REPORT_REDIS_KEY.format(run_id="r1")
    assert written["value"]["adverse_media"] == {"ok": True, "findings_count": 3,
                                                 "coverage_by_class": {"press": 1}}
    # merge touched ONLY adverse_media — the verdict and other fields are intact
    assert written["value"]["risk_classification"] == "RED"
    assert written["value"]["bottom_line"] == "verdict already delivered"


@pytest.mark.asyncio
async def test_followup_records_error_honestly_on_search_failure(monkeypatch) -> None:
    """Never-false-clean: a failed search leaves adverse_media marked error, not clean."""
    written: dict[str, Any] = {}

    async def _boom_search(**kwargs):
        raise RuntimeError("searxng down")

    async def _fake_get_report(run_id):
        return {"run_id": "r2", "adverse_media": {"status": "in_progress"}}

    async def _fake_set_json(key, value, *a, **k):
        written["value"] = value

    import aria_service.intel.researcher as _res
    from aria_service.intel import redis_store
    monkeypatch.setattr(_res, "run_adverse_media_deep_search", _boom_search)
    monkeypatch.setattr(ddo, "get_report", _fake_get_report)
    monkeypatch.setattr(redis_store, "set_json", _fake_set_json)

    await ddo._run_adverse_media_followup(
        "r2", entity_name="X", director_names=[], ubo_names=[],
        sectors=["defence"], trigger_reason="AMBER (AMBER_LIGHT)",
    )

    assert "error" in written["value"]["adverse_media"]
    # the error dict REPLACED the in_progress marker → no longer pending
    assert written["value"]["adverse_media"].get("status") != "in_progress"


@pytest.mark.asyncio
async def test_followup_never_raises_when_report_missing(monkeypatch) -> None:
    async def _fake_search(**kwargs):
        return {"ok": True, "findings_count": 0}

    async def _none_report(run_id):
        return None

    import aria_service.intel.researcher as _res
    monkeypatch.setattr(_res, "run_adverse_media_deep_search", _fake_search)
    monkeypatch.setattr(ddo, "get_report", _none_report)
    # must not raise even when there is no stored report to merge into
    await ddo._run_adverse_media_followup(
        "gone", entity_name="X", director_names=[], ubo_names=[],
        sectors=["defence"], trigger_reason="RED",
    )


# ── launch is GC-safe and a no-op when adverse-media was not triggered ───────

@pytest.mark.asyncio
async def test_launch_creates_tracked_task_when_triggered(monkeypatch) -> None:
    ran: dict[str, Any] = {}

    async def _fake_followup(run_id, **kwargs):
        ran["run_id"] = run_id
        ran["kwargs"] = kwargs

    monkeypatch.setattr(ddo, "_run_adverse_media_followup", _fake_followup)

    class _Rep:
        run_id = "rr"
        _am_followup = {"entity_name": "Z", "director_names": [], "ubo_names": [],
                        "sectors": ["defence"], "trigger_reason": "RED"}

    ddo._launch_adverse_media_followup(_Rep())
    await asyncio.sleep(0)  # let the created task run

    assert ran.get("run_id") == "rr"
    assert ran["kwargs"]["trigger_reason"] == "RED"


def test_launch_is_noop_without_followup_params() -> None:
    """A GREEN / non-triggered run has no _am_followup → no task, no error."""
    class _Rep:
        run_id = "rr"
        # no _am_followup attribute

    before = len(ddo._AM_FOLLOWUP_TASKS)
    ddo._launch_adverse_media_followup(_Rep())  # must be a clean no-op
    assert len(ddo._AM_FOLLOWUP_TASKS) == before


def test_pending_adverse_media_is_graded_as_not_run() -> None:
    """R-F2657 never-false-clean: a DEFERRED (in_progress) adverse-media search has NOT
    run yet → must count as not-run for grading, so a triggered target cannot over-grade
    toward Grade A (or dodge the penalty permanently if the follow-up is lost on restart)
    before real findings merge."""
    from aria_service.intel.dd_schema import _quality_metrics
    base = {"identity": {"meta": {"status": "ok"}, "registration_status": "active",
                         "directors": [1, 2]}, "confidence_gate_triggered": False}

    pending = dict(base, adverse_media={"status": "in_progress",
                                        "framework_version": "R-F2657 async follow-up"})
    m = _quality_metrics(pending)
    assert m["adverse_media_skipped"] is True, "pending adverse-media must count as not-run"
    assert m["adverse_media_run"] is False

    merged = dict(base, adverse_media={"ok": True, "findings_count": 2})
    m2 = _quality_metrics(merged)
    assert m2["adverse_media_skipped"] is False and m2["adverse_media_run"] is True
