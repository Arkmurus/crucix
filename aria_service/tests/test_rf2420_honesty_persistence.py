"""R-F2420 — honesty index persistence.

Measured live 2026-07-04: verification climbed to rate_sample 9 and HELD, but
honesty stayed at scored_n 2-3 and never reached the 5-sample floor — even though
the SAME status-ok judgments backed both signals. Root cause (aria.py): the
native-grounding VERIFICATION is recorded SYNCHRONOUSLY in the main handler
(awaited), while the honesty JUDGMENT was recorded ONLY in the fire-and-forget
background _judge_bg task (create_task, no strong ref), which loses writes when
the event loop is saturated. Fix: record honesty on the same reliable SYNC path
(the repair already computed the judgment), and skip the background task.

This capability test proves the STORAGE side that the root-cause fix depends on:
N status-ok judgments recorded → get_honesty_stats reflects N (NOT trimmed to a
few), ruling out a size-cap / eviction cause. The sync-recording wiring is the
code fix; this proves the index itself accumulates.
"""
from __future__ import annotations

import asyncio

from aria_service.intel import honesty_judge as hj
from aria_service.intel import redis_store as rs


def _inmemory_store(monkeypatch):
    store: dict = {}

    async def fake_get(k):
        return store.get(k)

    async def fake_set(k, v, ex=None):
        store[k] = v

    monkeypatch.setattr(rs, "get_json", fake_get)
    monkeypatch.setattr(rs, "set_json", fake_set)
    # R-F3717 — the strict reader must be backed by the SAME dict. In production
    # get_json and get_json_strict read one store and differ only in how they
    # report a FAILURE; a fake that emulates just the lenient one sends the
    # read-before-write path to the real (empty) store, so the index reads absent
    # every time and this test measures the fake, not the code.
    monkeypatch.setattr(rs, "get_json_strict", fake_get)
    return store


def test_honesty_index_accumulates_N_status_ok_not_trimmed(monkeypatch):
    _inmemory_store(monkeypatch)

    async def main():
        for i in range(8):
            j = {"status": "ok", "claims": ["a fact"], "supported_count": 1,
                 "honesty_score": 1.0, "verdicts": []}
            await hj.record_judgment(
                j, trace_id="", session_id="s",
                question_preview=f"q{i}", response_preview="r",
            )
        return await hj.get_honesty_stats()

    stats = asyncio.run(main())
    # All 8 land in the index AND all 8 count as scored (status ok, numeric score,
    # within the 24h window) — NOT trimmed to a handful.
    assert stats["total"] == 8, f"index trimmed to {stats['total']}, expected 8"
    assert stats["scored_sample_size"] == 8, (
        f"scored_sample_size {stats['scored_sample_size']} != 8 — honesty can't "
        f"reach the 5-sample floor")
    assert stats["scored_sample_size"] >= 5   # crosses the composite floor


def test_scored_sample_size_counts_only_status_ok(monkeypatch):
    """Honesty scored_n counts status-ok scored judgments; no_source/no_claims are
    in the index total but not scored — documents the real semantics so a low
    scored_n from non-ok judgments isn't mistaken for a persistence wipe."""
    _inmemory_store(monkeypatch)

    async def main():
        oks = [{"status": "ok", "claims": ["x"], "supported_count": 1,
                "honesty_score": 1.0, "verdicts": []} for _ in range(5)]
        non = [{"status": "no_source", "claims": ["x"], "supported_count": 0,
                "honesty_score": 0.0, "verdicts": []} for _ in range(3)]
        for j in oks + non:
            await hj.record_judgment(j, trace_id="", session_id="s",
                                     question_preview="q", response_preview="r")
        return await hj.get_honesty_stats()

    stats = asyncio.run(main())
    assert stats["total"] == 8            # all persisted in the index
    assert stats["scored_sample_size"] == 5   # only the 5 status-ok are scored
