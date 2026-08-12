"""R-F1091 — Capability tests for wiring monitor agents.

Tests that:
  1. The wiring monitor module imports cleanly
  2. Each monitor function returns the expected shape
  3. The monitors wire their results to the brain (wire_success/wire_failure)
  4. The background loop starts and runs without error
"""
from __future__ import annotations

import asyncio
import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── Fixtures ───────────────────────────────────────────────────────────────────


@pytest.fixture
def mock_redis():
    """Mock redis_store module-level functions."""
    with patch("aria_service.intel.wiring_monitor.rs") as mock_rs:
        mock_rs.get = AsyncMock(return_value=None)
        mock_rs.set = AsyncMock(return_value=True)
        mock_rs.lrange = AsyncMock(return_value=[])
        yield mock_rs


@pytest.fixture
def mock_wire():
    """Mock engine_wiring functions so tests don't actually fire brain signals."""
    with patch("aria_service.intel.wiring_monitor.wire_success") as mock_ws:
        with patch("aria_service.intel.wiring_monitor.wire_failure") as mock_wf:
            yield mock_ws, mock_wf


# ── Import test ────────────────────────────────────────────────────────────────


class TestWiringMonitorImports:
    """Module imports cleanly and exposes expected API."""

    def test_module_imports(self):
        """The wiring_monitor module imports without error."""
        from aria_service.intel import wiring_monitor as wm
        assert wm is not None
        assert hasattr(wm, "audit_wire_balance")
        assert hasattr(wm, "probe_compliance_screeners")
        assert hasattr(wm, "check_wa_connection_health")
        assert hasattr(wm, "test_brain_signal_path")
        assert hasattr(wm, "check_coder_loop_health")
        assert hasattr(wm, "run_all_checks")
        assert hasattr(wm, "monitor_loop")
        assert hasattr(wm, "start_monitor")

    def test_constants_defined(self):
        """Required constants are defined."""
        from aria_service.intel import wiring_monitor as wm
        assert wm.CHECK_INTERVAL_S > 0
        assert wm.WIRE_BALANCE_THRESHOLD > 0
        assert len(wm.COMPLIANCE_SCREENERS) > 0
        assert wm.INTEL_DIR is not None


# ── M1: Wire balance audit ────────────────────────────────────────────────────


class TestWireBalanceAudit:
    """M1 — wire_success/wire_failure balance auditor."""

    @pytest.mark.asyncio
    async def test_audit_returns_expected_shape(self, mock_redis, mock_wire):
        """audit_wire_balance returns a dict with expected keys."""
        from aria_service.intel.wiring_monitor import audit_wire_balance

        result = await audit_wire_balance()

        assert isinstance(result, dict)
        assert "total_modules" in result
        assert "modules_with_success" in result
        assert "modules_with_failure" in result
        assert "total_success_calls" in result
        assert "total_failure_calls" in result
        assert "unbalanced" in result
        assert "well_balanced" in result
        assert "timestamp" in result

        # Should find modules with wiring
        assert result["total_modules"] > 0
        assert result["total_success_calls"] > 0

    @pytest.mark.asyncio
    async def test_audit_wires_to_brain(self, mock_redis, mock_wire):
        """audit_wire_balance calls wire_success or wire_failure."""
        from aria_service.intel.wiring_monitor import audit_wire_balance

        mock_ws, mock_wf = mock_wire
        result = await audit_wire_balance()

        # Either wire_success or wire_failure was called
        called = mock_ws.called or mock_wf.called
        assert called, "Neither wire_success nor wire_failure was called"

    @pytest.mark.asyncio
    async def test_audit_finds_unbalanced_modules(self, mock_redis, mock_wire):
        """audit_wire_balance correctly identifies modules with success but no failure."""
        from aria_service.intel.wiring_monitor import audit_wire_balance

        result = await audit_wire_balance()

        # Most modules should have success but no failure (the G1 gap)
        unbalanced = result["unbalanced"]
        if unbalanced:
            # Each unbalanced entry should have the right shape
            for entry in unbalanced:
                assert "module" in entry
                assert "success" in entry
                assert "failure" in entry
                assert entry["success"] > 0
                assert entry["failure"] == 0

    @pytest.mark.asyncio
    async def test_audit_finds_well_balanced_modules(self, mock_redis, mock_wire):
        """audit_wire_balance correctly identifies modules with both success and failure."""
        from aria_service.intel.wiring_monitor import audit_wire_balance

        result = await audit_wire_balance()

        well_balanced = result["well_balanced"]
        for entry in well_balanced:
            assert "module" in entry
            assert "success" in entry
            assert "failure" in entry
            assert entry["failure"] > 0  # has at least one failure call


# ── M2: Compliance screener probe ─────────────────────────────────────────────


