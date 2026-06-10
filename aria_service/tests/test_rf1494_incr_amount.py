"""R-F1494 capability test — incr() honours `amount` on a FRESH key.

R-F1493 replaced the locked read-modify-write in state_store.incr with an atomic
SQL UPSERT (good — it kills the lock contention that wedged the app). But its INSERT
hardcoded value='1', so incr(key, amount=N) on a MISSING key stored 1 instead of N
(e.g. stream_guard_observer.py:180 calls incr(key, amount=count) → silent undercount).
R-F1494 inserts `amount` on a fresh key. These tests would FAIL on the R-F1493 code
(fresh-key incr(5) returned 1) and pass on the fix.
"""
import asyncio
import tempfile

import pytest

from aria_service.intel import state_store as ss


@pytest.mark.asyncio
async def test_incr_fresh_key_uses_amount():
    await ss.connect(tempfile.mktemp(suffix=".db"))
    v = await ss.incr("rf1494_fresh", 5)          # fresh key, amount=5
    assert v == 5, f"fresh-key incr(amount=5) must return 5, got {v} (R-F1493 bug returned 1)"
    v2 = await ss.incr("rf1494_fresh", 3)         # existing -> 8
    assert v2 == 8, f"existing incr must add amount, got {v2}"


@pytest.mark.asyncio
async def test_incr_default_amount_one():
    await ss.connect(tempfile.mktemp(suffix=".db"))
    assert await ss.incr("rf1494_one") == 1
    assert await ss.incr("rf1494_one") == 2


@pytest.mark.asyncio
async def test_concurrent_incrs_no_lost_updates():
    # The point of R-F1493's atomic UPSERT: concurrent increments don't lose updates.
    await ss.connect(tempfile.mktemp(suffix=".db"))
    await asyncio.gather(*[ss.incr("rf1494_conc", 1) for _ in range(50)])
    final = await ss.incr("rf1494_conc", 0)       # +0 reads the current total
    assert final == 50, f"50 concurrent incrs of 1 must total 50, got {final}"
