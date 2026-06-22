"""R-F1777 — Capability test: deep_researcher fail_wire wiring.

FAIL→PASS pattern (§3c):
  BEFORE: deep_researcher.investigate() raises but NO gap reaches the brain.
  AFTER: deep_researcher.investigate() raises AND a 'source_failure' gap for
         module=deep_researcher lands in the capability_gaps ledger.

Proves the @fail_wire decorator actually works end-to-end on a real module.
"""
import pytest
from unittest.mock import patch, AsyncMock

from aria_service.intel.deep_researcher import investigate
from aria_service.intel.wire import _BG_TASKS


@pytest.mark.asyncio
async def test_deep_researcher_fail_wire_records_gap():
    """When deep_researcher.investigate() raises, a 'source_failure' gap
    for module=deep_researcher must land in the capability_gaps ledger.

    This proves the @fail_wire decorator actually works end-to-end on a
    real module — not just that the decorator is syntactically present.
    """
    recorded_gaps = []

    async def mock_record_gap(gap_type, detail, source):
        recorded_gaps.append({"gap_type": gap_type, "detail": detail, "source": source})

    # Patch the wire's _record_gap so we can intercept the signal
    import aria_service.intel.wire as _wire
    _original = _wire._record_gap
    _wire._record_gap = mock_record_gap
    try:
        # investigate() requires an LLM provider — we don't have one in test,
        # so it will raise when called without proper args. The @fail_wire
        # decorator should catch the exception and fire a record_gap call.
        with pytest.raises(Exception):
            await investigate()

        # Wait for the background task to complete (proves no GC leak)
        import asyncio
        for _ in range(200):
            if recorded_gaps:
                break
            await asyncio.sleep(0.01)
    finally:
        _wire._record_gap = _original

    assert len(recorded_gaps) >= 1, (
        "No gap was recorded — @fail_wire on investigate() did not fire. "
        "Expected a 'source_failure' gap for module=deep_researcher."
    )
    # Check the gap has the right type
    gap_types = [g["gap_type"] for g in recorded_gaps]
    assert "source_failure" in gap_types, (
        "Expected gap_type='source_failure' but got %s" % gap_types
    )
    # Check the detail mentions deep_researcher
    details = [g["detail"] for g in recorded_gaps]
    assert any("deep_researcher" in d or "investigate" in d for d in details), (
        "Gap detail should mention deep_researcher or investigate, got: %s" % details
    )
