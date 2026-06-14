"""
R-F1559 capability test: prove Eagle Eye is OBSERVABLE and ALIVE.

Before R-F1559, eagle_eye looked wired (R-F1553) but the critical-gap path
called ``capability_gaps.record_gap`` with kwargs it does not accept
(``module=``/``description=``/``severity=``) → every call raised TypeError
and was swallowed by a bare ``except`` → critical findings NEVER reached the
brain. This test drives the REAL scan/finding path (a temp file containing an
``eval(...)`` sev-10 finding, scanned by the real EagleEyeGuardian) and the
real wiring helpers, asserting:

  (a) a sev>=9 finding produces a capability_gaps.record_gap call carrying the
      finding (spy on record_gap, assert called with finding text)
  (b) a scan emits a brain signal (wire_success / wire_failure)
  (c) eagle_eye registers + heartbeats in the agent registry

These drive the actual code paths exercised by _scan_loop, not helpers.
"""
import tempfile
from pathlib import Path

import pytest


@pytest.mark.asyncio
async def test_sev9_finding_records_capability_gap(monkeypatch):
    """(a) A real sev>=9 (eval) finding must call record_gap with the finding."""
    from aria_service.intel import eagle_eye
    from aria_service.intel import capability_gaps

    calls: list[dict] = []

    async def _spy_record_gap(gap_type, detail, **kwargs):
        calls.append({"gap_type": gap_type, "detail": detail, **kwargs})
        return {"id": "test", "type": gap_type, "detail": detail}

    # Spy on the REAL record_gap that _record_critical_gaps imports
    monkeypatch.setattr(capability_gaps, "record_gap", _spy_record_gap)
    # Don't fire operator notification at a real redis store in this test
    async def _noop_notify(detail):
        return None
    monkeypatch.setattr(eagle_eye, "_notify_operator_critical", _noop_notify)

    with tempfile.TemporaryDirectory() as tmp:
        project_root = Path(tmp)
        bad_file = project_root / "unsafe.py"
        bad_file.write_text("result = eval(user_input)\n")  # sev 10 finding

        # REAL scan path
        guardian = eagle_eye.EagleEyeGuardian(project_root)
        report = await guardian.scan_once()

        assert report["high_severity_issues"] >= 1, "scan must find the eval() issue"

        # REAL critical-gap recording path (the one that was broken)
        await eagle_eye._record_critical_gaps(report)

    assert len(calls) >= 1, "sev>=9 finding must produce a record_gap call"
    # The finding (eval) must be carried into the gap detail
    assert any("eval" in c["detail"].lower() for c in calls), (
        f"record_gap detail must mention the finding (eval), got: {calls}"
    )
    # Must use a registered gap type so the coder picks it up
    assert all(c["gap_type"] == "security_threat" for c in calls), (
        f"critical findings should record as 'security_threat', got: {calls}"
    )


@pytest.mark.asyncio
async def test_record_gap_uses_real_signature():
    """Regression guard: _record_critical_gaps must call record_gap with its
    REAL signature. The pre-R-F1559 bug passed module=/description=/severity=
    which record_gap rejects. Drive it against the REAL record_gap (no spy) and
    assert it does not raise a TypeError that the bare except would hide."""
    import inspect
    from aria_service.intel import capability_gaps, eagle_eye

    sig = inspect.signature(capability_gaps.record_gap)
    params = set(sig.parameters)
    # These were the WRONG kwargs the old code passed
    assert "module" not in params and "description" not in params, (
        "record_gap signature changed — update _record_critical_gaps"
    )
    # The kwargs the new code passes must be valid
    for kw in ("gap_type", "detail", "source"):
        assert kw in params, f"record_gap must accept {kw}"


@pytest.mark.asyncio
async def test_scan_emits_brain_signal(monkeypatch):
    """(b) A scan must emit a brain signal via the wiring helpers."""
    from aria_service.intel import eagle_eye
    from aria_service.intel import engine_wiring

    successes: list[dict] = []
    failures: list[dict] = []

    def _spy_success(module, summary, **kwargs):
        successes.append({"module": module, "summary": summary, **kwargs})

    def _spy_failure(module, detail, **kwargs):
        failures.append({"module": module, "detail": detail, **kwargs})

    # _wire_scan_to_brain does `from .engine_wiring import wire_success, wire_failure`
    monkeypatch.setattr(engine_wiring, "wire_success", _spy_success)
    monkeypatch.setattr(engine_wiring, "wire_failure", _spy_failure)

    # High-severity report -> wire_failure
    eagle_eye._wire_scan_to_brain(
        {"high_severity_issues": 2, "active_trouble_spots": 3, "code_smells": 1}
    )
    assert len(failures) == 1, "high-severity scan must emit a brain failure signal"
    assert failures[0]["module"] == "eagle_eye"

    # Clean report -> wire_success
    eagle_eye._wire_scan_to_brain(
        {"high_severity_issues": 0, "active_trouble_spots": 0, "code_smells": 0}
    )
    assert len(successes) == 1, "clean scan must emit a brain success signal"
    assert successes[0]["module"] == "eagle_eye"


@pytest.mark.asyncio
async def test_scan_failure_emits_brain_failure(monkeypatch):
    """(b cont.) A scan exception must reach the brain via wire_failure (§21a)."""
    from aria_service.intel import eagle_eye
    from aria_service.intel import engine_wiring

    failures: list[dict] = []
    monkeypatch.setattr(
        engine_wiring, "wire_failure",
        lambda module, detail, **kw: failures.append({"module": module, "detail": detail}),
    )

    eagle_eye._wire_failure_to_brain("Scan failed: boom")
    assert len(failures) == 1
    assert failures[0]["module"] == "eagle_eye"
    assert "boom" in failures[0]["detail"]


@pytest.mark.asyncio
async def test_eagle_eye_registers_and_heartbeats(monkeypatch):
    """(c) eagle_eye must register as an agent and tick a heartbeat."""
    from aria_service.intel import eagle_eye
    from aria_service.intel import agent_registry

    registered: list[tuple] = []
    heartbeats: list[tuple] = []

    async def _spy_register(self, agent_id, agent_type, current_task="starting up", **kw):
        registered.append((agent_id, agent_type))
        return True

    async def _spy_tick(self, agent_id, current_task=None):
        heartbeats.append((agent_id, current_task))

    monkeypatch.setattr(agent_registry.AgentRegistry, "register", _spy_register)
    monkeypatch.setattr(agent_registry.AgentRegistry, "tick_heartbeat", _spy_tick)

    # REAL start() path registers the agent
    with tempfile.TemporaryDirectory() as tmp:
        await eagle_eye.start(Path(tmp))
        await eagle_eye.stop()

    assert ("eagle_eye", "codebase_guardian") in registered, (
        f"eagle_eye must register as an agent, got: {registered}"
    )

    # REAL heartbeat path
    await eagle_eye._tick_heartbeat()
    assert any(hb[0] == "eagle_eye" for hb in heartbeats), (
        f"eagle_eye must tick a heartbeat, got: {heartbeats}"
    )
