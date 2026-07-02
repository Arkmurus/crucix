"""R-F2277 — state_store liveness watchdog (escalating self-heal).

Root cause of the 2026-07-02 3.5h outage: the single aiosqlite writer thread
wedged; asyncio.wait_for cancels the awaiting coroutine but cannot interrupt the
running thread, so every op timed out forever. The event-loop watchdog (R-F1417)
never fired (the loop stayed healthy — ops timed out and returned defaults), and
reconnect only self-heals on 'closed'-string errors, never a TimeoutError. So
nothing escalated to a process restart. This watchdog is that missing recovery
actor: probe the store; on sustained failure reconnect, then os._exit past a
ceiling so Fly cold-boots.

These capability tests drive the REAL watchdog loop + the REAL probe against a
real temp-file store — asserting the user-visible outcome (does it force a
restart when wedged? does it stay hands-off when healthy / disabled?).
"""
from __future__ import annotations

import asyncio
import os
import tempfile

import pytest

from aria_service.intel import state_store as _ss


class _ForcedExit(BaseException):
    """Sentinel for a faked os._exit. BaseException (not Exception) so it escapes
    the watchdog loop's `except Exception: continue` guard, exactly as a real
    os._exit terminates the process rather than being swallowed."""


# ─────────────────────────── pure predicate ────────────────────────────────
class TestShouldRestartPredicate:
    def test_fires_only_when_enabled_armed_and_past_ceiling(self):
        assert _ss._should_restart_for_wedge(200.0, armed=True, enabled=True, ceiling_s=180.0) is True

    def test_no_fire_under_ceiling(self):
        assert _ss._should_restart_for_wedge(120.0, armed=True, enabled=True, ceiling_s=180.0) is False

    def test_no_fire_when_disabled(self):
        assert _ss._should_restart_for_wedge(999.0, armed=True, enabled=False, ceiling_s=180.0) is False

    def test_no_fire_when_not_armed(self):
        # never restart during the cold-boot settle window
        assert _ss._should_restart_for_wedge(999.0, armed=False, enabled=True, ceiling_s=180.0) is False

    def test_bad_input_is_false_not_raise(self):
        assert _ss._should_restart_for_wedge(None, armed=True, enabled=True, ceiling_s=180.0) is False


# ─────────────────────────── real probe round-trip ─────────────────────────
class TestProbeLiveness:
    @pytest.fixture(autouse=True)
    async def _fresh_store(self, monkeypatch):
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        db_path = tmp.name
        tmp.close()
        monkeypatch.setenv("ARIA_STATE_DB_PATH", db_path)
        if _ss._conn is not None:
            await _ss.close()
        await _ss.connect()
        yield
        try:
            await _ss.close()
        except Exception:
            pass
        try:
            os.unlink(db_path)
        except Exception:
            pass

    @pytest.mark.asyncio
    async def test_probe_true_on_healthy_store(self):
        assert await _ss.probe_liveness(timeout_s=5.0) is True

    @pytest.mark.asyncio
    async def test_probe_false_when_read_raises(self, monkeypatch):
        async def _boom(*a, **k):
            raise RuntimeError("simulated wedge")
        monkeypatch.setattr(_ss, "get", _boom)
        assert await _ss.probe_liveness(timeout_s=1.0) is False

    @pytest.mark.asyncio
    async def test_probe_false_when_flush_hangs(self, monkeypatch):
        async def _hang(*a, **k):
            await asyncio.sleep(30)
        monkeypatch.setattr(_ss, "_flush_write_queue", _hang)
        # short per-op timeout so the hang is detected fast (the exact wedge shape)
        assert await _ss.probe_liveness(timeout_s=0.1) is False


# ─────────────────────────── watchdog escalation ───────────────────────────
class TestWatchdogEscalation:
    @pytest.fixture(autouse=True)
    def _fast_thresholds_and_stubs(self, monkeypatch):
        # tiny windows so the escalation runs in ~0.1s, not minutes
        monkeypatch.setenv("ARIA_SS_WATCHDOG_SETTLE_S", "0.0")
        monkeypatch.setenv("ARIA_SS_WATCHDOG_INTERVAL_S", "0.02")
        monkeypatch.setenv("ARIA_SS_WATCHDOG_RECONNECT_S", "0.04")
        monkeypatch.setenv("ARIA_SS_WATCHDOG_CEILING_S", "0.10")
        # never touch a real DB from the loop's self-heal step
        self.reconnect_calls = 0
        self.ensure_calls = 0
        async def _fake_reconnect():
            self.reconnect_calls += 1
        async def _fake_ensure():
            self.ensure_calls += 1
        monkeypatch.setattr(_ss, "_reconnect", _fake_reconnect)
        monkeypatch.setattr(_ss, "_ensure_read_conn", _fake_ensure)
        # capture the dangerous os._exit
        self.exit_called = False
        def _fake_exit(code):
            self.exit_called = True
            raise _ForcedExit(code)
        monkeypatch.setattr(os, "_exit", _fake_exit)
        _ss._ss_wd_unhealthy_since = None
        _ss._ss_wd_reconnect_fired = False

    @pytest.mark.asyncio
    async def test_forces_exit_when_store_wedged(self, monkeypatch):
        async def _always_fail(*a, **k):
            return False
        monkeypatch.setattr(_ss, "probe_liveness", _always_fail)
        with pytest.raises(_ForcedExit):
            await asyncio.wait_for(_ss.liveness_watchdog_loop(), timeout=5.0)
        assert self.exit_called is True
        # reconnect must have been attempted BEFORE escalating to a restart
        await asyncio.sleep(0)  # let the scheduled create_task run
        assert self.reconnect_calls >= 1, "in-process reconnect must fire before os._exit"

    @pytest.mark.asyncio
    async def test_no_exit_when_disabled(self, monkeypatch):
        monkeypatch.setenv("ARIA_STATE_STORE_WATCHDOG_ENABLED", "0")
        async def _always_fail(*a, **k):
            return False
        monkeypatch.setattr(_ss, "probe_liveness", _always_fail)
        # returns immediately (kill-switch) — no probe, no exit
        await asyncio.wait_for(_ss.liveness_watchdog_loop(), timeout=2.0)
        assert self.exit_called is False

    @pytest.mark.asyncio
    async def test_no_exit_when_self_restart_off(self, monkeypatch):
        monkeypatch.setenv("ARIA_SS_WATCHDOG_SELF_RESTART", "0")
        async def _always_fail(*a, **k):
            return False
        monkeypatch.setattr(_ss, "probe_liveness", _always_fail)
        # loop runs forever without exiting; let it run past the ceiling, then cancel
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(_ss.liveness_watchdog_loop(), timeout=0.6)
        assert self.exit_called is False, "must NOT restart when self_restart is off"
        await asyncio.sleep(0)
        assert self.reconnect_calls >= 1, "should still attempt in-process reconnect"

    @pytest.mark.asyncio
    async def test_healthy_store_never_restarts(self, monkeypatch):
        async def _always_ok(*a, **k):
            return True
        monkeypatch.setattr(_ss, "probe_liveness", _always_ok)
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(_ss.liveness_watchdog_loop(), timeout=0.5)
        assert self.exit_called is False
        assert _ss._ss_wd_unhealthy_since is None, "healthy streak must keep unhealthy_since None"
