"""R-F1788 — Phase 1 batch 1: reasoning_router + symbolic_reasoner + local_brain.

FAIL->PASS (§3c): BEFORE, a failure in these reasoning modules reached nothing
(dark path). AFTER, @fail_wire emits an 'engine_failure' gap to the brain on any
unhandled exception.

Each test forces a REAL failure via a no-args call to a REQUIRED-arg function
(TypeError, caught by the wrapper before any side effect) and asserts the gap
lands with the module's registered gap_type. This proves the wiring fires in
each module without executing the no-required-arg getter functions.
"""
import asyncio

import pytest

import aria_service.intel.wire as wire
from aria_service.intel.reasoning_router import try_local_reasoning
from aria_service.intel.symbolic_reasoner import reason
from aria_service.intel.local_brain import try_local_response


async def _force_and_assert(call, expected_module_type="engine_failure"):
    recorded = []

    async def _mock(gap_type, detail, source):
        recorded.append({"gap_type": gap_type, "detail": detail, "source": source})

    original = wire._record_gap
    wire._record_gap = _mock
    try:
        with pytest.raises(Exception):
            await call()
        for _ in range(200):
            if recorded:
                break
            await asyncio.sleep(0.01)
    finally:
        wire._record_gap = original

    assert len(recorded) >= 1, "no gap recorded — @fail_wire did not fire"
    assert expected_module_type in [g["gap_type"] for g in recorded], (
        f"expected '{expected_module_type}' gap, got {[g['gap_type'] for g in recorded]}"
    )


@pytest.mark.asyncio
async def test_reasoning_router_async_fail_wire_records_gap():
    # try_local_reasoning(question) — no-args -> TypeError, caught by wrapper.
    await _force_and_assert(lambda: try_local_reasoning())


@pytest.mark.asyncio
async def test_local_brain_async_fail_wire_records_gap():
    # try_local_response(message) — no-args -> TypeError.
    await _force_and_assert(lambda: try_local_response())


def test_symbolic_reasoner_sync_fail_wire_records_gap():
    # reason(question) is SYNC — its wrapper's _wire_failure needs a running
    # loop to create the bg task, so drive the whole test under asyncio.run.
    async def _run():
        recorded = []

        async def _mock(gap_type, detail, source):
            recorded.append({"gap_type": gap_type, "detail": detail, "source": source})

        original = wire._record_gap
        wire._record_gap = _mock
        try:
            with pytest.raises(Exception):
                reason()  # no-args -> TypeError, sync wrapper catches it
            import time
            deadline = time.time() + 5
            while time.time() < deadline:
                if recorded:
                    break
                await asyncio.sleep(0.01)
        finally:
            wire._record_gap = original

        assert len(recorded) >= 1, "no gap recorded — sync @fail_wire did not fire"
        assert "engine_failure" in [g["gap_type"] for g in recorded]

    asyncio.run(_run())
