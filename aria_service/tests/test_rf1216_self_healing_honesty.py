"""R-F1216: Capability test — self_healing.py no longer returns false success.

Every action type in _execute_action() must VERIFY the action completed
before returning success: True. RESTART and ROLLBACK must return
success: False because they require operator intervention.
"""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from aria_service.intel.self_healing import (
    AutoRecoveryEngine,
    CircuitBreakerManager,
    RecoveryAction,
    RecoveryActionType,
)


@pytest.fixture
def engine():
    """Create an AutoRecoveryEngine with a fresh CircuitBreakerManager."""
    cbm = CircuitBreakerManager()
    return AutoRecoveryEngine(cbm)


@pytest.mark.asyncio
async def test_reconnect_verifies_connection():
    """RECONNECT probes the target and returns success: False if unreachable."""
    cbm = CircuitBreakerManager()
    eng = AutoRecoveryEngine(cbm)
    action = RecoveryAction(
        action=RecoveryActionType.RECONNECT,
        target="nonexistent.example.com:9999",
        reason="connection lost",
    )
    with patch("aria_service.intel.self_healing.httpx.AsyncClient") as mock_client:
        mock_instance = MagicMock()
        mock_instance.__aenter__.return_value.get = AsyncMock(
            side_effect=Exception("Connection refused")
        )
        mock_client.return_value = mock_instance
        result = await eng._execute_action(action)
        assert not result.get("success"), (
            f"RECONNECT returned success: True for unreachable target: {result}"
        )
        assert "failed" in result.get("message", "").lower(), (
            f"Message should indicate failure: {result}"
        )


@pytest.mark.asyncio
async def test_reconnect_success_on_reachable():
    """RECONNECT returns success: True only when the target responds <500."""
    cbm = CircuitBreakerManager()
    eng = AutoRecoveryEngine(cbm)
    action = RecoveryAction(
        action=RecoveryActionType.RECONNECT,
        target="localhost:8000",
        reason="connection lost",
    )
    with patch("aria_service.intel.self_healing.httpx.AsyncClient") as mock_client:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_instance = MagicMock()
        mock_instance.__aenter__.return_value.get = AsyncMock(return_value=mock_response)
        mock_client.return_value = mock_instance
        result = await eng._execute_action(action)
        assert result.get("success"), (
            f"RECONNECT returned success: False for reachable target: {result}"
        )


@pytest.mark.asyncio
async def test_restart_returns_success_false():
    """RESTART returns success: False because it requires operator intervention."""
    cbm = CircuitBreakerManager()
    eng = AutoRecoveryEngine(cbm)
    action = RecoveryAction(
        action=RecoveryActionType.RESTART,
        target="aria_intel",
        reason="OOM detected",
    )
    with patch("aria_service.intel.self_healing.wire_failure") as mock_wf:
        result = await eng._execute_action(action)
        assert not result.get("success"), (
            f"RESTART returned success: True — it requires operator: {result}"
        )
        assert result.get("needs_operator"), (
            f"RESTART should flag needs_operator: {result}"
        )
        assert mock_wf.called, "wire_failure was not called for RESTART"


@pytest.mark.asyncio
async def test_rollback_returns_success_false():
    """ROLLBACK returns success: False because it requires operator intervention."""
    cbm = CircuitBreakerManager()
    eng = AutoRecoveryEngine(cbm)
    action = RecoveryAction(
        action=RecoveryActionType.ROLLBACK,
        target="aria_intel",
        reason="regression detected",
    )
    with patch("aria_service.intel.self_healing.wire_failure") as mock_wf:
        result = await eng._execute_action(action)
        assert not result.get("success"), (
            f"ROLLBACK returned success: True — it requires operator: {result}"
        )
        assert result.get("needs_operator"), (
            f"ROLLBACK should flag needs_operator: {result}"
        )
        assert mock_wf.called, "wire_failure was not called for ROLLBACK"


