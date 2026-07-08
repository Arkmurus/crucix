"""R-F2477 — state_store reads must NOT be blinded by writer saturation.

Root cause of the "DD page empty / running forever" incident: under the R-F2277
write storm, _row()'s synchronous read-after-write flush (bounded at 5s = the same
budget as the outer get() 5s) queued behind the saturated single writer and blocked
the full 5s → the actual read never ran → get() timed out → returned None → the app
read LIVE data (the DD report_index, present in the DB) as EMPTY. get() then cached
that None for 5s (for EVERY key, not just error_log) → the blank persisted.

Fixes:
  (1) _READ_FLUSH_BUDGET_S caps the read-path flush (0.3s) so it can never eat the
      read budget — on cap we skip it and read COMMITTED data from the WAL read pool.
  (2) the cooldown cache is scoped to error_log keys only; a transient timeout on any
      other key is never cached as None.
"""
import asyncio
import os
import tempfile

import pytest


async def _ensure_connected(_ss):
    if _ss._conn is None:
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        os.environ["ARIA_STATE_DB_PATH"] = tmp.name
        tmp.close()
        await _ss.connect()


class _FakeQ:
    """Reports a small non-empty queue so _row() takes the flush branch."""
    def empty(self):
        return False
    def qsize(self):
        return 1


@pytest.mark.asyncio
async def test_read_survives_saturated_writer_flush():
    """Fix 1: with a SLOW flush (writer saturated), get() still returns the
    committed value instead of timing out to None."""
    from aria_service.intel import state_store as _ss
    await _ensure_connected(_ss)

    KEY = "_rf2477_live_key"
    await _ss.set_key(KEY, "live_data")
    assert await _ss.get(KEY) == "live_data"  # commit it

    orig_flush = _ss._flush_write_queue
    orig_q = _ss._QUEUED_WRITES

    async def _slow_flush():
        await asyncio.sleep(10)  # writer saturated — flush would block far past 5s

    _ss._flush_write_queue = _slow_flush
    _ss._QUEUED_WRITES = _FakeQ()
    try:
        # Drive _row directly so the assertion is on the READ path itself, not the
        # 5s/5s race between the inner flush-timeout and get()'s outer 5s wrapper.
        loop = asyncio.get_event_loop()
        t0 = loop.time()
        row = await _ss._row(KEY, expected_kind="string")
        dt = loop.time() - t0
    finally:
        _ss._flush_write_queue = orig_flush
        _ss._QUEUED_WRITES = orig_q
        await _ss.delete(KEY)

    assert row and row[0] == "live_data", \
        f"read blinded by slow flush — got {row!r} (should be committed data)"
    # post-fix: flush capped at _READ_FLUSH_BUDGET_S (0.3s) → read returns fast.
    # pre-fix: flush was capped at 5s → _row would block ~5s before reading.
    assert dt < 1.0, f"read took {dt:.1f}s — the flush ate the read budget (cap={_ss._READ_FLUSH_BUDGET_S}s)"


@pytest.mark.asyncio
async def test_transient_timeout_not_cached_for_live_key():
    """Fix 2: a transient read timeout on a non-error_log key must NOT be cached as
    None (which would blind the key for 5s). The next read retries and succeeds."""
    from aria_service.intel import state_store as _ss
    await _ensure_connected(_ss)

    KEY = "_rf2477_report_index_like"   # NOT an error_log key
    _ss._error_log_cache.pop(KEY, None)

    calls = {"n": 0}
    orig_row = _ss._row

    async def _flaky_row(key, expected_kind=None):
        if key == KEY:
            calls["n"] += 1
            if calls["n"] == 1:
                await asyncio.sleep(10)  # first read times out
            return ("recovered", "string", None)
        return await orig_row(key, expected_kind)

    _ss._row = _flaky_row
    try:
        first = await _ss.get(KEY)     # times out → None
        second = await _ss.get(KEY)    # must RETRY (not serve cached None)
    finally:
        _ss._row = orig_row
        _ss._error_log_cache.pop(KEY, None)

    assert first is None, f"expected first read to time out to None, got {first!r}"
    assert second == "recovered", (
        f"live key was poisoned by the timeout cache — 2nd read got {second!r} "
        f"(should retry to 'recovered')")
    assert KEY not in _ss._error_log_cache, "non-error_log key must not be cached at all"


if __name__ == "__main__":
    asyncio.run(test_read_survives_saturated_writer_flush())
    print("PASS test_read_survives_saturated_writer_flush")
    asyncio.run(test_transient_timeout_not_cached_for_live_key())
    print("PASS test_transient_timeout_not_cached_for_live_key")
    print("ALL PASS")
