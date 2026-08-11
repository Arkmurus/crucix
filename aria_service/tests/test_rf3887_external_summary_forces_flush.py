"""R-F3887 — a READ was gated by a WRITE-coalescing interval, and reported empty.

`get_external_summary` has always carried the comment "surface pending coalesced
records" above a bare `await _flush_external_pending()`. That call returns
immediately while `(now - _ext_last_flush) < _COST_FLUSH_INTERVAL_S` (~15s), so the
summary could report `by_service: {}` with records sitting in the in-process buffer.
Those records are in-memory and lost on restart, so a low-traffic service could go
unreported indefinitely while the endpoint insisted nothing had been spent.

HOW MUCH THIS COSTS IN PRACTICE, measured on myself the same hour: after wiring
Brave into the cost tracker (R-F3884) I probed `/api/aria/cost/external`, read
`by_service: {}`, and twice concluded the wiring had failed. It had not — the first
probe was inside the coalescing window, and the second was a store-less local
process (§17: `state_store: no connection`, which the R-F1 None-on-error contract
renders as a clean zero). Waiting 25 seconds showed
`{"brave": {"calls": 2, "cost_usd": 0.01}}`.

So this is the same family as everything the C-23/C-25 entries record: **the
instrument was wrong, not the subject** — and an empty reading that means "not
flushed yet" is indistinguishable from one that means "nothing was spent".

Forcing is safe: this is a read-path flush of at most one interval's records, not the
hot write path R-F2483 exists to coalesce. R-F2483's own docstring notes external
cost is "observability + the composite month total, NOT the $300 LLM cap
(assert_monthly_cap reserves atomically)", so nothing depends on the read being lazy.
"""
from __future__ import annotations

import pytest

from aria_service.intel import cost_tracker as ct


def test_the_summary_forces_the_flush():
    """Pinned at the call site: without `force=True` the read inherits the write
    path's time gate, and the comment above it becomes false."""
    from aria_service.tests._source_probe import function_source

    src = function_source(ct, "get_external_summary")
    assert "_flush_external_pending(force=True)" in src, (
        "get_external_summary must FORCE the flush — a bare call is time-gated by "
        "_COST_FLUSH_INTERVAL_S, so the endpoint reports {} while records are "
        "buffered (R-F3887)")


@pytest.mark.asyncio
async def test_a_record_written_now_is_visible_now(monkeypatch):
    """CAPABILITY TEST — the user-visible symptom. Record a call, read the summary
    immediately, and see it. Before the fix this returned an empty dict for ~15s."""
    store: dict = {}

    async def _get_json(k): return store.get(k)
    async def _set_json(k, v, ex=None, **kw): store[k] = v

    monkeypatch.setattr(ct.rs, "get_json", _get_json)
    monkeypatch.setattr(ct.rs, "set_json", _set_json)
    # Pretend a flush just happened, so the time gate WOULD suppress an unforced one.
    monkeypatch.setattr(ct, "_ext_last_flush", __import__("time").time())
    monkeypatch.setattr(ct, "_pending_external_records", [])

    await ct.record_brave_call(operation="search", success=True)
    summary = await ct.get_external_summary()

    assert summary["by_service"].get("brave"), (
        "a Brave call recorded moments ago must appear in the summary; an empty "
        "by_service is indistinguishable from 'nothing was spent'")
    assert summary["total_calls"] >= 1


@pytest.mark.asyncio
async def test_the_hot_write_path_stays_coalesced(monkeypatch):
    """The converse control (R-F3858). R-F2483 exists because ~7 store ops per
    external call amplified writes during a DD's search fan-out. Forcing the READ
    must not force the WRITE path — recording must still return without flushing."""
    import time as _t
    monkeypatch.setattr(ct.rs, "get_json", lambda k: _none())
    monkeypatch.setattr(ct.rs, "set_json", lambda k, v, ex=None, **kw: _none())
    monkeypatch.setattr(ct, "_ext_last_flush", _t.time())
    monkeypatch.setattr(ct, "_pending_external_records", [])

    wrote = await ct._flush_external_pending()          # no force
    assert wrote is False, (
        "the unforced flush must remain time-gated, or R-F2483's write coalescing "
        "is undone and every external call pays ~7 store ops again")


async def _none():
    return None
