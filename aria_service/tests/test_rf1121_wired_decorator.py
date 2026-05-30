"""R-F1121 — Capability tests for the @wired decorator.

Tests that:
1. @wired calls wire_success on successful completion
2. @wired calls wire_failure on exception
3. @wired passes through the return value
4. @wired re-raises the exception
5. Placeholder substitution works in summary/detail
6. entity_arg extracts the right kwarg
7. capture_result appends result to detail
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from aria_service.intel.engine_wiring import wired


# ── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture
def mock_wire_success():
    with patch("aria_service.intel.engine_wiring.wire_success") as m:
        yield m


@pytest.fixture
def mock_wire_failure():
    with patch("aria_service.intel.engine_wiring.wire_failure") as m:
        yield m


# ── Tests ───────────────────────────────────────────────────────────────────

class TestWiredDecorator:
    """Proves the @wired decorator fires brain signals on both paths."""

    async def test_success_calls_wire_success(self, mock_wire_success, mock_wire_failure):
        """On success, wire_success is called and wire_failure is NOT called."""

        @wired(module="test_module", summary="Test completed")
        async def my_func() -> str:
            return "ok"

        result = await my_func()
        assert result == "ok"
        mock_wire_success.assert_called_once()
        args, kwargs = mock_wire_success.call_args
        assert kwargs.get("module") == "test_module"
        assert "Test completed" in kwargs.get("summary", "")
        mock_wire_failure.assert_not_called()

    async def test_failure_calls_wire_failure(self, mock_wire_success, mock_wire_failure):
        """On exception, wire_failure is called and wire_success is NOT called."""

        @wired(module="test_module", detail="Boom: {msg}")
        async def my_func(msg: str) -> str:
            raise ValueError(msg)

        with pytest.raises(ValueError, match="broken"):
            await my_func(msg="broken")

        mock_wire_failure.assert_called_once()
        args, kwargs = mock_wire_failure.call_args
        assert kwargs.get("module") == "test_module"
        assert "broken" in kwargs.get("detail", "")
        mock_wire_success.assert_not_called()

    async def test_passes_through_return_value(self, mock_wire_success, mock_wire_failure):
        """The return value is passed through unchanged."""

        @wired(module="test")
        async def my_func() -> dict:
            return {"key": "value", "count": 42}

        result = await my_func()
        assert result == {"key": "value", "count": 42}

    async def test_re_raises_exception(self, mock_wire_success, mock_wire_failure):
        """The original exception is re-raised."""

        @wired(module="test")
        async def my_func() -> None:
            raise RuntimeError("original error")

        with pytest.raises(RuntimeError, match="original error"):
            await my_func()

    async def test_placeholder_substitution(self, mock_wire_success, mock_wire_failure):
        """{arg_name} placeholders are filled from kwargs."""

        @wired(module="test", summary="Analysis for {entity}", detail="Detail: {entity}")
        async def my_func(entity: str) -> str:
            return "done"

        await my_func(entity="Acme Corp")
        mock_wire_success.assert_called_once()
        args, kwargs = mock_wire_success.call_args
        assert "Acme Corp" in kwargs.get("summary", "")
        assert "Acme Corp" in kwargs.get("detail", "")

    async def test_entity_arg_extraction(self, mock_wire_success, mock_wire_failure):
        """entity_arg extracts the named kwarg as entity_name."""

        @wired(module="test", summary="Done", entity_arg="target_name")
        async def my_func(target_name: str) -> str:
            return "done"

        await my_func(target_name="Evil Corp")
        mock_wire_success.assert_called_once()
        args, kwargs = mock_wire_success.call_args
        assert kwargs.get("entity_name") == "Evil Corp"

    async def test_capture_result_appends_to_detail(self, mock_wire_success, mock_wire_failure):
        """capture_result=True appends the result string to detail."""

        @wired(module="test", summary="Done", detail="Base detail", capture_result=True)
        async def my_func() -> dict:
            return {"status": "completed", "count": 5}

        await my_func()
        mock_wire_success.assert_called_once()
        args, kwargs = mock_wire_success.call_args
        detail = kwargs.get("detail", "")
        assert "Base detail" in detail
        assert "completed" in detail

    async def test_default_module_from_function(self, mock_wire_success, mock_wire_failure):
        """When module is empty, it defaults to the function's __module__."""

        @wired(summary="Done")
        async def my_func() -> str:
            return "ok"

        await my_func()
        mock_wire_success.assert_called_once()
        args, kwargs = mock_wire_success.call_args
        assert "test_rf1121_wired_decorator" in kwargs.get("module", "")

    async def test_noop_on_clean_no_args(self, mock_wire_success, mock_wire_failure):
        """@wired with no args still works (defaults all)."""

        @wired()
        async def my_func() -> str:
            return "ok"

        result = await my_func()
        assert result == "ok"
        mock_wire_success.assert_called_once()
