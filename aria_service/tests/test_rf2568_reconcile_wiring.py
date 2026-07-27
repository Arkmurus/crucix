"""R-F2568 — dd_reconcile + outcome_reconcile loops must wire their failures to the brain.

These reconcile loops previously swallowed failures to logger.debug (DARK, §21d). The
dd reconciler is the ONLY thing that clears orphaned status='running' DDs after a
restart/wedge (R-F2300) and it fails on the exact state_store wedge it's needed for —
so a silent failure = user DDs hang with no self-heal. Capability test drives the REAL
one-pass helpers and asserts wire_failure lands on failure, and is NOT called on success.
"""
from __future__ import annotations

import asyncio

from aria_service import main as M
from aria_service.intel import dd_orchestrator as ddo
from aria_service.intel import outcome_wire as ow
from aria_service.intel import engine_wiring as ew


def _spy(monkeypatch) -> list:
    calls: list = []
    monkeypatch.setattr(ew, "wire_failure", lambda **k: calls.append(k))
    return calls


def test_dd_reconcile_wires_failure(monkeypatch):
    calls = _spy(monkeypatch)
    async def _boom():
        raise RuntimeError("state_store wedge")
    monkeypatch.setattr(ddo, "reconcile_stale_running_dds", _boom)
    asyncio.run(M._dd_reconcile_once())
    assert any(c.get("module") == "dd_reconcile" for c in calls), "dd reconcile failure not wired"
    assert any(c.get("gap_type") == "engine_failure" for c in calls)


def test_dd_reconcile_silent_on_success(monkeypatch):
    calls = _spy(monkeypatch)
    async def _ok():
        return {"cleared": 0}
    monkeypatch.setattr(ddo, "reconcile_stale_running_dds", _ok)
    asyncio.run(M._dd_reconcile_once())
    assert calls == [], "must NOT wire a failure on a successful reconcile"


def test_rf3288_a_reconcile_that_did_work_is_not_silent(monkeypatch):
    """R-F3288 — §21a wants BOTH branches wired, and only failure was.

    reconcile_stale_running_dds counts what it did (scanned / reconciled /
    resumed), logs it, and returns a dict that its ONLY production caller
    (main.py:441) discards without assigning. So the work itself reached no sink:
    a pass that re-launched five restart-killed DDs was indistinguishable from one
    that found nothing.

    That matters because this is the only thing that clears orphaned 'running'
    DDs after a restart. If it silently stops resuming, the first evidence is a
    user's DD hanging, which is the R-F2300 failure it exists to prevent.

    Note what is NOT the fix: adding `resumed` to the returned dict. The caller
    throws the dict away, so that would have created a field nobody reads, which
    is the producer-with-no-consumer defect this session kept finding.
    """
    successes: list = []
    monkeypatch.setattr(ew, "wire_success", lambda **k: successes.append(k))
    calls = _spy(monkeypatch)

    async def _did_work():
        return {"scanned": 7, "reconciled": 2, "resumed": 5}
    monkeypatch.setattr(ddo, "reconcile_stale_running_dds", _did_work)
    monkeypatch.setattr(ddo, "reconcile_pending_adverse_media",
                        lambda *a, **k: _noop())
    asyncio.run(M._dd_reconcile_once())

    assert calls == [], "a successful reconcile must not wire a failure"
    assert successes, "a reconcile that resumed 5 DDs reached no sink at all"
    blob = " ".join(str(v) for c in successes for v in c.values())
    assert "5" in blob and "2" in blob, (
        "the signal must carry WHAT WAS DONE; 'it ran' is not an outcome"
    )


def test_rf3288_an_idle_reconcile_stays_quiet(monkeypatch):
    """No work, no signal. A heartbeat on every 600s pass would bury the real ones."""
    successes: list = []
    monkeypatch.setattr(ew, "wire_success", lambda **k: successes.append(k))
    _spy(monkeypatch)

    async def _idle():
        return {"scanned": 3, "reconciled": 0, "resumed": 0}
    monkeypatch.setattr(ddo, "reconcile_stale_running_dds", _idle)
    monkeypatch.setattr(ddo, "reconcile_pending_adverse_media",
                        lambda *a, **k: _noop())
    asyncio.run(M._dd_reconcile_once())
    assert successes == [], "an idle pass must not emit a signal"


async def _noop():
    return {"scanned": 0, "relaunched": 0}


def test_outcome_reconcile_wires_per_surface_failure(monkeypatch):
    calls = _spy(monkeypatch)
    monkeypatch.setattr(ow, "KNOWN_SURFACES", ["wa", "web"])
    async def _boom(surface):
        raise RuntimeError("pending-set read failed")
    monkeypatch.setattr(ow, "reconcile_silent_drops", _boom)
    asyncio.run(M._outcome_reconcile_once())
    wired = [c for c in calls if c.get("module") == "outcome_reconcile"]
    assert len(wired) == 2, f"expected a wire per failing surface, got {len(wired)}"


def test_outcome_reconcile_silent_on_success(monkeypatch):
    calls = _spy(monkeypatch)
    monkeypatch.setattr(ow, "KNOWN_SURFACES", ["wa"])
    async def _ok(surface):
        return 0
    monkeypatch.setattr(ow, "reconcile_silent_drops", _ok)
    asyncio.run(M._outcome_reconcile_once())
    assert calls == []


if __name__ == "__main__":
    import sys
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
