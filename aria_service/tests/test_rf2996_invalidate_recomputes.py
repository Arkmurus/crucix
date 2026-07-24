"""R-F2996 — invalidate_heatmap_cache must force a RECOMPUTE, not re-serve stale disk.

Live bug (2026-07-24): the coverage dashboard stayed pinned on a pre-deploy snapshot
across TWO reboots — R-F2987's honest freshness fields never surfaced. Root cause:
invalidate_heatmap_cache() cleared `_HEATMAP_DISK_SEEDED` (the one-shot cold-start
disk-seed guard, R-F931), so each invalidation (continuous_update fires periodically)
RE-ARMED the seed and the next read RE-SERVED the stale on-disk matrix instead of
recomputing. This drives the real build_heatmap() through an invalidate and asserts
the second read reflects a fresh recompute — the user-visible symptom, not a helper.
"""
from __future__ import annotations

import asyncio
from unittest.mock import patch

from aria_service.intel import coverage_heatmap as ch


def _reset():
    ch._HEATMAP_CACHE.clear()
    ch._HEATMAP_INFLIGHT.clear()
    ch._HEATMAP_DISK_SEEDED.clear()


def test_rf2996_invalidate_forces_recompute_not_stale_disk_reseed():
    _reset()
    STALE = {"matrix": {"d": {"j": {}}}, "summary": {"tag": "STALE_DISK"}}
    FRESH = {"matrix": {"d": {"j": {}}}, "summary": {"tag": "FRESH_RECOMPUTE"}}

    async def _fake_uncached(**_kw):
        return FRESH

    with patch.object(ch, "_HEATMAP_DISK_PATH", "/tmp/does-not-matter.json"), \
         patch.object(ch, "_HEATMAP_TTL_S", 120.0), \
         patch.object(ch, "_load_disk_cache", return_value=STALE), \
         patch.object(ch, "_build_heatmap_uncached", side_effect=_fake_uncached):

        async def _run():
            # 1) cold-start: first read serves the one-shot disk seed (stale)
            first = await ch.build_heatmap()
            # 2) data changed → invalidate
            ch.invalidate_heatmap_cache()
            # 3) next read MUST recompute fresh, NOT re-serve the stale disk seed
            second = await ch.build_heatmap()
            return first, second

        first, second = asyncio.run(_run())

    assert first["summary"]["tag"] == "STALE_DISK", "cold-start should serve the disk seed once (R-F931)"
    assert second["summary"]["tag"] == "FRESH_RECOMPUTE", \
        "after invalidate, the read must RECOMPUTE — not re-serve the stale disk seed (the R-F2996 bug)"
    # the seed guard stays consumed for the process (one-shot), so it can't re-serve disk
    assert (None, None) in ch._HEATMAP_DISK_SEEDED
    _reset()


def test_rf2996_invalidate_still_clears_memory_and_inflight():
    """Regression guard: the parts invalidate SHOULD clear still clear."""
    _reset()
    ch._HEATMAP_CACHE[(None, None)] = (1.0, {"x": 1})
    ch.invalidate_heatmap_cache()
    assert not ch._HEATMAP_CACHE, "result cache must still be cleared"
    assert not ch._HEATMAP_INFLIGHT, "inflight map must still be cleared"
    _reset()
