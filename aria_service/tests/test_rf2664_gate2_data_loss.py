"""R-F2664 — gate #2 regional-mastery DATA-LOSS guard.

Bug: `student._load_regional_mastery` used non-strict `rs.get_json`, which swallows a
store-not-ready `StoreReadError` to `None`. On a slow-boot deploy that made
`_regional_cache` poison to `{}` for the whole process, and the next
`update_regional_mastery` persisted that `{}`+1-cell over the durable key —
CLOBBERING every prior cell (silent gate-#2 heatmap wipe). The R-F268 dirty-guard
could not catch it because a real update sets dirty=True.

Fix: strict read distinguishes "store not ready" (transient → return {} but do NOT
cache; the write path then SKIPS so nothing clobbers) from "genuinely absent"
(→ empty scaffold, update proceeds normally). These drive the REAL write path and
assert no clobber. Verified to FAIL against the pre-R-F2664 tree.
"""
from __future__ import annotations

import pytest

from aria_service.intel import student
from aria_service.intel import redis_store as rs


@pytest.fixture(autouse=True)
def _fresh_cache(monkeypatch):
    # Simulate a fresh process (cache uninitialised), like just after a deploy.
    monkeypatch.setattr(student, "_regional_cache", None, raising=False)
    monkeypatch.setattr(student, "_regional_dirty", False, raising=False)
    yield


@pytest.mark.asyncio
async def test_store_not_ready_does_not_clobber(monkeypatch):
    """THE fix: a store-not-ready load must NOT poison the cache to {} and must NOT
    let an update persist an empty map over the durable key."""
    async def _boom(key):
        raise rs.StoreReadError("state_store: connection wedged during boot")
    monkeypatch.setattr(rs, "get_json_strict", _boom)

    writes: list = []
    async def _spy_set(key, val, *a, **k):
        writes.append(val)
    monkeypatch.setattr(rs, "set_json", _spy_set)

    await student.update_regional_mastery(["compliance"], ["west_africa"], correct=True)

    assert writes == [], f"a DEFERRED load must NOT persist (would clobber); wrote {writes}"
    assert student._regional_cache is None, (
        "cache must stay uninitialised on store-not-ready so the next warm call retries")


@pytest.mark.asyncio
async def test_genuinely_absent_key_still_updates(monkeypatch):
    """A genuinely absent key (strict read returns None) → empty scaffold, and a real
    update proceeds + persists (we must not over-correct into never writing)."""
    async def _absent(key):
        return None
    monkeypatch.setattr(rs, "get_json_strict", _absent)

    writes: list = []
    async def _spy_set(key, val, *a, **k):
        writes.append(val)
    monkeypatch.setattr(rs, "set_json", _spy_set)

    await student.update_regional_mastery(["compliance"], ["west_africa"], correct=True)

    assert writes, "a genuine update on an absent key must persist"
    assert "compliance:west_africa" in writes[-1], writes


@pytest.mark.asyncio
async def test_warm_update_preserves_prior_cells(monkeypatch):
    """Normal path: a warm load reads the real data and an update APPENDS to it —
    prior cells are preserved, never overwritten."""
    prior = {
        "compliance:west_africa": {"score": 0.62, "samples": 8},
        "procurement:gulf": {"score": 0.71, "samples": 12},
    }
    async def _warm(key):
        return dict(prior)
    monkeypatch.setattr(rs, "get_json_strict", _warm)

    writes: list = []
    async def _spy_set(key, val, *a, **k):
        writes.append(val)
    monkeypatch.setattr(rs, "set_json", _spy_set)

    await student.update_regional_mastery(["technical"], ["gulf"], correct=True)

    saved = writes[-1]
    assert "compliance:west_africa" in saved and "procurement:gulf" in saved, (
        f"prior cells were lost — clobber! saved={list(saved)}")
    assert "technical:gulf" in saved, "the new cell was not added"


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
