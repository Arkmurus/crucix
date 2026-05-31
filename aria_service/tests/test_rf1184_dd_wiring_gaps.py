"""R-F1184 — Capability tests for DD wiring gaps.

Tests that _persist_report actually calls:
  1. intel_ledger.add_signal() — every DD report
  2. audit_log.record() — RED/HARD_STOP verdicts only
  3. mem0.summarise_and_store() — non-ERROR reports

Each test patches the sink and verifies it was called with the right args.
"""

from __future__ import annotations

import json
import pytest
from unittest.mock import AsyncMock, patch, MagicMock, call

from aria_service.intel.dd_schema import ARKDDReport, RiskClassification


# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def mock_redis():
    """Patch redis_store so _persist_report doesn't hit real storage."""
    store: dict[str, str] = {}

    async def _get_json(key: str):
        raw = store.get(key)
        if raw:
            return json.loads(raw)
        return None

    async def _set_json(key: str, obj, ex=None, keepttl=False):
        store[key] = json.dumps(obj, default=str)

    with patch("aria_service.intel.redis_store.get_json", AsyncMock(side_effect=_get_json)), \
         patch("aria_service.intel.redis_store.set_json", AsyncMock(side_effect=_set_json)):
        yield store


def _make_report(
    run_id: str,
    entity_name: str,
    canonical_id: str,
    risk: str,
    bottom_line: str,
    confidence: str = "ASSESSED",
) -> ARKDDReport:
    """Helper to create an ARKDDReport with identity section populated."""
    from aria_service.intel.dd_schema import IdentitySection
    r = ARKDDReport(
        run_id=run_id,
        target={"name": entity_name},
        canonical_entity_id=canonical_id,
        version_number=1,
        risk_classification=risk,
        bottom_line=bottom_line,
        confidence_tag=confidence,
    )
    # Populate identity section so report.identity.entity_name works
    r.identity.entity_name = entity_name
    r.identity.entity_type = "company"
    r.identity.jurisdiction_iso2 = canonical_id.split(":")[1] if ":" in canonical_id else "XX"
    return r


@pytest.fixture
def green_report() -> ARKDDReport:
    """A GREEN DD report — should trigger intel_ledger + mem0, NOT audit_log."""
    return _make_report(
        run_id="dd_test_green",
        entity_name="Clean Corp Ltd",
        canonical_id="company:GB:12345678",
        risk=RiskClassification.GREEN.value,
        bottom_line="No material concerns identified.",
        confidence="PROBABLE",
    )


@pytest.fixture
def red_report() -> ARKDDReport:
    """A RED DD report — should trigger ALL three sinks."""
    return _make_report(
        run_id="dd_test_red",
        entity_name="Risky Business SARL",
        canonical_id="company:LU:87654321",
        risk=RiskClassification.RED.value,
        bottom_line="Sanctions hit on beneficial owner. Do not proceed.",
        confidence="ASSESSED",
    )


@pytest.fixture
def hard_stop_report() -> ARKDDReport:
    """A HARD_STOP DD report — should trigger ALL three sinks."""
    return _make_report(
        run_id="dd_test_hardstop",
        entity_name="Sanctioned Entity Ltd",
        canonical_id="company:IR:99999999",
        risk=RiskClassification.HARD_STOP.value,
        bottom_line="Direct OFAC SDN match. File SAR immediately.",
        confidence="CONFIRMED",
    )


# ── Capability test 1: intel_ledger.add_signal is called for every DD ───────


@pytest.mark.asyncio
async def test_rf1184_intel_ledger_called_for_green(mock_redis, green_report):
    """Capability test: intel_ledger.add_signal is called for a GREEN DD."""
    from aria_service.intel import dd_orchestrator

    with patch("aria_service.intel.intel_ledger.add_signal", AsyncMock()) as mock_add:
        # We need to call _persist_report directly. It's a module-level function.
        await dd_orchestrator._persist_report(green_report)

        mock_add.assert_awaited_once()
        # await_args returns a Call object; [0] is positional args tuple, [0][0] is first arg
        call_args = mock_add.await_args[0][0]
        assert "Clean Corp Ltd" in call_args.get("summary", "")
        assert call_args.get("type") == "dd_report"
        assert call_args.get("severity") == "medium"  # GREEN = medium
        assert "dd" in call_args.get("tags", [])
        assert "green" in call_args.get("tags", [])


@pytest.mark.asyncio
async def test_rf1184_intel_ledger_called_for_red(mock_redis, red_report):
    """Capability test: intel_ledger.add_signal is called for a RED DD."""
    from aria_service.intel import dd_orchestrator

    with patch("aria_service.intel.intel_ledger.add_signal", AsyncMock()) as mock_add:
        await dd_orchestrator._persist_report(red_report)

        mock_add.assert_awaited_once()
        call_args = mock_add.await_args[0][0]
        assert "Risky Business SARL" in call_args.get("summary", "")
        assert call_args.get("severity") == "high"  # RED = high


