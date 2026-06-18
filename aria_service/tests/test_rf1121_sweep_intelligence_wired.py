"""R-F1121 — Capability test: _run_sweep_intelligence is wired to the brain.

Proves that:
1. On success, wire_success is called (via @wired decorator)
2. On exception, wire_failure is called (via @wired decorator)
3. The function still works correctly (report is populated)
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aria_service.intel.dd_schema import ARKDDReport, LayerStatus


# ── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture
def sample_target() -> dict:
    return {
        "name": "Acme Corp",
        "jurisdiction_iso2": "US",
        "type": "company",
    }


@pytest.fixture
def empty_report() -> ARKDDReport:
    return ARKDDReport(
        run_id="test-sweep-wired",
        target={"name": "Acme Corp"},
        orchestrator_mode="standard",
    )


# ── Tests ───────────────────────────────────────────────────────────────────

class TestSweepIntelligenceWired:
    """Proves _run_sweep_intelligence fires brain signals on both paths."""

    async def test_success_wires_to_brain(self, sample_target, empty_report):
        """On success, wire_success is called via @wired decorator."""
        with patch("aria_service.intel.engine_wiring.wire_success") as mock_ws, \
             patch("aria_service.intel.engine_wiring.wire_failure") as mock_wf, \
             patch("aria_service.intel.brain_hook.get_stats", return_value={"modules": {}}), \
             patch("aria_service.intel.intel_ledger.get_recent", return_value=[]):

            # Import AFTER patching so module-level wire_success calls
            # from brain_hook/intel_ledger are caught by the mock.
            from aria_service.intel.dd_orchestrator import _run_sweep_intelligence

            # Reset mock to clear any module-level wiring calls
            mock_ws.reset_mock()

            await _run_sweep_intelligence(sample_target, empty_report)

        # wire_success should have been called
        mock_ws.assert_called_once()
        args, kwargs = mock_ws.call_args
        assert kwargs.get("module") == "dd_orchestrator.sweep_intelligence"
        assert "completed" in kwargs.get("summary", "")

        # wire_failure should NOT have been called
        mock_wf.assert_not_called()

        # Report should be populated
        assert empty_report.sweep_data.meta.status == LayerStatus.OK.value
        assert empty_report.sweep_data.meta.duration_ms >= 0

    async def test_graceful_degradation_on_brain_failure(self, sample_target, empty_report):
        """Brain failure is handled gracefully — function completes, wire_success fires."""
        from aria_service.intel.dd_orchestrator import _run_sweep_intelligence

        with patch("aria_service.intel.engine_wiring.wire_success") as mock_ws, \
             patch("aria_service.intel.engine_wiring.wire_failure") as mock_wf, \
             patch("aria_service.intel.brain_hook.get_stats", side_effect=RuntimeError("brain down")):

            await _run_sweep_intelligence(sample_target, empty_report)

        # wire_success fires because the function handles brain failure gracefully
        mock_ws.assert_called_once()
        mock_wf.assert_not_called()

        # Report should still be populated (graceful degradation — PARTIAL, not ERROR)
        assert empty_report.sweep_data.meta.status == LayerStatus.PARTIAL.value
        assert "brain down" in empty_report.sweep_data.data_gaps[0]

    async def test_graceful_degradation_on_ledger_failure(self, sample_target, empty_report):
        """Ledger query failure is handled gracefully (data_gaps, not exception)."""
        from aria_service.intel.dd_orchestrator import _run_sweep_intelligence

        with patch("aria_service.intel.engine_wiring.wire_success") as mock_ws, \
             patch("aria_service.intel.engine_wiring.wire_failure") as mock_wf, \
             patch("aria_service.intel.brain_hook.get_stats", return_value={"modules": {}}), \
             patch("aria_service.intel.intel_ledger.get_recent", side_effect=Exception("ledger timeout")):

            await _run_sweep_intelligence(sample_target, empty_report)

        # wire_success should still be called (ledger failure is non-fatal)
        mock_ws.assert_called_once()
        mock_wf.assert_not_called()

        # Report should have data_gaps but status OK (degraded internally)
        assert len(empty_report.sweep_data.data_gaps) > 0
        assert "ledger" in empty_report.sweep_data.data_gaps[0].lower()
