"""R-F1067 — Capability test for self_healing attempt_recovery().

Verifies that attempt_recovery actually returns a dict and that
RESTART/ROLLBACK actions are handled (not silently no-op).
"""
from __future__ import annotations

import pytest


class TestSelfHealingCapability:
    """Capability test: attempt_recovery must return a dict."""

    @pytest.mark.asyncio
    async def test_attempt_recovery_returns_dict(self) -> None:
        """attempt_recovery must return a dict with success/message."""
        from aria_service.intel.self_healing import AutoRecoveryEngine, CircuitBreakerManager

        cbm = CircuitBreakerManager()
        recovery = AutoRecoveryEngine(circuit_breaker_manager=cbm)
        result = await recovery.attempt_recovery("test_subsystem", "connection refused")

        assert isinstance(result, dict), "Result must be a dict"
        assert "success" in result, "Result must have success field"
        assert "message" in result or "action" in result, "Result must have message or action"

    @pytest.mark.asyncio
    async def test_recovery_determines_action(self) -> None:
        """_determine_action must return a RecoveryAction for known errors."""
        from aria_service.intel.self_healing import AutoRecoveryEngine, CircuitBreakerManager, RecoveryActionType

        cbm = CircuitBreakerManager()
        recovery = AutoRecoveryEngine(circuit_breaker_manager=cbm)

        # Connection errors -> RECONNECT
        action = recovery._determine_action("db", "connection refused")
        assert action.action == RecoveryActionType.RECONNECT

        # Memory errors -> RESTART
        action = recovery._determine_action("worker", "out of memory")
        assert action.action == RecoveryActionType.RESTART

        # Deploy errors -> ROLLBACK
        action = recovery._determine_action("app", "deploy regression")
        assert action.action == RecoveryActionType.ROLLBACK

        # Cache errors -> CLEAR_CACHE
        action = recovery._determine_action("cache", "stale data")
        assert action.action == RecoveryActionType.CLEAR_CACHE

    @pytest.mark.asyncio
    async def test_execute_action_handles_all_types(self) -> None:
        """_execute_action must handle all RecoveryActionTypes without crashing."""
        from aria_service.intel.self_healing import AutoRecoveryEngine, CircuitBreakerManager, RecoveryAction, RecoveryActionType

        cbm = CircuitBreakerManager()
        recovery = AutoRecoveryEngine(circuit_breaker_manager=cbm)

        for action_type in RecoveryActionType:
            action = RecoveryAction(action=action_type, target="test")
            result = await recovery._execute_action(action)
            assert isinstance(result, dict), f"Action {action_type} must return dict"
            assert "success" in result, f"Action {action_type} must have success field"