# ── Capability test 2: audit_log.record is called for RED/HARD_STOP only ────


@pytest.mark.asyncio
async def test_rf1184_audit_log_not_called_for_green(mock_redis, green_report):
    """Capability test: audit_log.record is NOT called for GREEN."""
    from aria_service.intel import dd_orchestrator

    with patch("aria_service.intel.audit_log.record", AsyncMock()) as mock_record:
        await dd_orchestrator._persist_report(green_report)

        mock_record.assert_not_awaited()


@pytest.mark.asyncio
async def test_rf1184_audit_log_called_for_red(mock_redis, red_report):
    """Capability test: audit_log.record IS called for RED."""
    from aria_service.intel import dd_orchestrator

    with patch("aria_service.intel.audit_log.record", AsyncMock()) as mock_record:
        await dd_orchestrator._persist_report(red_report)

        mock_record.assert_awaited_once()
        call_kwargs = mock_record.await_args.kwargs
        assert call_kwargs.get("action") == "dd_verdict"
        assert call_kwargs.get("decision") == "RED"
        assert "Risky Business SARL" in call_kwargs.get("entity_name", "")


@pytest.mark.asyncio
async def test_rf1184_audit_log_called_for_hard_stop(mock_redis, hard_stop_report):
    """Capability test: audit_log.record IS called for HARD_STOP."""
    from aria_service.intel import dd_orchestrator

    with patch("aria_service.intel.audit_log.record", AsyncMock()) as mock_record:
        await dd_orchestrator._persist_report(hard_stop_report)

        mock_record.assert_awaited_once()
        call_kwargs = mock_record.await_args.kwargs
        assert call_kwargs.get("action") == "dd_verdict"
        assert call_kwargs.get("decision") == "HARD_STOP"


# ── Capability test 3: mem0.summarise_and_store is called for non-ERROR ─────


@pytest.mark.asyncio
async def test_rf1184_mem0_called_for_green(mock_redis, green_report):
    """Capability test: mem0.summarise_and_store is called for GREEN."""
    from aria_service.intel import dd_orchestrator

    with patch("aria_service.intel.mem0.is_enabled", return_value=True), \
         patch("aria_service.intel.mem0.summarise_and_store", AsyncMock()) as mock_mem0:
        await dd_orchestrator._persist_report(green_report)

        mock_mem0.assert_awaited_once()
        call_kwargs = mock_mem0.await_args.kwargs
        assert "Clean Corp Ltd" in call_kwargs.get("aria_response", "")
        assert call_kwargs.get("session_id") == "dd_dd_test_green"


@pytest.mark.asyncio
async def test_rf1184_mem0_called_for_red(mock_redis, red_report):
    """Capability test: mem0.summarise_and_store is called for RED."""
    from aria_service.intel import dd_orchestrator

    with patch("aria_service.intel.mem0.is_enabled", return_value=True), \
         patch("aria_service.intel.mem0.summarise_and_store", AsyncMock()) as mock_mem0:
        await dd_orchestrator._persist_report(red_report)

        mock_mem0.assert_awaited_once()
        call_kwargs = mock_mem0.await_args.kwargs
        assert "Risky Business SARL" in call_kwargs.get("aria_response", "")


@pytest.mark.asyncio
async def test_rf1184_mem0_skipped_when_disabled(mock_redis, green_report):
    """Capability test: mem0 is NOT called when mem0 is disabled."""
    from aria_service.intel import dd_orchestrator

    with patch("aria_service.intel.mem0.is_enabled", return_value=False), \
         patch("aria_service.intel.mem0.summarise_and_store", AsyncMock()) as mock_mem0:
        await dd_orchestrator._persist_report(green_report)

        mock_mem0.assert_not_awaited()


# ── Capability test 4: all three fire together on RED ───────────────────────


@pytest.mark.asyncio
async def test_rf1184_all_three_fire_on_red(mock_redis, red_report):
    """Capability test: all three sinks fire on a RED DD."""
    from aria_service.intel import dd_orchestrator

    with patch("aria_service.intel.intel_ledger.add_signal", AsyncMock()) as mock_il, \
         patch("aria_service.intel.audit_log.record", AsyncMock()) as mock_al, \
         patch("aria_service.intel.mem0.is_enabled", return_value=True), \
         patch("aria_service.intel.mem0.summarise_and_store", AsyncMock()) as mock_mem0:
        await dd_orchestrator._persist_report(red_report)

        mock_il.assert_awaited_once()
        mock_al.assert_awaited_once()
        mock_mem0.assert_awaited_once()


# ── Capability test 5: audit_log action is in RECORDED_ACTIONS ──────────────


def test_rf1184_dd_verdict_in_recorded_actions():
    """Unit test: 'dd_verdict' must be in audit_log.RECORDED_ACTIONS."""
    from aria_service.intel.audit_log import RECORDED_ACTIONS
    assert "dd_verdict" in RECORDED_ACTIONS, \
        "dd_verdict must be in RECORDED_ACTIONS for audit_log.record to accept it"
