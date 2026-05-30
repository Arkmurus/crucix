"""
Capability test: R-F1165 — compliance_watch wiring to brain.
Tests that capture_message calls wire_success/wire_failure.
wire_success/wire_failure are fire-and-forget (sync dispatch to bg task),
so we verify they are CALLED, not that the bg task completes.
"""
import pytest
from unittest.mock import patch, MagicMock


@pytest.mark.asyncio
async def test_compliance_watch_capture_wires_success():
    """capture_message must call wire_success on success."""
    from aria_service.intel.compliance_watch import capture_message
    wire_success_called = False

    def fake_wire_success(**kwargs):
        nonlocal wire_success_called
        wire_success_called = True
        assert kwargs.get("module") == "compliance_watch"

    with patch("aria_service.intel.compliance_watch.wire_success",
               side_effect=fake_wire_success):
        with patch("aria_service.intel.compliance_watch._head_hash",
                   return_value="0" * 64):
            with patch("aria_service.intel.redis_store.incr",
                       return_value=1):
                with patch("aria_service.intel.redis_store.lpush",
                           return_value=1):
                    result = await capture_message(
                        group="test-group",
                        sender="test-sender",
                        text="test message",
                    )

    assert result.get("captured") is True, f"Expected captured=True, got {result}"
    assert wire_success_called, "wire_success was not called"


@pytest.mark.asyncio
async def test_compliance_watch_capture_wires_failure():
    """capture_message must call wire_failure on failure."""
    from aria_service.intel.compliance_watch import capture_message
    wire_failure_called = False

    def fake_wire_failure(**kwargs):
        nonlocal wire_failure_called
        wire_failure_called = True
        assert kwargs.get("module") == "compliance_watch"

    with patch("aria_service.intel.compliance_watch.wire_failure",
               side_effect=fake_wire_failure):
        with patch("aria_service.intel.compliance_watch._head_hash",
                   side_effect=Exception("redis down")):
            result = await capture_message(
                group="test-group",
                sender="test-sender",
                text="test message",
            )

    assert result.get("captured") is False, f"Expected captured=False, got {result}"
    assert wire_failure_called, "wire_failure was not called"
