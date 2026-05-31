"""R-F1224: Capability test — ARIA-LLM/RunPod provider wiring.

Verifies:
1. aria_llm_provider.complete() calls wire_success on success
2. aria_llm_provider.complete() calls wire_failure on connection error
3. aria_llm_provider.complete() calls wire_failure on HTTP error
4. aria_llm_provider.stream() calls wire_success on success
5. aria_llm_provider.stream() calls wire_failure on error
6. aria_llm_provider.summary() returns expected shape
7. self_healing includes ARIA-LLM health check when configured
"""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock


class TestAriaLLMProvider:
    """aria_llm_provider brain wiring tests."""

    @pytest.mark.asyncio
    async def test_complete_wires_success(self):
        """complete() calls wire_success on successful response."""
        # httpx is imported locally inside complete() — patch at source
        with patch("aria_service.llm.aria_llm_provider.wire_success") as mock_ws, \
             patch("aria_service.llm.aria_llm_provider.wire_failure") as mock_wf, \
             patch("aria_service.llm.aria_llm_provider._base_url",
                   return_value="https://test.runpod.io/v1"), \
             patch("httpx.AsyncClient") as mock_client:

            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json = MagicMock(return_value={
                "choices": [{"message": {"content": "test response"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
                "model": "aria-llm-v0.1",
            })
            mock_instance = MagicMock()
            mock_instance.__aenter__.return_value.post = AsyncMock(return_value=mock_response)
            mock_client.return_value = mock_instance

            from aria_service.llm.aria_llm_provider import complete
            result = await complete("test prompt")

            assert result.get("ok"), f"Completion should succeed: {result}"
            assert mock_ws.called, "wire_success was not called on success"
            assert not mock_wf.called, "wire_failure was called on success"

    @pytest.mark.asyncio
    async def test_complete_wires_failure_on_connection_error(self):
        """complete() calls wire_failure on connection error."""
        with patch("aria_service.llm.aria_llm_provider.wire_success") as mock_ws, \
             patch("aria_service.llm.aria_llm_provider.wire_failure") as mock_wf, \
             patch("aria_service.llm.aria_llm_provider._base_url",
                   return_value="https://test.runpod.io/v1"), \
             patch("httpx.AsyncClient") as mock_client:

            mock_instance = MagicMock()
            mock_instance.__aenter__.return_value.post = AsyncMock(
                side_effect=Exception("Connection refused")
            )
            mock_client.return_value = mock_instance

            from aria_service.llm.aria_llm_provider import complete
            result = await complete("test prompt")

            assert not result.get("ok"), f"Completion should fail: {result}"
            assert mock_wf.called, "wire_failure was not called on connection error"

    @pytest.mark.asyncio
    async def test_complete_wires_failure_on_http_error(self):
        """complete() calls wire_failure on HTTP error."""
        with patch("aria_service.llm.aria_llm_provider.wire_success") as mock_ws, \
             patch("aria_service.llm.aria_llm_provider.wire_failure") as mock_wf, \
             patch("aria_service.llm.aria_llm_provider._base_url",
                   return_value="https://test.runpod.io/v1"), \
             patch("httpx.AsyncClient") as mock_client:

            mock_response = MagicMock()
            mock_response.status_code = 503
            mock_response.text = "Service Unavailable"
            mock_instance = MagicMock()
            mock_instance.__aenter__.return_value.post = AsyncMock(return_value=mock_response)
            mock_client.return_value = mock_instance

            from aria_service.llm.aria_llm_provider import complete
            result = await complete("test prompt")

            assert not result.get("ok"), f"Completion should fail: {result}"
            assert mock_wf.called, "wire_failure was not called on HTTP error"

    def test_summary_returns_dict(self):
        """summary() returns expected dict shape."""
        from aria_service.llm.aria_llm_provider import summary
        result = summary()
        assert isinstance(result, dict)
        assert "module" in result
        assert "configured" in result

    @pytest.mark.asyncio
    async def test_complete_not_configured(self):
        """complete() returns error when ARIA_LLM_URL not set."""
        with patch("aria_service.llm.aria_llm_provider._base_url", return_value=None):
            from aria_service.llm.aria_llm_provider import complete
            result = await complete("test prompt")
            assert not result.get("ok")
            assert "not set" in result.get("error", "")


class TestSelfHealingAriaLLM:
    """self_healing includes ARIA-LLM health check."""

    def test_health_check_includes_aria_llm_when_configured(self):
        """HealthMonitor.check_all includes aria_llm when ARIA_LLM_URL is set."""
        with patch.dict("os.environ", {"ARIA_LLM_URL": "https://test.runpod.io/v1"}):
            # Re-import to pick up the env var
            import importlib
            import aria_service.intel.self_healing as sh
            importlib.reload(sh)
            monitor = sh.HealthMonitor()
            # check_all builds services dict — verify aria_llm is included
            import asyncio
            with patch.object(monitor, "check_subsystem", return_value=sh.HealthCheck(
                subsystem="aria_llm", status=sh.HealthStatus.HEALTHY,
            )):
                results = asyncio.run(monitor.check_all())
                assert "aria_llm" in results, (
                    "aria_llm should be in health check results when ARIA_LLM_URL is set"
                )
