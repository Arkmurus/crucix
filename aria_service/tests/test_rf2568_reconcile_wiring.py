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
