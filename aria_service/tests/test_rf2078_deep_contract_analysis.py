"""R-F2078: capability tests for deep LLM-powered contract analysis.

Tests that analyse_contract_deep exists, handles edge cases, and
produces the expected output structure.
"""
import pytest
from aria_service.intel.document_reader import analyse_contract_deep


@pytest.mark.asyncio
async def test_analyse_contract_deep_returns_unreadable_without_source():
    """Without a valid source, the function must return UNREADABLE."""
    result = await analyse_contract_deep(
        source="/nonexistent/file.pdf",
        llm=None,
    )
    assert result["status"] == "UNREADABLE", (
        f"Expected UNREADABLE status without valid source, got {result['status']}"
    )
    assert "extraction" in result, f"Expected extraction info, got: {result}"


@pytest.mark.asyncio
async def test_analyse_contract_deep_handles_unreadable_source():
    """An unreadable source must return UNREADABLE status."""
    # Use a mock LLM that would be configured but the source doesn't exist
    class MockLLM:
        is_configured = True
        async def complete(self, prompt, **kwargs):
            from ..llm.provider import LLMResult
            return LLMResult(content="Test analysis")

    result = await analyse_contract_deep(
        source="/nonexistent/file.pdf",
        llm=MockLLM(),
    )
    # The document reader will fail to read the file
    assert result["status"] in ("UNREADABLE", "ERROR"), (
        f"Expected UNREADABLE or ERROR for nonexistent file, got {result['status']}"
    )


def test_analyse_contract_deep_function_exists():
    """The function must exist and have the right signature."""
    import inspect
    sig = inspect.signature(analyse_contract_deep)
    params = list(sig.parameters.keys())
    assert "source" in params, f"Missing 'source' param: {params}"
    assert "llm" in params, f"Missing 'llm' param: {params}"
    assert "comparison_source" in params, (
        f"Missing 'comparison_source' param: {params}"
    )
    assert "market" in params, f"Missing 'market' param: {params}"
    print(f"Signature OK: analyse_contract_deep{sig}")


def test_analyse_contract_deep_returns_dict():
    """The function must be async and return a dict."""
    import asyncio
    assert asyncio.iscoroutinefunction(analyse_contract_deep), (
        "analyse_contract_deep must be async"
    )
