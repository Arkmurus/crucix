"""R-F1292 — Capability test: web_integrity wires its SUCCESS branch (§21a).

Before R-F1292 the brain-wiring call sat inside `if errors_found > 0`, so a clean
monitoring cycle emitted nothing — the success branch was dark (ARIA's wiring
audit flagged web_integrity, correctly on this point). Now a clean cycle emits a
lightweight engine_wiring.wire_success (NOT brain_hook.absorb, to avoid inflating
the composite on this high-frequency loop), while the failure path keeps absorb.
"""
from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock

import aria_service.intel.web_integrity_agent as wia


def _agent():
    a = wia.WebIntegrityAgent.__new__(wia.WebIntegrityAgent)
    a._detector = wia.ErrorPatternDetector()
    a._redis = None
    a._brain_hook = AsyncMock()
    a._save_last_check = AsyncMock()
    a._get_last_cred_verify = AsyncMock(return_value=time.time())
    return a


def _patch_checks(monkeypatch, passed: bool):
    async def _chk(ep):
        path = ep.get("path") or ep.get("name") or "x"
        return wia.IntegrityCheck(endpoint=str(path), method="GET", passed=passed)
    monkeypatch.setattr(wia, "check_endpoint", _chk)
    monkeypatch.setattr(wia, "check_endpoint_public", _chk)


def test_rf1292_clean_cycle_emits_wire_success(monkeypatch):
    calls = []
    monkeypatch.setattr("aria_service.intel.engine_wiring.wire_success",
                        lambda **kw: calls.append(kw))
    _patch_checks(monkeypatch, passed=True)

    a = _agent()
    asyncio.run(a._one_cycle())

    assert calls, "a clean integrity cycle must emit wire_success (§21a)"
    assert calls[0].get("module") == "web_integrity"
    # success heartbeat must NOT go through brain_hook.absorb (composite-safe)
    a._brain_hook.absorb.assert_not_called()


def test_rf1292_failure_cycle_does_not_emit_success(monkeypatch):
    calls = []
    monkeypatch.setattr("aria_service.intel.engine_wiring.wire_success",
                        lambda **kw: calls.append(kw))
    # make endpoints fail (non-critical so _escalate_critical isn't needed)
    async def _fail(ep):
        c = wia.IntegrityCheck(endpoint=str(ep.get("path", "x")), method="GET", passed=False)
        c.errors = ["boom"]
        return c
    monkeypatch.setattr(wia, "check_endpoint", _fail)
    monkeypatch.setattr(wia, "check_endpoint_public", _fail)
    # ensure no endpoint is marked critical for this test
    monkeypatch.setattr(wia, "WEB_ENDPOINTS", [{"path": "/x", "critical": False}])
    monkeypatch.setattr(wia, "_WEB_ENDPOINTS_PUBLIC", [{"path": "/y", "critical": False}])

    a = _agent()
    a._record_error = AsyncMock()
    asyncio.run(a._one_cycle())

    assert not calls, "a failing cycle must NOT emit a success heartbeat"
    # R-F2026 — the failing cycle routes through _record_error (which itself wires
    # the brain via the R-F1598 lightweight record_signal path); this test mocks
    # _record_error, so assert the failure reached that handler — NOT the old
    # absorb path (R-F1598 removed absorb from web_integrity wiring).
    a._record_error.assert_awaited()
