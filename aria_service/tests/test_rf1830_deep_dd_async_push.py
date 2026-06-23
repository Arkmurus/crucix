"""R-F1830 — async-complete-and-push for DEEP DD.

Operator (2026-06-23) asked for a "full and deep DD" on the web and got a
time-boxed PARTIAL: R-F1829 caps the inline web DD to fit the 600s SSE proxy
window, so a deep request can't run deep inline. R-F1830 closes that: when the
inline run time-boxes, ARIA launches the FULL-depth DD as a tracked background
task that persists to the user's DD Reports panel (orchestrate_dd already
persists keyed to user_id) — best of both: fast partial inline + full report
out-of-band, per CLAUDE.md §25.

CAPABILITY tests drive the real launcher + the _execute_tool wiring:
  - the launcher calls orchestrate_dd in deep mode, full budget, owned by the user
  - it is de-duped per (entity, user) so a resend can't spawn a duplicate job
  - it no-ops without a user_id (the report couldn't be routed to a panel)
  - _execute_tool launches it + emits the user-facing note when the inline run
    time-boxes, and does NOT when the run completed in time
"""
from __future__ import annotations

import asyncio
import types

import pytest

from aria_service.intel import dd_orchestrator as ddo
from aria_service.routes import aria as aria_routes


class _StubLLM:
    is_configured = True


def _reset_bg_state():
    aria_routes._DD_DEEP_BG_INFLIGHT.clear()
    aria_routes._DD_DEEP_BG_TASKS.clear()


def _fake_report(*, time_boxed: bool):
    """Minimal stand-in with exactly the attributes the dd-branch return path
    touches — so _execute_tool runs without a real 7-layer report."""
    ident = types.SimpleNamespace(
        directors=[], registration_number="", incorporation_date="",
        jurisdiction_iso2="", entity_name="Modirum Gespi",
    )
    digital = types.SimpleNamespace(press_coverage=[])
    return types.SimpleNamespace(
        identity=ident, digital=digital, ecosystem_status={},
        confidence_gate_triggered=False, run_id="dd_test", layers_run=["identity"],
        risk_classification="AMBER-LIGHT", total_duration_ms=1000,
        time_boxed=time_boxed,
        render_markdown=lambda concise=False: "## fake ARK-DD report",
    )


@pytest.mark.asyncio
async def test_rf1830_launcher_runs_deep_full_budget_owned(monkeypatch):
    _reset_bg_state()
    calls = []

    async def _fake_orch(*a, **k):
        calls.append(k)
        return object()

    monkeypatch.setattr(ddo, "orchestrate_dd", _fake_orch)

    launched = aria_routes._launch_deep_dd_bg(
        {"name": "Modirum Gespi"}, _StubLLM(), user_id="u1", user_email=None
    )
    assert launched is True
    await asyncio.gather(*list(aria_routes._DD_DEEP_BG_TASKS))

    assert len(calls) == 1, "deep-bg DD did not run exactly once"
    assert calls[0]["mode"] == "deep", "background DD must run in deep mode"
    assert calls[0]["user_id"] == "u1", "report not owned by the requesting user"
    assert calls[0]["total_budget_s"] >= 600, (
        f"background DD budget {calls[0]['total_budget_s']} is not a FULL-depth "
        f"budget — defeats the point of the async push."
    )
    # in-flight key cleared after completion
    assert not aria_routes._DD_DEEP_BG_INFLIGHT


@pytest.mark.asyncio
async def test_rf1830_dedup_blocks_duplicate_job(monkeypatch):
    _reset_bg_state()
    gate = asyncio.Event()

    async def _block_orch(*a, **k):
        await gate.wait()

    monkeypatch.setattr(ddo, "orchestrate_dd", _block_orch)

    first = aria_routes._launch_deep_dd_bg({"name": "Acme Corp"}, _StubLLM(), user_id="u1", user_email=None)
    second = aria_routes._launch_deep_dd_bg({"name": "Acme Corp"}, _StubLLM(), user_id="u1", user_email=None)
    assert first is True, "first launch should fire"
    assert second is False, "duplicate (same entity+user) must be de-duped — the recurring duplicate-job pain"

    gate.set()
    await asyncio.gather(*list(aria_routes._DD_DEEP_BG_TASKS))


def test_rf1830_no_user_id_no_launch():
    _reset_bg_state()
    assert aria_routes._launch_deep_dd_bg({"name": "Z Corp"}, _StubLLM(), user_id="", user_email=None) is False
    assert not aria_routes._DD_DEEP_BG_TASKS


@pytest.mark.asyncio
async def test_rf1830_execute_tool_pushes_when_timeboxed(monkeypatch):
    """When the inline run time-boxes AND a user_id is present, _execute_tool
    launches the deep-bg job and emits the verbatim user-facing note."""
    _reset_bg_state()

    async def _fake_orch(*a, **k):
        return _fake_report(time_boxed=True)

    monkeypatch.setattr(ddo, "orchestrate_dd", _fake_orch)

    launched_flag = {"n": 0}

    def _fake_launch(target, llm, *, user_id, user_email):
        launched_flag["n"] += 1
        return True

    monkeypatch.setattr(aria_routes, "_launch_deep_dd_bg", _fake_launch)

    out = await aria_routes._execute_tool(
        {"tool": "dd_orchestrate", "name": "Modirum Gespi", "skip_doc_gate": True},
        _StubLLM(), dd_budget_s=240.0, user_id="u1",
    )

    assert launched_flag["n"] == 1, "time-boxed inline run did not launch the deep-bg DD"
    assert "DEEP-DD BACKGROUND JOB STARTED" in out
    assert "full-depth DD in the background" in out, "user-facing note missing"


@pytest.mark.asyncio
async def test_rf1830_no_push_when_complete(monkeypatch):
    """Guard against over-firing: a run that completed in time (not time-boxed)
    must NOT spawn a redundant (costly) background job."""
    _reset_bg_state()

    async def _fake_orch(*a, **k):
        return _fake_report(time_boxed=False)

    monkeypatch.setattr(ddo, "orchestrate_dd", _fake_orch)

    launched_flag = {"n": 0}
    monkeypatch.setattr(
        aria_routes, "_launch_deep_dd_bg",
        lambda *a, **k: launched_flag.__setitem__("n", launched_flag["n"] + 1) or True,
    )

    out = await aria_routes._execute_tool(
        {"tool": "dd_orchestrate", "name": "Modirum Gespi", "skip_doc_gate": True},
        _StubLLM(), dd_budget_s=240.0, user_id="u1",
    )
    assert launched_flag["n"] == 0, "completed run wrongly spawned a background DD"
    assert "DEEP-DD BACKGROUND JOB STARTED" not in out
