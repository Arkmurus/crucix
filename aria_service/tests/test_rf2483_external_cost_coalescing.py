"""R-F2483 — record_external_call must coalesce its hot-key writes (mirrors the
R-F2172 LLM path). A DD fires dozens of external searches; the old path did ~7
state_store ops per call (record + INDEX/AGG/MONTH read-modify-write) — the
dominant single-writer amplifier. Coalesced: N calls → one batched write per hot
key per interval, with the same correct totals and fold-back-on-failure (no cost
data lost). The $300 LLM cap is untouched (separate atomic reserve).
"""
import asyncio
import os
import tempfile
import time

import pytest


async def _connect(_ss):
    if _ss._conn is None:
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        os.environ["ARIA_STATE_DB_PATH"] = tmp.name
        tmp.close()
        await _ss.connect()


@pytest.mark.asyncio
async def test_external_calls_coalesce_with_correct_totals():
    from aria_service.intel import state_store as _ss, cost_tracker as ct
    from aria_service.intel import redis_store as rs
    await _connect(_ss)

    await rs.delete(ct.EXTERNAL_AGG_KEY)
    ct._pending_external_records = []
    ct._ext_last_flush = time.time()   # hold the interval gate so calls buffer

    N = 6
    for i in range(N):
        await ct.record_external_call(service="brave", operation="search",
                                      cost_usd=0.002, success=(i != 0))

    # Coalesced: nothing flushed yet (gated) — all N buffered, hot key NOT written N times.
    assert len(ct._pending_external_records) == N, ct._pending_external_records

    wrote = await ct._flush_external_pending(force=True)
    assert wrote is True
    assert ct._pending_external_records == []

    agg = await rs.get_json(ct.EXTERNAL_AGG_KEY) or {}
    brave = agg.get("brave") or {}
    assert brave.get("calls") == N, agg
    assert round(brave.get("cost_usd") or 0.0, 6) == round(0.002 * N, 6), agg
    assert brave.get("errors") == 1, agg   # the i==0 call was a failure


@pytest.mark.asyncio
async def test_flush_failure_folds_back_no_cost_lost():
    from aria_service.intel import state_store as _ss, cost_tracker as ct
    from aria_service.intel import redis_store as rs
    await _connect(_ss)

    ct._pending_external_records = []
    ct._ext_last_flush = time.time()
    await ct.record_external_call(service="gdelt", operation="search", cost_usd=0.0)
    assert len(ct._pending_external_records) == 1

    orig = rs.get_json

    async def _boom(*a, **k):
        raise RuntimeError("db down mid-flush")

    rs.get_json = _boom
    try:
        wrote = await ct._flush_external_pending(force=True)
    finally:
        rs.get_json = orig

    assert wrote is False
    assert len(ct._pending_external_records) == 1, "batch must fold back — no cost lost"


if __name__ == "__main__":
    asyncio.run(test_external_calls_coalesce_with_correct_totals())
    print("PASS test_external_calls_coalesce_with_correct_totals")
    asyncio.run(test_flush_failure_folds_back_no_cost_lost())
    print("PASS test_flush_failure_folds_back_no_cost_lost")
    print("ALL PASS")
