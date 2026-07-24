"""R-F3006 — durable month-rollup: a rollup read-failure must RE-QUEUE the batch's
records (retry on the next flush), not silently drop their rollup contribution.

This was the root cause of the live ~89% rollup under-count (4,443 of 39,834 July
calls). _load_rollup_for_update returns None on a store-read failure (R-F2854 fail-
closed, to avoid resetting the total), and the flush then did `continue` AFTER already
consuming the batch and counting it into the index + per-feature aggregate — so the
rollup silently under-counted while records persisted. This drives the real flush and
asserts: (1) on read-fail the records are re-queued, not dropped; (2) a later flush
applies them to the rollup exactly; (3) index/agg count each call ONCE (retries are
rollup-only).
"""
from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, patch

from aria_service.intel import cost_tracker as ct


class _StoreReadError(Exception):
    pass


def _reset():
    ct._pending_cost_records.clear()
    ct._pending_rollup_records.clear()
    ct._cost_last_flush = 0.0


def _rec(cid, cost):
    return {"id": cid, "ts": time.time(), "model": "deepseek-v4-flash",
            "provider": "deepseek", "feature": "uncategorized", "tier": "unattributed",
            "input_tokens": 1000, "output_tokens": 100, "total_tokens": 1100, "cost_usd": cost}


def test_rf3006_rollup_readfail_requeues_then_applies_exactly_once():
    _reset()
    store: dict = {}
    strict_failed = {"done": False}

    async def _set(k, v, ex=None):
        store[k] = v

    async def _get(k):            # non-strict: index + per-feature aggregate
        return store.get(k)

    async def _strict(k):         # strict: month rollup read
        if k.startswith(ct.COST_MONTH_PREFIX) and not strict_failed["done"]:
            strict_failed["done"] = True
            raise _StoreReadError("state_store wedged")
        return store.get(k)

    ct._pending_cost_records.extend([_rec("a", 0.001), _rec("b", 0.002)])
    month_key = ct.COST_MONTH_PREFIX + ct._current_month_key()

    with patch.object(ct.rs, "set_json", AsyncMock(side_effect=_set)), \
         patch.object(ct.rs, "get_json", AsyncMock(side_effect=_get)), \
         patch.object(ct.rs, "get_json_strict", AsyncMock(side_effect=_strict)):
        # Flush 1 — rollup read FAILS. Old behaviour dropped the batch's rollup
        # contribution; R-F3006 re-queues it.
        asyncio.run(ct._flush_cost_pending(force=True))
        assert len(ct._pending_rollup_records) == 2, "read-fail must RE-QUEUE, not drop"
        assert month_key not in store, "rollup not written on a failed read (fail-closed)"
        # but index + aggregate were applied once already
        assert store[ct.COST_AGG_KEY]["uncategorized"]["calls"] == 2

        # Flush 2 — store recovered; the re-queued records are applied to the rollup.
        asyncio.run(ct._flush_cost_pending(force=True))
        assert not ct._pending_rollup_records, "retry buffer drained after success"
        roll = store[month_key]
        assert roll["total_calls"] == 2, "rollup now counts BOTH calls (no under-count)"
        assert abs(roll["total_cost_usd"] - 0.003) < 1e-9
        # index/agg must NOT be double-counted by the rollup retry
        assert store[ct.COST_AGG_KEY]["uncategorized"]["calls"] == 2, "agg counted once, not twice"
    _reset()


def test_rf3006_flush_runs_for_pending_retries_even_with_no_new_batch():
    """The gate must not early-return when only rollup retries are pending."""
    _reset()
    store: dict = {}

    async def _set(k, v, ex=None):
        store[k] = v

    async def _get(k):
        return store.get(k)

    async def _strict(k):
        return store.get(k)  # succeeds now

    ct._pending_rollup_records.append(_rec("z", 0.005))  # only a retry, no new batch
    month_key = ct.COST_MONTH_PREFIX + ct._current_month_key()
    with patch.object(ct.rs, "set_json", AsyncMock(side_effect=_set)), \
         patch.object(ct.rs, "get_json", AsyncMock(side_effect=_get)), \
         patch.object(ct.rs, "get_json_strict", AsyncMock(side_effect=_strict)):
        asyncio.run(ct._flush_cost_pending(force=True))
    assert not ct._pending_rollup_records
    assert store[month_key]["total_calls"] == 1
    _reset()