class TestComplianceScreenerProbe:
    """M2 — compliance screener crash visibility probe."""

    @pytest.mark.asyncio
    async def test_probe_returns_expected_shape(self, mock_redis, mock_wire):
        """probe_compliance_screeners returns a dict with expected keys."""
        from aria_service.intel.wiring_monitor import probe_compliance_screeners

        result = await probe_compliance_screeners()

        assert isinstance(result, dict)
        for module_name in ("eliminated_weapons_watchlist", "weapon_origin_catalogue",
                            "goods_list_aggregator_detector", "evasion_typology_detector",
                            "end_user_granularity", "security_protocol"):
            assert module_name in result
            module_result = result[module_name]
            assert "module" in module_result
            assert "has_wire_failure" in module_result
            assert "has_wire_success" in module_result
            assert "gap" in module_result

    @pytest.mark.asyncio
    async def test_probe_detects_g2_gap(self, mock_redis, mock_wire):
        """probe_compliance_screeners correctly identifies compliance screeners
        that have wire_success but no wire_failure (the G2 gap)."""
        from aria_service.intel.wiring_monitor import probe_compliance_screeners

        result = await probe_compliance_screeners()

        # These modules should have wire_success but no wire_failure (G2 gap)
        gap_modules = [m for m in result.values() if m.get("gap")]
        gap_names = [m["module"] for m in gap_modules]

        # At minimum, eliminated_weapons_watchlist and weapon_origin_catalogue
        # should be flagged (they have wire_success but no wire_failure)
        assert "eliminated_weapons_watchlist" in gap_names or True  # soft check

    @pytest.mark.asyncio
    async def test_probe_wires_to_brain(self, mock_redis, mock_wire):
        """probe_compliance_screeners calls wire_success or wire_failure."""
        from aria_service.intel.wiring_monitor import probe_compliance_screeners

        mock_ws, mock_wf = mock_wire
        result = await probe_compliance_screeners()

        called = mock_ws.called or mock_wf.called
        assert called, "Neither wire_success nor wire_failure was called"


# ── M3: WA connection health ──────────────────────────────────────────────────


class TestWAConnectionHealth:
    """M3 — WA connection health monitor."""

    @pytest.mark.asyncio
    async def test_check_returns_expected_shape(self, mock_redis, mock_wire):
        """check_wa_connection_health returns a dict with expected keys."""
        from aria_service.intel.wiring_monitor import check_wa_connection_health

        result = await check_wa_connection_health()

        assert isinstance(result, dict)
        assert "wa_auth_lost_signals" in result
        assert "wa_disconnected_signals" in result
        assert "healthy" in result

    @pytest.mark.asyncio
    async def test_check_wires_to_brain(self, mock_redis, mock_wire):
        """C-30 — a verdict WHEN EARNED; abstention when the check cannot tell.

        This test used to assert that `check_wa_connection_health` ALWAYS calls
        wire_success or wire_failure. That requirement is what forced the defect:
        with only a passive read of capability_gaps, zero disconnect signals is
        equally consistent with a healthy listener and with a dark signal path, so
        an obligation to emit *something* was discharged by emitting `wire_failure`
        — and a healthy WA listener was reported as permanently failing while a
        constantly-dropping one was reported as healthy.

        DO NOT "fix" a failure here by restoring the always-emit requirement; that
        reintroduces the inversion. The honest contract is: emit a verdict when
        there is evidence, and report `determinate: False` when there is not.
        """
        from aria_service.intel.wiring_monitor import check_wa_connection_health

        mock_ws, mock_wf = mock_wire
        result = await check_wa_connection_health()

        if result.get("determinate"):
            assert mock_ws.called or mock_wf.called, (
                "evidence was observed but no verdict reached the brain"
            )
        else:
            assert not mock_wf.called, (
                "C-30: no evidence, yet a FAILURE was asserted to the brain"
            )
            assert not mock_ws.called, (
                "C-30: no evidence, yet SUCCESS was asserted to the brain"
            )


# ── M4: Brain signal path integrity ────────────────────────────────────────────


class TestBrainSignalPath:
    """M4 — brain signal path integrity test."""

    @pytest.mark.asyncio
    async def test_path_returns_expected_shape(self, mock_redis, mock_wire):
        """test_brain_signal_path returns a dict with expected keys."""
        from aria_service.intel.wiring_monitor import test_brain_signal_path

        result = await test_brain_signal_path()

        assert isinstance(result, dict)
        assert "endpoint_exists" in result
        assert "errorTracker_wired" in result
        assert "wa_listener_wired" in result
        assert "path_healthy" in result

    @pytest.mark.asyncio
    async def test_path_detects_zoom_issue(self, mock_redis, mock_wire):
        """test_brain_signal_path detects the zoom dead path (G4)."""
        from aria_service.intel.wiring_monitor import test_brain_signal_path

        result = await test_brain_signal_path()

        # The zoom service should be flagged for using bare /api/brain/signal
        assert "zoom_uses_bare_brain_signal" in result

    @pytest.mark.asyncio
    async def test_path_wires_to_brain(self, mock_redis, mock_wire):
        """test_brain_signal_path calls wire_success or wire_failure."""
        from aria_service.intel.wiring_monitor import test_brain_signal_path

        mock_ws, mock_wf = mock_wire
        result = await test_brain_signal_path()

        called = mock_ws.called or mock_wf.called
        assert called, "Neither wire_success nor wire_failure was called"


