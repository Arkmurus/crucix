"""R-F961 — self_introspect surfaces REAL degraded_reasons + live autonomy.

Live incident (2026-05-28): ARIA's self-gap-analysis over WhatsApp claimed
"a subsystem needs attention (DEGRADED)" and "(autonomy fields UNAVAILABLE)" —
confabulating a mystery failing subsystem when:
  - degraded_reasons was simply ['mode_supervised'] (a deliberate review-gating
    POSTURE, not a broken subsystem), and
  - the autonomous engine was actually enabled + running.

self_introspect_context_block never surfaced the operating_mode / degraded_reasons
and the AUTONOMY block printed "(autonomy fields UNAVAILABLE)" because
/health/perf's `autonomy` dict lacked the field names. R-F961 wires in the live
/api/aria/health (degraded_reasons) + /api/aria/autonomous/status (engine state),
with a guard note so the LLM cannot turn 'mode_supervised' into a phantom fault.
"""
from __future__ import annotations

import asyncio

import pytest

from aria_service.intel import self_introspect_guard as sig


def _patch_all(monkeypatch, *, mode="SUPERVISED", reasons=None,
               engine=None, tasks=None):
    from aria_service.routes import aria as routes

    async def _perf():
        return {"inventory": {"knowledge_facts": 71041}, "retention": {},
                "autonomy": {}, "advisories": []}

    async def _health():
        return {"status": "degraded" if reasons else "healthy",
                "operating_mode": mode,
                "degraded_reasons": reasons if reasons is not None else ["mode_supervised"]}

    async def _auton():
        return {"ok": True,
                "engine": engine if engine is not None else {
                    "enabled": True, "running": True, "autonomy_label": "L3 FULL",
                    "autonomy_level": 3, "fire_count": 87},
                "safety": {"engine_paused": False},
                "tasks": tasks if tasks is not None else [{"id": "t1"}, {"id": "t2"}]}

    monkeypatch.setattr(routes, "health_perf_ep", _perf, raising=False)
    monkeypatch.setattr(routes, "health_check_ep", _health, raising=False)
    monkeypatch.setattr(routes, "autonomous_status_ep", _auton, raising=False)


def test_rf961_status_section_with_supervised_guard(monkeypatch):
    _patch_all(monkeypatch)
    block = asyncio.run(
        sig.self_introspect_context_block("give me a full system gap analysis"))
    assert "STATUS:" in block
    assert "operating_mode: SUPERVISED" in block
    assert "degraded_reasons: ['mode_supervised']" in block
    # the exact guard that kills the confabulation
    assert "NO subsystem is" in block
    assert "SUPERVISED posture" in block
    assert "Do NOT claim a subsystem needs attention" in block


def test_rf961_autonomy_shows_live_engine_not_unavailable(monkeypatch):
    _patch_all(monkeypatch)
    block = asyncio.run(
        sig.self_introspect_context_block("how many tasks do you have scheduled"))
    assert "enabled: True" in block
    assert "running: True" in block
    assert "fire_count: 87" in block
    assert "scheduled_tasks: 2" in block
    assert "autonomy fields UNAVAILABLE" not in block
    # engine-vs-mode disambiguation: SUPERVISED must not be read as "autonomy off"
    assert "does NOT stop the engine" in block


def test_rf961_named_degraded_reason_is_cited_not_called_unknown(monkeypatch):
    _patch_all(monkeypatch, mode="NORMAL", reasons=["rag_unavailable"])
    block = asyncio.run(
        sig.self_introspect_context_block("self-assessment please"))
    assert "rag_unavailable" in block
    assert "NAMED here" in block
    # the supervised-only note must NOT appear for a real fault
    assert "NO subsystem is" not in block


def test_rf961_degrades_gracefully_when_status_endpoints_fail(monkeypatch):
    """If /health or /autonomous/status throw, the block still renders the
    inventory and never fabricates — the try/except keeps it safe."""
    from aria_service.routes import aria as routes

    async def _perf():
        return {"inventory": {"knowledge_facts": 71041}, "retention": {},
                "autonomy": {}, "advisories": []}

    async def _boom():
        raise RuntimeError("status probe down")

    monkeypatch.setattr(routes, "health_perf_ep", _perf, raising=False)
    monkeypatch.setattr(routes, "health_check_ep", _boom, raising=False)
    monkeypatch.setattr(routes, "autonomous_status_ep", _boom, raising=False)

    block = asyncio.run(
        sig.self_introspect_context_block("what are your capabilities?"))
    # no STATUS section (status probe failed) but the block still renders
    assert "[TOOL: self_introspect" in block
    assert "71,041" in block
    # autonomy falls back to the perf dict → UNAVAILABLE, never fabricated
    assert "autonomy fields UNAVAILABLE" in block
