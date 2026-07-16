"""R-F2680 — honesty_judge judge_failed gap must actually be recorded.

Bug: `_wire_judge_result` (sync, called from the async `judge_response`)
handled the `judge_failed` branch by calling `capability_gaps.record_gap(...)`
WITHOUT scheduling it — `record_gap` is a coroutine, so a bare call created an
orphan coroutine that was never awaited. The "honesty judge is broken" signal
was silently dropped (and Python emitted a RuntimeWarning), leaving the coder
blind to exactly the failure the gap exists to surface. The sibling
`honesty_judge_unsupported_claims` branch already wrapped its call in
`asyncio.ensure_future(...)`; this test asserts the judge_failed branch does too.

Capability test (§3c): drives the actual broken function `_wire_judge_result`
on a `judge_failed` result and asserts the gap LANDS in the ledger via the real
`record_gap` → `rs.lpush` sink. Fails before the fix (no gap written), passes
after.
"""
from __future__ import annotations

import asyncio
import uuid
from unittest.mock import patch


def _drive_wire(result: dict) -> list[dict]:
    """Run _wire_judge_result inside a loop, drain any scheduled tasks, and
    return the gaps the real record_gap path wrote."""
    from aria_service.intel import honesty_judge, capability_gaps

    store: list[str] = []          # newest-first, mirrors rs list semantics
    sentinels: dict[str, str] = {}

    async def fake_lpush(key, value, **kwargs):
        store.insert(0, value)

    async def fake_ltrim(key, start, stop):
        del store[stop + 1:]

    async def fake_lrange(key, start, stop):
        end = None if stop == -1 else stop + 1
        return store[start:end]

    async def fake_get(key):
        return sentinels.get(key)

    async def fake_set(key, value, ex=None, keepttl=False, **kwargs):
        sentinels[key] = value

    async def run():
        with patch("aria_service.intel.capability_gaps.rs.lpush", side_effect=fake_lpush), \
             patch("aria_service.intel.capability_gaps.rs.ltrim", side_effect=fake_ltrim), \
             patch("aria_service.intel.capability_gaps.rs.lrange", side_effect=fake_lrange), \
             patch("aria_service.intel.capability_gaps.rs.get", side_effect=fake_get), \
             patch("aria_service.intel.capability_gaps.rs.set", side_effect=fake_set):
            # The actual broken path: sync wiring fn scheduling an async record.
            honesty_judge._wire_judge_result(result)
            # Deterministically let any scheduled task complete (the fix uses
            # ensure_future; before the fix nothing was scheduled).
            pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
            if pending:
                await asyncio.gather(*pending)
            return await capability_gaps.get_gaps(resolved=False, limit=50)

    return asyncio.run(run())


def test_judge_failed_records_gap():
    unique_err = f"provider exploded {uuid.uuid4()}"  # unique → dodge dedupe
    result = {
        "status": "judge_failed",
        "error": unique_err,
        "claims": [],
        "verdicts": [],
    }

    gaps = _drive_wire(result)

    honesty_gaps = [g for g in gaps if g.get("type") == "honesty_judge_failure"]
    assert honesty_gaps, (
        "judge_failed must record a honesty_judge_failure gap; got "
        f"{[g.get('type') for g in gaps]}"
    )
    assert unique_err[:50] in honesty_gaps[0].get("detail", ""), (
        "recorded gap should carry the judge error detail"
    )
