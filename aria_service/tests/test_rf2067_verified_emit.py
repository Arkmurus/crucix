"""R-F2067: capability test for the integrated verified_emit flow."""
from __future__ import annotations

from aria_service.intel.truth_verifier import verified_emit, TruthVerifier


def test_verified_emit_imports():
    """verified_emit is importable."""
    from aria_service.intel.truth_verifier import verified_emit
    assert callable(verified_emit)


def test_verified_emit_file_content():
    """verified_emit succeeds with file_content evidence."""
    result = verified_emit(
        "aria_service/intel/truth_verifier.py exists",
        ["file_content"],
    )
    assert result["verified"], f"Should verify: {result.get('error', '')}"
    assert "report" in result
    assert result["report"]["claims_verified"] >= 1


def test_verified_emit_missing_file():
    """verified_emit fails with missing file evidence."""
    result = verified_emit(
        "aria_service/intel/nonexistent.py does not exist",
        ["file_content"],
    )
    assert not result["verified"], "Should fail for missing file"
    assert "error" in result


def test_verified_emit_unknown_evidence():
    """verified_emit fails with unknown evidence type."""
    result = verified_emit(
        "Test claim",
        ["nonexistent_type"],
    )
    assert not result["verified"], "Should fail for unknown evidence type"


def test_verified_emit_reuses_verifier():
    """verified_emit can reuse an existing verifier."""
    verifier = TruthVerifier()
    result1 = verified_emit(
        "aria_service/intel/truth_verifier.py exists",
        ["file_content"],
        verifier=verifier,
    )
    assert result1["verified"], f"First claim failed: {result1.get('error', '')}"
    assert verifier.get_report()["claims_verified"] == 1

    result2 = verified_emit(
        "aria_service/intel/truth_verifier.py still exists",
        ["file_content"],
        verifier=verifier,
    )
    assert result2["verified"], f"Second claim failed: {result2.get('error', '')}"
    assert verifier.get_report()["claims_verified"] == 2
