"""
Capability test: R-F1165 — phase gate endpoint returns correct shape.
Tests that GET /api/aria/phase/gates returns the expected structure.
"""
import pytest
from fastapi.testclient import TestClient
from aria_service.main import app

client = TestClient(app)


@pytest.mark.asyncio
async def test_phase_gates_endpoint():
    """The phase gate endpoint must return a well-formed response."""
    from aria_service.routes.aria import phase_gates_ep
    # Call the endpoint directly
    result = await phase_gates_ep()
    assert isinstance(result, dict), f"Expected dict, got {type(result)}"
    assert "phase" in result, f"Missing 'phase' key: {result.keys()}"
    assert result["phase"] == "A", f"Expected phase A, got {result['phase']}"
    assert "gates" in result, f"Missing 'gates' key"
    assert isinstance(result["gates"], list), f"Expected list, got {type(result['gates'])}"
    assert len(result["gates"]) == 7, f"Expected 7 gates, got {len(result['gates'])}"
    assert "summary" in result, f"Missing 'summary' key"
    assert "generated_at" in result, f"Missing 'generated_at' key"
    # Each gate must have id, title, status, value, evidence
    for gate in result["gates"]:
        assert "id" in gate, f"Gate missing 'id': {gate.keys()}"
        assert "title" in gate, f"Gate {gate['id']} missing 'title'"
        assert "status" in gate, f"Gate {gate['id']} missing 'status'"
        assert gate["status"] in ("open", "closed", "unknown"), \
            f"Gate {gate['id']} invalid status: {gate['status']}"
        assert "evidence" in gate, f"Gate {gate['id']} missing 'evidence'"
    # Summary must have closed/open/unknown/total
    summary = result["summary"]
    assert "closed" in summary
    assert "open" in summary
    assert "unknown" in summary
    assert "total" in summary
    assert summary["total"] == 7
    assert summary["closed"] + summary["open"] + summary["unknown"] == 7
