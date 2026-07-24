"""R-F2963 (C0) — regional-mastery write coalescing.

_save_regional_mastery rewrote the whole 224-cell blob on EVERY observation; the
reading loop's ~15-cell burst hammered the single aiosqlite writer. This coalesces
the burst to at most one write per interval (mirroring R-F2408), with a force-flush
so nothing is stranded. Pre-req that makes the C3 seed-all flip safe.
"""
from __future__ import annotations

import asyncio
from unittest import mock

import pytest


@pytest.fixture(autouse=True)
def _restore_regional_globals():
    """These tests mutate student module globals directly; snapshot + restore them
    so they never pollute other tests (the coalesce flag / cache / cursor)."""
    from aria_service.intel import student
    saved = {k: getattr(student, k) for k in (
        "_REGIONAL_SAVE_COALESCE", "_REGIONAL_FLUSH_INTERVAL_S",
        "_regional_cache", "_regional_dirty", "_regional_last_save", "_regional_save_lock",
    )}
    try:
        yield
    finally:
        for k, v in saved.items():
            setattr(student, k, v)


def _reset_regional_state(student):
    # module-level globals persist across tests in-process — reset for determinism
    student._regional_cache = None
    student._regional_dirty = False
    student._regional_last_save = 0.0
    student._regional_save_lock = None


def test_rf2963_burst_coalesces_to_fewer_writes():
    """3 rapid updates within the flush interval → far fewer whole-blob writes
    than 3 (coalesced), and the cache still holds the latest scores."""
    from aria_service.intel import student

    writes: list = []

    async def fake_set_json(key, obj, ex=None, **kw):
        writes.append(key)

    async def fake_get_json_strict(key):
        return {}

    async def run():
        _reset_regional_state(student)
        # Force coalescing ON with a long interval so the burst defers.
        student._REGIONAL_SAVE_COALESCE = True
        student._REGIONAL_FLUSH_INTERVAL_S = 60.0
        with mock.patch.object(student.rs, "set_json", side_effect=fake_set_json), \
             mock.patch.object(student.rs, "get_json_strict", side_effect=fake_get_json_strict):
            for i in range(3):
                await student.update_regional_mastery(["procurement"], ["central_africa"], correct=True, weight=0.3)
            # After the burst: the first update writes (last_save was 0), the next
            # two coalesce → 1 write for 3 updates.
            n_after_burst = len(writes)
            # Force-flush persists the deferred updates.
            flushed = await student.flush_regional()
            return n_after_burst, len(writes), flushed, student._regional_cache

    n_burst, n_total, flushed, cache = asyncio.run(run())
    assert n_burst < 3, f"burst must coalesce, got {n_burst} writes for 3 updates"
    assert flushed is True, "force-flush must persist the deferred update"
    assert n_total == n_burst + 1, "force-flush writes exactly once more"
    assert cache["procurement:central_africa"]["samples"] == 3, "all 3 observations landed in the cache"


def test_rf2963_flag_off_is_inline_every_write():
    """With coalescing OFF, behaviour is the pre-R-F2963 inline write (every dirty
    update persists) — the safe fallback."""
    from aria_service.intel import student

    writes: list = []

    async def fake_set_json(key, obj, ex=None, **kw):
        writes.append(key)

    async def fake_get_json_strict(key):
        return {}

    async def run():
        _reset_regional_state(student)
        student._REGIONAL_SAVE_COALESCE = False
        with mock.patch.object(student.rs, "set_json", side_effect=fake_set_json), \
             mock.patch.object(student.rs, "get_json_strict", side_effect=fake_get_json_strict):
            for i in range(3):
                await student.update_regional_mastery(["legal"], ["europe"], correct=True, weight=0.3)
            return len(writes)

    assert asyncio.run(run()) == 3, "flag OFF must write inline on every dirty update"


def test_rf2963_flush_noop_when_nothing_pending():
    """flush_regional is a safe no-op when there's nothing dirty."""
    from aria_service.intel import student

    async def run():
        _reset_regional_state(student)
        student._REGIONAL_SAVE_COALESCE = True
        return await student.flush_regional()

    assert asyncio.run(run()) is False