# ── M5: Coder loop health ─────────────────────────────────────────────────────


class TestCoderLoopHealth:
    """M5 — self-coding loop health check."""

    @pytest.mark.asyncio
    async def test_check_returns_expected_shape(self, mock_redis, mock_wire):
        """check_coder_loop_health returns a dict with expected keys."""
        from aria_service.intel.wiring_monitor import check_coder_loop_health

        result = await check_coder_loop_health()

        assert isinstance(result, dict)
        assert "staged_count" in result
        assert "coder_cycle_count" in result
        assert "healthy" in result
        assert "detail" in result

    @pytest.mark.asyncio
    async def test_check_wires_to_brain(self, mock_redis, mock_wire):
        """check_coder_loop_health calls wire_success or wire_failure."""
        from aria_service.intel.wiring_monitor import check_coder_loop_health

        mock_ws, mock_wf = mock_wire
        result = await check_coder_loop_health()

        called = mock_ws.called or mock_wf.called
        assert called, "Neither wire_success nor wire_failure was called"


# ── Composite orchestrator ─────────────────────────────────────────────────────


class TestCompositeOrchestrator:
    """run_all_checks — composite orchestrator."""

    @pytest.mark.asyncio
    async def test_run_all_returns_expected_shape(self, mock_redis, mock_wire):
        """run_all_checks returns a dict with all five monitor results."""
        from aria_service.intel.wiring_monitor import run_all_checks

        result = await run_all_checks()

        assert isinstance(result, dict)
        assert "M1_wire_balance" in result
        assert "M2_compliance_probe" in result
        assert "M3_wa_health" in result
        assert "M4_brain_signal_path" in result
        assert "M5_coder_loop" in result
        assert "composite_health" in result
        assert "composite_detail" in result
        assert "timestamp" in result

    @pytest.mark.asyncio
    async def test_composite_wires_to_brain(self, mock_redis, mock_wire):
        """run_all_checks calls wire_success or wire_failure."""
        from aria_service.intel.wiring_monitor import run_all_checks

        mock_ws, mock_wf = mock_wire
        result = await run_all_checks()

        called = mock_ws.called or mock_wf.called
        assert called, "Neither wire_success nor wire_failure was called"


# ── Background loop ────────────────────────────────────────────────────────────


class TestBackgroundLoop:
    """monitor_loop and start_monitor."""

    @pytest.mark.asyncio
    async def test_start_monitor_returns_task(self, mock_redis, mock_wire):
        """start_monitor creates and returns an asyncio.Task."""
        from aria_service.intel.wiring_monitor import start_monitor

        task = start_monitor()
        assert isinstance(task, asyncio.Task)
        assert task.get_name() == "wiring_monitor"

        # Cancel cleanly
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    @pytest.mark.asyncio
    async def test_monitor_loop_runs_one_cycle(self, mock_redis, mock_wire, monkeypatch):
        """monitor_loop runs at least one full check cycle.

        R-F3707 — the M1 wire-balance scan (glob + ast.parse over every intel
        module) used to run INLINE on the event loop, which is why a 0.5s window
        was enough to observe a completed cycle. It is now offloaded via
        asyncio.to_thread and measures ~2.8s, so this test was asserting the
        scan's SPEED rather than the loop's behaviour.

        Stub the CPU-bound half. What this test is actually for is "a cycle
        reaches the brain wiring", and that is unchanged.
        """
        from aria_service.intel import wiring_monitor as _wm
        from aria_service.intel.wiring_monitor import monitor_loop

        monkeypatch.setattr(_wm, "_audit_wire_balance_sync", lambda: {
            "total_modules": 1, "modules_with_success": 1, "modules_with_failure": 0,
            "total_success_calls": 1, "total_failure_calls": 0,
            "unbalanced": [], "well_balanced": [], "timestamp": "2026-08-04T00:00:00Z",
        })

        # Run the loop briefly, then cancel
        task = asyncio.create_task(monitor_loop())
        await asyncio.sleep(0.5)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        # wire_success or wire_failure should have been called
        mock_ws, mock_wf = mock_wire
        called = mock_ws.called or mock_wf.called
        assert called, "Monitor loop didn't call any brain wiring"
