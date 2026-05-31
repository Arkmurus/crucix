"""R-F1214: Capability test — self_improve.py brain wiring.

Verifies that every public function in self_improve.py emits brain
signals (wire_success / wire_failure) on both success and failure paths.
"""
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock

from aria_service.intel.self_improve import (
    read_own_code,
    list_own_files,
    stage_improvement,
    deploy_improvement,
    discard_improvement,
    rollback_improvement,
    evolve_prompt,
    record_error,
    diagnose_failure,
    propose_new_module,
    autonomous_improvement_cycle,
    _SI_CYCLES, _SI_STAGED, _SI_DEPLOYED, _SI_FAILURES,
    _SI_DISCARDED, _SI_ROLLED_BACK, _SI_ERRORS_RECORDED,
    _SI_DIAGNOSES, _SI_MODULES_PROPOSED,
)


@pytest.fixture(autouse=True)
def reset_counters():
    """Reset global counters before each test."""
    global _SI_CYCLES, _SI_STAGED, _SI_DEPLOYED, _SI_FAILURES
    global _SI_DISCARDED, _SI_ROLLED_BACK, _SI_ERRORS_RECORDED
    global _SI_DIAGNOSES, _SI_MODULES_PROPOSED
    _SI_CYCLES = 0
    _SI_STAGED = 0
    _SI_DEPLOYED = 0
    _SI_FAILURES = 0
    _SI_DISCARDED = 0
    _SI_ROLLED_BACK = 0
    _SI_ERRORS_RECORDED = 0
    _SI_DIAGNOSES = 0
    _SI_MODULES_PROPOSED = 0


@pytest.mark.asyncio
async def test_read_own_code_wires_success():
    """read_own_code calls wire_success on success."""
    with patch("aria_service.intel.self_improve.wire_success") as mock_ws, \
         patch("aria_service.intel.self_improve.wire_failure") as mock_wf, \
         patch("aria_service.intel.self_improve.MODIFIABLE_FILES", {"test_file.py"}), \
         patch("aria_service.intel.self_improve.PROTECTED_FILES", set()):
        # Build a real Path for _root so the / operator and .resolve() work
        import tempfile, os
        tmpdir = Path(tempfile.mkdtemp())
        test_file = tmpdir / "test_file.py"
        test_file.write_text("def foo():\n    pass\n")
        try:
            with patch("aria_service.intel.self_improve._root", tmpdir):
                result = await read_own_code("test_file.py")
                assert mock_ws.called, "wire_success was not called on successful read"
                assert "error" not in result, f"Unexpected error: {result.get('error')}"
        finally:
            test_file.unlink()
            tmpdir.rmdir()


@pytest.mark.asyncio
async def test_read_own_code_wires_failure_on_protected():
    """read_own_code calls wire_failure on protected file access."""
    with patch("aria_service.intel.self_improve.wire_success") as mock_ws, \
         patch("aria_service.intel.self_improve.wire_failure") as mock_wf, \
         patch("aria_service.intel.self_improve.PROTECTED_FILES", {"secret.key"}):
        result = await read_own_code("secret.key")
        assert mock_wf.called, "wire_failure was not called on protected file access"
        assert "error" in result


@pytest.mark.asyncio
async def test_list_own_files_wires_success():
    """list_own_files calls wire_success."""
    with patch("aria_service.intel.self_improve.wire_success") as mock_ws, \
         patch("aria_service.intel.self_improve.MODIFIABLE_FILES", set()):
        result = await list_own_files()
        assert mock_ws.called, "wire_success was not called on list_own_files"
        assert isinstance(result, list)


@pytest.mark.asyncio
async def test_stage_improvement_wires_failure_on_bad_type():
    """stage_improvement calls wire_failure on unknown change type."""
    with patch("aria_service.intel.self_improve.wire_success") as mock_ws, \
         patch("aria_service.intel.self_improve.wire_failure") as mock_wf, \
         patch("aria_service.intel.self_improve.MODIFIABLE_FILES", {"test.py"}), \
         patch("aria_service.intel.self_improve.CHANGE_TYPES", {"bug_fix": {"auto_deploy": False}}):
        result = await stage_improvement("test.py", "content", "invalid_type", "desc")
        assert mock_wf.called, "wire_failure was not called on invalid change type"
        assert "error" in result


