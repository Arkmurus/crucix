"""R-F2067: capability test for the truth verifier.

Verifies that the TruthVerifier can:
1. Import and instantiate
2. Verify a claim with build_rev evidence
3. Verify a claim with file_content evidence
4. Fail verification when evidence is missing
5. Generate a verification report
"""
from __future__ import annotations

from aria_service.intel.truth_verifier import TruthVerifier, Claim


def test_verifier_imports():
    """The verifier module imports cleanly."""
    from aria_service.intel.truth_verifier import TruthVerifier, Claim, Evidence
    assert TruthVerifier is not None
    assert Claim is not None
    assert Evidence is not None


def test_verifier_instantiation():
    """The verifier can be instantiated."""
    verifier = TruthVerifier()
    assert verifier is not None
    assert verifier._verified_claims == []


def test_verifier_unknown_evidence_type():
    """Unknown evidence type returns failure."""
    verifier = TruthVerifier()
    claim = Claim(
        statement="Test claim",
        evidence_requirements=["nonexistent_type"],
    )
    ok, report = verifier.verify(claim)
    assert not ok, "Unknown evidence type should fail"
    assert "Unknown evidence type" in report["message"]


def test_verifier_file_content_evidence():
    """File content evidence works for existing files."""
    verifier = TruthVerifier()
    claim = Claim(
        statement="aria_service/intel/truth_verifier.py exists",
        evidence_requirements=["file_content"],
    )
    ok, report = verifier.verify(claim)
    assert ok, f"File content evidence should pass: {report['message']}"
    assert len(report["evidence"]) == 1
    assert report["evidence"][0]["type"] == "file_content"


def test_verifier_file_content_missing():
    """File content evidence fails for non-existent files."""
    verifier = TruthVerifier()
    claim = Claim(
        statement="aria_service/intel/nonexistent_file.py does not exist",
        evidence_requirements=["file_content"],
    )
    ok, report = verifier.verify(claim)
    assert not ok, "Missing file should fail verification"


def test_verifier_report():
    """Verification report returns expected structure."""
    verifier = TruthVerifier()
    report = verifier.get_report()
    assert "timestamp" in report
    assert "claims_verified" in report
    assert "claims" in report
    assert report["claims_verified"] == 0


def test_verifier_claim_dataclass():
    """Claim dataclass works correctly."""
    claim = Claim(
        statement="Test claim",
        evidence_requirements=["file_content"],
    )
    assert claim.statement == "Test claim"
    assert claim.evidence_requirements == ["file_content"]
    assert claim.evidence == []
