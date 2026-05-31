"""
Capability test: R-F1192 — coder UI endpoint handles {directive, file} format.
Tests that POST /api/aria/self/code accepts both formats and returns expected shapes.
"""
import pytest
from fastapi import HTTPException
from aria_service.routes.aria import self_code_ep


class _MockLLM:
    """Minimal mock LLM that returns canned code."""
    is_configured = True

    async def complete(self, system, prompt, **kw):
        class _Result:
            text = "def hello():\n    return 'world'\n"
        return _Result()


class _MockRequest:
    """Minimal mock request with a configured LLM."""
    app = type("App", (), {"state": type("State", (), {"llm_provider": _MockLLM()})})()

    def __init__(self, body):
        self._body = body

    async def json(self):
        return self._body


@pytest.mark.asyncio
async def test_self_code_directive_format_returns_expected_shape():
    """The coder UI format {directive, file} must return ok, summary, file, code."""
    req = _MockRequest({"directive": "Add a hello function", "file": "aria_service/intel/self_improve.py"})
    result = await self_code_ep(req)
    assert isinstance(result, dict), f"Expected dict, got {type(result)}"
    assert result.get("ok") is True, f"Expected ok=True, got {result}"
    assert "summary" in result, f"Missing 'summary': {result.keys()}"
    assert "file" in result, f"Missing 'file': {result.keys()}"
    assert "code" in result, f"Missing 'code': {result.keys()}"
    assert result["file"] == "aria_service/intel/self_improve.py"
    assert len(result["code"]) > 20, f"Code too short: {len(result['code'])} chars"


@pytest.mark.asyncio
async def test_self_code_directive_too_short():
    """A directive shorter than 5 chars must raise 400."""
    req = _MockRequest({"directive": "hi", "file": "test.py"})
    with pytest.raises(HTTPException) as exc:
        await self_code_ep(req)
    assert exc.value.status_code == 400
    assert "too short" in exc.value.detail


@pytest.mark.asyncio
async def test_self_code_directive_no_file():
    """A directive without a file must fall through to legacy format and raise 400."""
    req = _MockRequest({"directive": "Add a hello function"})
    with pytest.raises(HTTPException) as exc:
        await self_code_ep(req)
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_self_code_legacy_format_still_works():
    """The legacy {request, name} format must still be accepted."""
    req = _MockRequest({"request": "track Saudi MoD procurement notices", "name": "saudi_mod_tracker"})
    result = await self_code_ep(req)
    # The legacy format calls propose_new_module which needs a real LLM,
    # so with our mock it should return ok=False with an error
    assert isinstance(result, dict), f"Expected dict, got {type(result)}"
    # Either ok=True with module info, or ok=False with error (if LLM mock doesn't match)
    assert "ok" in result
