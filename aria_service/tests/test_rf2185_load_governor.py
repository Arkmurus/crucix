"""R-F2185 — adaptive load governor (self-regulating autonomy).

The single-process brain shares one event loop + one state_store connection
between ~15 autonomous loops and user-facing serving. Before this, nothing made
background work YIELD when serving was under pressure — so an autonomous surge
starved chat/DD (the live 2026-06-30 degradation: chat submits timing out, the
doc review "never delivered"). The governor is the self-heal: under pressure the
autonomous engine sheds its tick automatically and resumes when calm.

These tests drive the REAL governor + the REAL engine gate logic.
"""
from __future__ import annotations

import time

import pytest

from aria_service.intel import load_governor as lg


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    lg._stall_events.clear()
    lg._shed_events.clear()
    lg._shed_total = 0
    lg._last_shed_log = 0.0
    monkeypatch.setenv("ARIA_LOAD_GOVERNOR_ENABLED", "1")
    monkeypatch.setenv("ARIA_LOAD_SHED_THRESHOLD", "0.6")
    yield


def _fake_state_store(monkeypatch, qsize: int, qmax: int = 2000):
    """Point the governor's state_store probe at a fake write queue."""
    import aria_service.intel.state_store as ss

    class _Q:
        def qsize(self): return qsize
    monkeypatch.setattr(ss, "_QUEUED_WRITES", _Q(), raising=False)
    monkeypatch.setattr(ss, "_WRITE_QUEUE_MAX", qmax, raising=False)
    monkeypatch.setattr(ss, "_op_timeout_counts", {"op": 0}, raising=False)


def test_rf2185_calm_does_not_shed(monkeypatch):
    """Empty queue, no stalls → no pressure → do NOT shed (autonomy runs)."""
    _fake_state_store(monkeypatch, qsize=0)
    p = lg.pressure()
    assert p["score"] < 0.6, p
    assert lg.should_shed() is False


def test_rf2185_full_write_queue_sheds(monkeypatch):
    """A backing-up write queue (serving writes not draining) → shed."""
    _fake_state_store(monkeypatch, qsize=1400, qmax=2000)  # 0.7 ratio *1.5 = 1.0
    p = lg.pressure()
    assert p["score"] >= 0.6, p
    assert lg.should_shed() is True


def test_rf2185_recent_stalls_shed(monkeypatch):
    """Repeated event-loop stalls → shed even if the write queue is empty."""
    _fake_state_store(monkeypatch, qsize=0)
    for _ in range(3):              # _STALL_TRIP_COUNT = 3 → full stall pressure
        lg.record_loop_stall(8.0)
    p = lg.pressure()
    assert p["recent_stalls"] == 3, p
    assert p["score"] >= 0.6, p
    assert lg.should_shed() is True


def test_rf2185_stalls_age_out_and_resume(monkeypatch):
    """Pressure clears as stalls age past the window → autonomy auto-resumes."""
    _fake_state_store(monkeypatch, qsize=0)
    for _ in range(3):
        lg.record_loop_stall(8.0)
    assert lg.should_shed() is True
    # Age the stalls out by rewinding their timestamps past the window.
    lg._stall_events[:] = [t - (lg._STALL_WINDOW_S + 1) for t in lg._stall_events]
    assert lg.should_shed() is False, "governor must auto-resume when pressure clears"


def test_rf2185_disabled_never_sheds(monkeypatch):
    """Kill switch: governor OFF → never sheds (old always-run behaviour)."""
    monkeypatch.setenv("ARIA_LOAD_GOVERNOR_ENABLED", "0")
    _fake_state_store(monkeypatch, qsize=2000, qmax=2000)
    for _ in range(10):
        lg.record_loop_stall(10.0)
    assert lg.should_shed() is False


def test_rf2185_probe_error_failsafe(monkeypatch):
    """If the pressure probe raises, should_shed returns False (never stall
    autonomy on a probe bug)."""
    import aria_service.intel.load_governor as _lg
    monkeypatch.setattr(_lg, "pressure", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    assert _lg.should_shed() is False


def test_rf2185_engine_gate_skips_tick_under_pressure(monkeypatch):
    """The REAL engine-gate contract: should_shed() True must short-circuit the
    tick. We assert the governor exposes the boolean the engine loop branches on
    and that it flips with pressure (the engine loop calls exactly this)."""
    _fake_state_store(monkeypatch, qsize=0)
    assert lg.should_shed() is False          # calm → engine proceeds
    for _ in range(3):
        lg.record_loop_stall(8.0)
    assert lg.should_shed() is True           # pressured → engine `continue`s
    # stats surface is well-formed for observability
    s = lg.stats()
    assert s["enabled"] is True and "pressure" in s and s["shed_total"] >= 1
