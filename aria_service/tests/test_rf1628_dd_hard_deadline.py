"""R-F1628 — the DD must NEVER hang past its budget. The orchestrator's
per-layer clamp doesn't bound unclamped work (predictor/synthesis/absorbs); a
real deep DD ran 781s against a 660s budget and returned NOTHING (WA poll window
closed → no delivery). The orchestrate_dd wrapper now hard-bounds the whole run
and returns the accumulated PARTIAL report instead of hanging.

Drives the REAL wrapper with a deliberately-overrunning impl and asserts:
  (1) it returns within budget+margin (never hangs),
  (2) it returns a real ARKDDReport with an honest 'time-budget' bottom line,
  (3) the accumulated partial (whatever the impl stashed) is preserved.
"""
from __future__ import annotations

import asyncio
import time

import pytest

from aria_service.intel import dd_orchestrator as DD
from aria_service.intel.dd_schema import ARKDDReport, RiskClassification


@pytest.mark.asyncio
async def test_hard_deadline_returns_partial_not_hang(monkeypatch):
    # Tiny budget + margin so the test is fast; the wrapper reads these envs.
    monkeypatch.setenv("ARIA_DD_TOTAL_BUDGET_S", "1")
    monkeypatch.setenv("ARIA_DD_HARD_MARGIN_S", "1")  # hard deadline = 2s

    async def _slow_impl(target, *, _report_holder=None, **kw):
        # Stash a partial report (as the real impl does early), then "hang"
        # well past the 2s hard deadline doing unclamped work.
        rep = ARKDDReport(target=target, orchestrator_mode=kw.get("mode", "standard"),
                          trace_id=kw.get("trace_id"))
        rep.identity.entity_name = target.get("name", "")
        rep.identity.findings.append("layer-1 completed before the overrun")
        if _report_holder is not None:
            _report_holder["report"] = rep
        await asyncio.sleep(30)  # simulate the unclamped overrun
        return rep

    monkeypatch.setattr(DD, "_orchestrate_dd_impl", _slow_impl)

    start = time.monotonic()
    report = await DD.orchestrate_dd(target={"name": "Slow Co", "type": "company"}, mode="deep")
    elapsed = time.monotonic() - start

    # (1) returned within budget+margin (+ small scheduling slack), never hung 30s
    assert elapsed < 6, f"hard deadline must bound the run; took {elapsed:.1f}s"
    # (2) honest, real report
    assert isinstance(report, ARKDDReport)
    assert report.risk_classification == RiskClassification.AMBER_LIGHT.value
    assert "TIME-BUDGET" in report.bottom_line.upper()
    assert any("time budget" in g.lower() for g in report.data_gaps_summary)
    # (3) the accumulated partial was preserved (not a bare stub)
    assert report.identity.entity_name == "Slow Co"
    assert any("layer-1 completed" in f for f in report.identity.findings)


@pytest.mark.asyncio
async def test_fast_dd_returns_normally_unaffected(monkeypatch):
    monkeypatch.setenv("ARIA_DD_TOTAL_BUDGET_S", "5")
    monkeypatch.setenv("ARIA_DD_HARD_MARGIN_S", "5")

    async def _fast_impl(target, *, _report_holder=None, **kw):
        rep = ARKDDReport(target=target, orchestrator_mode="deep", trace_id=None)
        rep.identity.entity_name = target.get("name", "")
        rep.risk_classification = RiskClassification.GREEN.value
        rep.bottom_line = "all clear"
        if _report_holder is not None:
            _report_holder["report"] = rep
        return rep

    monkeypatch.setattr(DD, "_orchestrate_dd_impl", _fast_impl)
    report = await DD.orchestrate_dd(target={"name": "Fast Co"}, mode="deep")
    # a normal fast run passes through untouched — no deadline interference
    assert report.risk_classification == RiskClassification.GREEN.value
    assert report.bottom_line == "all clear"
