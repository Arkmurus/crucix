"""R-F1779 — Capability test: intel_ledger fail_wire wiring (async + sync).

FAIL→PASS pattern (§3c):
  BEFORE: intel_ledger functions raise but NO gap reaches the brain.
  AFTER: intel_ledger functions raise AND an 'engine_failure' gap for
         module=intel_ledger lands in the capability_gaps ledger.

Tests BOTH async (add_signal) and sync (query_ledger) paths.
"""
import pytest

from aria_service.intel.intel_ledger import add_signal, query_ledger


@pytest.mark.asyncio
async def test_intel_ledger_async_fail_wire_records_gap():
    """When async add_signal() raises, an 'engine_failure' gap must land."""
    recorded_gaps = []

    async def mock_record_gap(gap_type, detail, source):
        recorded_gaps.append({"gap_type": gap_type, "detail": detail, "source": source})

    import aria_service.intel.wire as _wire
    _original = _wire._record_gap
    _wire._record_gap = mock_record_gap
    try:
        with pytest.raises(Exception):
            await add_signal(None)

        import asyncio
        for _ in range(200):
            if recorded_gaps:
                break
            await asyncio.sleep(0.01)
    finally:
        _wire._record_gap = _original

    assert len(recorded_gaps) >= 1, "No gap recorded — @fail_wire on add_signal() did not fire"
    assert "engine_failure" in [g["gap_type"] for g in recorded_gaps], (
        "Expected gap_type='engine_failure' but got %s" % [g["gap_type"] for g in recorded_gaps]
    )


def test_intel_ledger_sync_fail_wire_records_gap():
    """When SYNC query_ledger() raises, an 'engine_failure' gap must land.

    This proves the sync wrapper in fail_wire works correctly — sync
    functions are wrapped with a sync try/except that calls the same
    _wire_failure non-blocking sink.

    Uses a malformed _cache entry to trigger a REAL failure in the
    scoring loop, not a defensive early-return.

    NOTE: the sync wrapper's _wire_failure needs a running event loop to
    create the background task. We run the entire test inside asyncio.run()
    to provide one.
    """
    import asyncio

    async def _run_test():
        recorded_gaps = []

        async def mock_record_gap(gap_type, detail, source):
            recorded_gaps.append({"gap_type": gap_type, "detail": detail, "source": source})

        import aria_service.intel.wire as _wire
        import aria_service.intel.intel_ledger as _il

        _original_record = _wire._record_gap
        _original_cache = _il._cache
        _wire._record_gap = mock_record_gap

        malformed_cache = {
            "signals": [{
                "countries": None,
                "topics": ["test"],
                "content": "test",
                "ts": 1000,
            }]
        }
        _il._cache = malformed_cache
        try:
            with pytest.raises(Exception):
                # query_ledger is sync — call it directly, the sync wrapper
                # will fire _wire_failure which creates a bg task on the loop
                query_ledger("test")

            # Wait for the background task to complete
            import time
            deadline = time.time() + 5
            while time.time() < deadline:
                if recorded_gaps:
                    break
                await asyncio.sleep(0.01)
        finally:
            _wire._record_gap = _original_record
            _il._cache = _original_cache

        assert len(recorded_gaps) >= 1, "No gap recorded — @fail_wire on query_ledger() did not fire"
        assert "engine_failure" in [g["gap_type"] for g in recorded_gaps], (
            "Expected gap_type='engine_failure' but got %s" % [g["gap_type"] for g in recorded_gaps]
        )

    asyncio.run(_run_test())
