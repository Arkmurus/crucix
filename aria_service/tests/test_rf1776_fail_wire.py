"""R-F1776 — Capability test for the fail_wire decorator.

FAIL→PASS pattern (§3c):
  BEFORE: a function without fail_wire does NOT signal on exception.
  AFTER: a function WITH fail_wire DOES signal on exception (gap lands in ledger).
  Also proves: original exception re-raises, task completes (no GC leak).
"""
import pytest
from unittest.mock import patch, AsyncMock, MagicMock

from aria_service.intel.wire import fail_wire, _BG_TASKS


@pytest.mark.asyncio
async def test_fail_wire_re_raises_original_exception():
    """The decorator must re-raise the original exception so callers
    still see it — the wire is additive, not a replacement for error handling."""
    recorded_gaps = []

    async def mock_record_gap(gap_type, detail, source):
        recorded_gaps.append({"gap_type": gap_type, "detail": detail, "source": source})

    @fail_wire(module="test_module", gap_type="test_gap")
    async def will_fail():
        raise ValueError("original error")

    import aria_service.intel.wire as _wire
    _original = _wire._record_gap
    _wire._record_gap = mock_record_gap
    try:
        with pytest.raises(ValueError, match="original error"):
            await will_fail()
    finally:
        _wire._record_gap = _original

    # The original exception must reach the caller
    assert len(recorded_gaps) == 0, "Gap should not be recorded synchronously (fire-and-forget)"


@pytest.mark.asyncio
async def test_fail_wire_records_gap_on_exception():
    """When a wrapped function raises, the decorator must fire a
    record_gap call that eventually lands in the ledger."""
    recorded_gaps = []

    async def mock_record_gap(gap_type, detail, source):
        recorded_gaps.append({"gap_type": gap_type, "detail": detail, "source": source})

    @fail_wire(module="test_module", gap_type="test_gap", source="test_source")
    async def will_fail():
        raise RuntimeError("something broke")

    # Apply patch MANUALLY (not as context manager) so it stays active
    # until the background task completes. The task runs fire-and-forget
    # via loop.create_task(), so a context manager would restore the
    # original before the task executes.
    import aria_service.intel.wire as _wire
    _original = _wire._record_gap
    _wire._record_gap = mock_record_gap
    try:
        with pytest.raises(RuntimeError):
            await will_fail()

        # Wait for the background task to complete
        import asyncio
        for _ in range(200):
            if recorded_gaps:
                break
            await asyncio.sleep(0.01)
    finally:
        _wire._record_gap = _original

    assert len(recorded_gaps) == 1, "Gap must be recorded (got %d)" % len(recorded_gaps)
    assert recorded_gaps[0]["gap_type"] == "test_gap"
    assert "something broke" in recorded_gaps[0]["detail"]
    assert recorded_gaps[0]["source"] == "test_source"


@pytest.mark.asyncio
async def test_fail_wire_does_not_record_on_success():
    """When a wrapped function succeeds, NO gap should be recorded
    (failure-only by design — per-call success signals would wedge)."""
    recorded_gaps = []

    async def mock_record_gap(gap_type, detail, source):
        recorded_gaps.append({"gap_type": gap_type, "detail": detail, "source": source})

    @fail_wire(module="test_module", gap_type="test_gap")
    async def will_succeed():
        return "success"

    import aria_service.intel.wire as _wire
    _original = _wire._record_gap
    _wire._record_gap = mock_record_gap
    try:
        result = await will_succeed()
        import asyncio
        await asyncio.sleep(0.05)
    finally:
        _wire._record_gap = _original

    assert result == "success"
    assert len(recorded_gaps) == 0, "No gap should be recorded on success"


@pytest.mark.asyncio
async def test_fail_wire_task_not_gc_collected():
    """The background task must NOT be GC-collected before it completes.
    Proves the strong reference (_BG_TASKS) prevents the R-F1363 bug class."""
    recorded_gaps = []
    task_completed = False

    async def mock_record_gap(gap_type, detail, source):
        nonlocal task_completed
        recorded_gaps.append({"gap_type": gap_type, "detail": detail, "source": source})
        task_completed = True

    @fail_wire(module="test_module", gap_type="test_gap")
    async def will_fail():
        raise ValueError("gc leak test")

    import aria_service.intel.wire as _wire
    _original = _wire._record_gap
    _wire._record_gap = mock_record_gap
    try:
        with pytest.raises(ValueError):
            await will_fail()

        # Force GC to try to collect the task
        import gc
        gc.collect()

        # Wait for the task to complete
        import asyncio
        for _ in range(200):
            if task_completed:
                break
            await asyncio.sleep(0.01)
    finally:
        _wire._record_gap = _original

    assert task_completed, "Background task was GC-collected before completing (R-F1363 bug class)"
    assert len(recorded_gaps) == 1, "Gap must be recorded despite GC pressure (got %d)" % len(recorded_gaps)
