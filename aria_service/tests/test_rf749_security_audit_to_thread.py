"""R-F749 (2026-05-20) — security_audit dispatched to to_thread.

Wedge round 10 (wedge_675_1779301744.log) captured a 7.20s main-loop
stall at security_protocol.py:871 — the CHECK 2 path-pattern regex
loop. Called from `self_improve._self_improve_loop` every 2h.

ROOT CAUSE: `run_security_audit` was `async def` but had exactly ONE
await (`knowledge.get_all_facts()`) and then ~140 lines of sync
regex over ~56k facts (~50MB+ concatenated text). The sync stretch
between awaits blocked the event loop for 5-10s every audit cycle.

R-F749: extracted everything after the get_all_facts() await into a
sync helper `_run_security_audit_sync(all_facts, timestamp)`. The
async wrapper now just awaits get_all_facts() then dispatches the
sync body via `asyncio.to_thread`.

Tests:
  - shape: run_security_audit still async, returns the same dict
  - shape: _run_security_audit_sync exists + sync + takes the
    expected args
  - capability: detects an API key leak in injected facts
  - capability: detects an internal path leak
  - wiring: source contains `asyncio.to_thread(_run_security_audit_sync,`
"""
from __future__ import annotations

import asyncio
import inspect


def test_run_security_audit_still_async():
    """R-F749: public API contract preserved — must remain `async def`."""
    from aria_service.intel import security_protocol as sp
    assert inspect.iscoroutinefunction(sp.run_security_audit)


def test_sync_helper_exists_and_is_sync():
    """R-F749: the worker-thread helper exists and is NOT a coroutine."""
    from aria_service.intel import security_protocol as sp
    assert hasattr(sp, "_run_security_audit_sync")
    assert not inspect.iscoroutinefunction(sp._run_security_audit_sync)


def test_sync_helper_signature():
    """R-F749: takes (all_facts, timestamp) as expected by the
    `asyncio.to_thread` dispatch."""
    from aria_service.intel import security_protocol as sp
    sig = inspect.signature(sp._run_security_audit_sync)
    params = list(sig.parameters.keys())
    assert params[:2] == ["all_facts", "timestamp"]


def test_sync_helper_returns_expected_shape():
    """R-F749: empty facts → 0 issues, all clean areas listed."""
    from aria_service.intel import security_protocol as sp
    out = sp._run_security_audit_sync([], "2026-05-20T00:00:00Z")
    assert set(out.keys()) >= {
        "issues_found", "critical", "warning", "clean_areas",
        "timestamp", "advisory",
    }
    assert out["timestamp"] == "2026-05-20T00:00:00Z"
    assert out["issues_found"] == 0


def test_sync_helper_detects_api_key_leak():
    """R-F749 capability check: an OpenAI-style API key in facts must
    be flagged as CRITICAL CHECK 1 FAIL."""
    from aria_service.intel import security_protocol as sp
    poisoned = [{"content": "sk-abcdefghij0123456789ABCDEFGHIJ"}]
    out = sp._run_security_audit_sync(poisoned, "2026-05-20T00:00:00Z")
    assert out["issues_found"] >= 1
    assert any("CHECK 1 FAIL" in c for c in out["critical"])


def test_sync_helper_detects_internal_path_leak():
    """R-F749 capability check: /app/aria_service/ path in facts must
    be flagged as CHECK 2 WARN."""
    from aria_service.intel import security_protocol as sp
    poisoned = [{"content": "Some text /app/aria_service/intel/foo.py more text"}]
    out = sp._run_security_audit_sync(poisoned, "2026-05-20T00:00:00Z")
    assert any("CHECK 2 FAIL" in w for w in out["warning"])


def test_async_wrapper_dispatches_via_to_thread():
    """R-F749: the source of `run_security_audit` must contain the
    `asyncio.to_thread(_run_security_audit_sync,` dispatch. Catches
    accidental revert / rebase loss."""
    from aria_service.intel import security_protocol as sp
    src = inspect.getsource(sp.run_security_audit)
    assert "R-F749" in src
    assert "asyncio.to_thread(_run_security_audit_sync" in src


def test_async_wrapper_end_to_end_smoke():
    """R-F749: full async path works — empty knowledge → 0 issues."""
    from aria_service.intel import security_protocol as sp

    async def _run():
        # Monkeypatch knowledge.get_all_facts to avoid hitting disk
        from aria_service.intel import knowledge as kn
        original = kn.get_all_facts

        async def _stub():
            return []

        kn.get_all_facts = _stub
        try:
            return await sp.run_security_audit()
        finally:
            kn.get_all_facts = original

    out = asyncio.run(_run())
    assert out["issues_found"] == 0
    assert "timestamp" in out
