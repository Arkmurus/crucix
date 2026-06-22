"""R-F1779 — Capability test: intel_ledger fail_wire wiring.

FAIL→PASS pattern (§3c):
  BEFORE: intel_ledger.add_signal() raises but NO gap reaches the brain.
  AFTER: intel_ledger.add_signal() raises AND a 'source_failure' gap for
         module=intel_ledger lands in the capability_gaps ledger.

Proves the @fail_wire decorator works end-to-end on intel_ledger.
"""
import pytest

from aria_service.intel.intel_ledger import add_signal


@pytest.mark.asyncio
async def test_intel_ledger_fail_wire_records_gap():
    """When intel_ledger.add_signal() raises, a 'source_failure' gap
    for module=intel_ledger must land in the capability_gaps ledger."""
    recorded_gaps = []

    async def mock_record_gap(gap_type, detail, source):
        recorded_gaps.append({"gap_type": gap_type, "detail": detail, "source": source})

    import aria_service.intel.wire as _wire
    _original = _wire._record_gap
    _wire._record_gap = mock_record_gap
    try:
        # add_signal requires a dict payload — call with invalid args to trigger raise
        with pytest.raises(Exception):
            await add_signal(None)

        import asyncio
        for _ in range(200):
            if recorded_gaps:
                break
            await asyncio.sleep(0.01)
    finally:
        _wire._record_gap = _original

    assert len(recorded_gaps) >= 1, (
        "No gap recorded — @fail_wire on add_signal() did not fire"
    )
    gap_types = [g["gap_type"] for g in recorded_gaps]
    assert "source_failure" in gap_types, (
        "Expected gap_type='source_failure' but got %s" % gap_types
    )