@pytest.mark.asyncio
async def test_discard_improvement_wires_failure_on_missing_id():
    """discard_improvement calls wire_failure on missing improvement_id."""
    with patch("aria_service.intel.self_improve.wire_success") as mock_ws, \
         patch("aria_service.intel.self_improve.wire_failure") as mock_wf:
        result = await discard_improvement("", "reason")
        assert mock_wf.called, "wire_failure was not called on missing id"
        assert not result.get("ok")


@pytest.mark.asyncio
async def test_evolve_prompt_wires_failure_on_llm_error():
    """evolve_prompt calls wire_failure when LLM call fails."""
    mock_llm = MagicMock()
    mock_llm.complete = AsyncMock(side_effect=Exception("LLM down"))
    with patch("aria_service.intel.self_improve.wire_success") as mock_ws, \
         patch("aria_service.intel.self_improve.wire_failure") as mock_wf:
        result = await evolve_prompt(mock_llm, "current prompt", "feedback")
        assert mock_wf.called, "wire_failure was not called on LLM error"
        assert "error" in result


@pytest.mark.asyncio
async def test_record_error_increments_counter():
    """record_error increments _SI_ERRORS_RECORDED."""
    import aria_service.intel.self_improve as _si
    before = _si._SI_ERRORS_RECORDED
    with patch("aria_service.intel.self_improve.rs.get_json", return_value=[]), \
         patch("aria_service.intel.self_improve.rs.set_json"):
        await record_error("test_error", "test message")
        assert _si._SI_ERRORS_RECORDED == before + 1


@pytest.mark.asyncio
async def test_diagnose_failure_wires_critical():
    """diagnose_failure calls wire_failure for critical severity."""
    with patch("aria_service.intel.self_improve.wire_success") as mock_ws, \
         patch("aria_service.intel.self_improve.wire_failure") as mock_wf, \
         patch("aria_service.intel.self_improve.rs.get_json", return_value=[]), \
         patch("aria_service.intel.self_improve.rs.set_json"):
        result = await diagnose_failure("oom", "out of memory error")
        assert mock_wf.called, "wire_failure was not called for critical failure"
        assert result.get("severity") == "critical"


@pytest.mark.asyncio
async def test_diagnose_failure_wires_success_for_non_critical():
    """diagnose_failure calls wire_success for non-critical."""
    with patch("aria_service.intel.self_improve.wire_success") as mock_ws, \
         patch("aria_service.intel.self_improve.wire_failure") as mock_wf, \
         patch("aria_service.intel.self_improve.rs.get_json", return_value=[]), \
         patch("aria_service.intel.self_improve.rs.set_json"):
        result = await diagnose_failure("timeout", "502 bad gateway")
        assert mock_ws.called, "wire_success was not called for non-critical failure"
        assert result.get("severity") != "critical"


@pytest.mark.asyncio
async def test_autonomous_cycle_wires_success():
    """autonomous_improvement_cycle calls wire_success."""
    mock_llm = MagicMock()
    mock_llm.is_configured = True
    with patch("aria_service.intel.self_improve.wire_success") as mock_ws, \
         patch("aria_service.intel.self_improve.wire_failure") as mock_wf, \
         patch("aria_service.intel.self_improve.get_recent_errors", return_value=[]), \
         patch("aria_service.intel.self_improve.rs.get_json", return_value={}), \
         patch("aria_service.intel.self_improve.rs.set_json"), \
         patch("aria_service.intel.self_improve._log_improvement"):
        result = await autonomous_improvement_cycle(mock_llm)
        assert mock_ws.called, "wire_success was not called on cycle completion"
        assert "cycle_end" in result


@pytest.mark.asyncio
async def test_propose_new_module_wires_failure_on_no_llm():
    """propose_new_module calls wire_failure when LLM not configured."""
    mock_llm = MagicMock()
    mock_llm.is_configured = False
    with patch("aria_service.intel.self_improve.wire_success") as mock_ws, \
         patch("aria_service.intel.self_improve.wire_failure") as mock_wf:
        result = await propose_new_module("test request", mock_llm)
        assert not result.get("ok")
