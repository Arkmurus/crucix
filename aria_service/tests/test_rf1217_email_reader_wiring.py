"""R-F1217: Capability test — email_reader.py brain wiring is bulletproof.

Verifies:
1. wire_success is called after successful send (not hidden in try/except: pass)
2. wire_failure is called on send failure
3. The brain wiring failure is logged, not silently swallowed
"""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock, call


@pytest.mark.asyncio
async def test_send_email_wires_success():
    """send_email calls wire_success after successful send."""
    # wire_success/wire_failure are imported locally via from .engine_wiring
    # Patch at the engine_wiring module level
    with patch("aria_service.intel.engine_wiring.wire_success") as mock_ws, \
         patch("aria_service.intel.engine_wiring.wire_failure") as mock_wf, \
         patch("aria_service.intel.email_reader._SMTP_HOST", "smtp.test.com"), \
         patch("aria_service.intel.email_reader._SMTP_USER", "user@test.com"), \
         patch("aria_service.intel.email_reader._SMTP_PASS", "pass"), \
         patch("aria_service.intel.email_reader._SMTP_PORT", 587):
        
        from aria_service.intel.email_reader import send_email
        
        # Mock the SMTP send to succeed
        with patch("asyncio.get_running_loop") as mock_loop:
            mock_loop.return_value.run_in_executor = AsyncMock(return_value=None)
            
            result = await send_email("test@test.com", "Test Subject", "Test body")
            
            assert result.get("success"), f"Send should succeed: {result}"
            assert mock_ws.called, "wire_success was not called after successful send"
            # wire_failure should NOT be called on success
            assert not mock_wf.called, "wire_failure was called on success"


@pytest.mark.asyncio
async def test_send_email_wires_failure_on_error():
    """send_email calls wire_failure when SMTP send fails."""
    with patch("aria_service.intel.engine_wiring.wire_success") as mock_ws, \
         patch("aria_service.intel.engine_wiring.wire_failure") as mock_wf, \
         patch("aria_service.intel.email_reader._SMTP_HOST", "smtp.test.com"), \
         patch("aria_service.intel.email_reader._SMTP_USER", "user@test.com"), \
         patch("aria_service.intel.email_reader._SMTP_PASS", "pass"), \
         patch("aria_service.intel.email_reader._SMTP_PORT", 587):
        
        from aria_service.intel.email_reader import send_email
        
        # Mock the SMTP send to fail
        with patch("asyncio.get_running_loop") as mock_loop:
            mock_loop.return_value.run_in_executor = AsyncMock(
                side_effect=Exception("SMTP connection refused")
            )
            
            result = await send_email("test@test.com", "Test Subject", "Test body")
            
            assert not result.get("success"), f"Send should fail: {result}"
            assert mock_wf.called, "wire_failure was not called on send failure"
            # wire_success should NOT be called on failure
            assert not mock_ws.called, "wire_success was called on failure"


@pytest.mark.asyncio
async def test_send_email_logs_wiring_failure():
    """send_email logs a warning if brain wiring fails after send."""
    with patch("aria_service.intel.engine_wiring.wire_success") as mock_ws, \
         patch("aria_service.intel.engine_wiring.wire_failure") as mock_wf, \
         patch("aria_service.intel.email_reader._SMTP_HOST", "smtp.test.com"), \
         patch("aria_service.intel.email_reader._SMTP_USER", "user@test.com"), \
         patch("aria_service.intel.email_reader._SMTP_PASS", "pass"), \
         patch("aria_service.intel.email_reader._SMTP_PORT", 587), \
         patch("aria_service.intel.email_reader.logger") as mock_logger:
        
        from aria_service.intel.email_reader import send_email
        
        # Make wire_success raise an exception
        mock_ws.side_effect = Exception("Brain wiring failed")
        
        with patch("asyncio.get_running_loop") as mock_loop:
            mock_loop.return_value.run_in_executor = AsyncMock(return_value=None)
            
            result = await send_email("test@test.com", "Test Subject", "Test body")
            
            # The email was still sent — success: True
            assert result.get("success"), f"Send should still succeed: {result}"
            # The wiring failure should be logged as warning
            warning_calls = [c for c in mock_logger.warning.call_args_list 
                           if "Brain wiring failed" in str(c)]
            assert warning_calls, (
                "Brain wiring failure was not logged as warning"
            )


@pytest.mark.asyncio
async def test_send_email_no_smtp_config():
    """send_email returns success: False when SMTP not configured."""
    with patch("aria_service.intel.email_reader._SMTP_HOST", ""), \
         patch("aria_service.intel.email_reader._SMTP_USER", ""):
        
        from aria_service.intel.email_reader import send_email
        
        result = await send_email("test@test.com", "Test Subject", "Test body")
        
        assert not result.get("success"), "Should fail without SMTP config"
        assert "not configured" in result.get("error", "").lower()