@pytest.mark.asyncio
async def test_rebuild_verifies_key_written():
    """REBUILD verifies the Redis key was actually written."""
    cbm = CircuitBreakerManager()
    eng = AutoRecoveryEngine(cbm)
    action = RecoveryAction(
        action=RecoveryActionType.REBUILD,
        target="semantic_search",
        reason="index corrupt",
    )
    with patch("aria_service.intel.self_healing.rs.set") as mock_set, \
         patch("aria_service.intel.self_healing.rs.get", return_value=b"1"):
        mock_set = AsyncMock()
        result = await eng._execute_action(action)
        assert result.get("success"), (
            f"REBUILD returned success: False when key was written: {result}"
        )


@pytest.mark.asyncio
async def test_rebuild_fails_on_unwritten_key():
    """REBUILD returns success: False if the key was not persisted."""
    cbm = CircuitBreakerManager()
    eng = AutoRecoveryEngine(cbm)
    action = RecoveryAction(
        action=RecoveryActionType.REBUILD,
        target="semantic_search",
        reason="index corrupt",
    )
    with patch("aria_service.intel.self_healing.rs.set") as mock_set, \
         patch("aria_service.intel.self_healing.rs.get", return_value=None):
        mock_set = AsyncMock()
        result = await eng._execute_action(action)
        assert not result.get("success"), (
            f"REBUILD returned success: True when key was not persisted: {result}"
        )


@pytest.mark.asyncio
async def test_clear_cache_verifies_deletion():
    """CLEAR_CACHE verifies keys were actually deleted by re-scanning."""
    cbm = CircuitBreakerManager()
    eng = AutoRecoveryEngine(cbm)
    action = RecoveryAction(
        action=RecoveryActionType.CLEAR_CACHE,
        target="redis",
        reason="stale data",
    )
    with patch("aria_service.intel.self_healing.rs.scan_keys") as mock_scan, \
         patch("aria_service.intel.self_healing.rs.delete", AsyncMock(return_value=1)):
        # First scan returns keys, second scan (verification) returns empty
        mock_scan.side_effect = [["key1", "key2"], []]
        result = await eng._execute_action(action)
        assert result.get("success"), (
            f"CLEAR_CACHE returned success: False when all keys deleted: {result}"
        )


@pytest.mark.asyncio
async def test_clear_cache_fails_on_remaining_keys():
    """CLEAR_CACHE returns success: False if keys remain after deletion."""
    cbm = CircuitBreakerManager()
    eng = AutoRecoveryEngine(cbm)
    action = RecoveryAction(
        action=RecoveryActionType.CLEAR_CACHE,
        target="redis",
        reason="stale data",
    )
    with patch("aria_service.intel.self_healing.rs.scan_keys") as mock_scan:
        # Both scans return keys — deletion was incomplete
        mock_scan = AsyncMock(side_effect=[["key1", "key2"], ["key1"]])
        with patch("aria_service.intel.self_healing.rs.delete", AsyncMock()):
            result = await eng._execute_action(action)
            assert not result.get("success"), (
                f"CLEAR_CACHE returned success: True when keys remain: {result}"
            )
            assert "incomplete" in result.get("message", "").lower(), (
                f"Message should indicate incomplete deletion: {result}"
            )


@pytest.mark.asyncio
async def test_notify_returns_success():
    """NOTIFY returns success: True — logging IS the action."""
    cbm = CircuitBreakerManager()
    eng = AutoRecoveryEngine(cbm)
    action = RecoveryAction(
        action=RecoveryActionType.NOTIFY,
        target="some_subsystem",
        reason="unknown error",
    )
    result = await eng._execute_action(action)
    assert result.get("success"), (
        f"NOTIFY should return success: True: {result}"
    )


@pytest.mark.asyncio
async def test_unknown_action_returns_failure():
    """Unknown action type returns success: False."""
    cbm = CircuitBreakerManager()
    eng = AutoRecoveryEngine(cbm)
    action = RecoveryAction(
        action="nonexistent_action",  # type: ignore
        target="test",
        reason="test",
    )
    result = await eng._execute_action(action)
    assert not result.get("success"), (
        f"Unknown action should return success: False: {result}"
    )
