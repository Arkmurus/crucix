"""R-F2408 — coalesce the whole-blob MASTERY_KEY save (state_store write-ceiling
relief), flag-gated + default OFF.

update_mastery() rewrites the ENTIRE mastery cache to ONE state_store key on every
learning observation. Under the accelerated free-learning loop that is a bursty
hot-key write on the single aiosqlite writer. R-F2408 defers the durability write
to at most once per interval when ARIA_MASTERY_COALESCE_SAVE is on, mirroring the
proven R-F2172 cost-coalescing pattern.

These capability tests drive the REAL update_mastery()/get_mastery_report() path
and count the ACTUAL persistence writes (by patching student._save_mastery), proving:
  1. flag OFF  -> byte-identical: one save per update (N updates == N saves)
  2. flag ON   -> coalesced: N rapid updates collapse to ~1 save this interval,
                  while the in-memory score still reflects EVERY update
  3. flush_mastery(force=True) persists the deferred write
  4. a report read opportunistically flushes the deferred write
"""
from __future__ import annotations

import pytest

from aria_service.intel import student as _st


async def _reset_mastery_state(monkeypatch, coalesce: bool):
    """Give each test a clean in-memory mastery cache with NO real store I/O:
    _save_mastery is replaced by a counter so we measure persistence attempts,
    never touching sqlite."""
    # Fresh, already-loaded cache so _load_mastery() is a no-op read.
    monkeypatch.setattr(_st, "_mastery_cache", {})
    monkeypatch.setattr(_st, "_mastery_dirty", False)
    monkeypatch.setattr(_st, "_mastery_last_save", 0.0)
    monkeypatch.setattr(_st, "_mastery_save_lock", None)
    monkeypatch.setattr(_st, "_MASTERY_SAVE_COALESCE", coalesce)
    monkeypatch.setattr(_st, "_MASTERY_FLUSH_INTERVAL_S", 1000.0)  # never elapses mid-test

    saves = {"n": 0}

    async def _fake_save():
        # mirror the real no-op-when-clean contract so counts are meaningful
        if not _st._mastery_dirty:
            return
        saves["n"] += 1
        _st._mastery_dirty = False

    monkeypatch.setattr(_st, "_save_mastery", _fake_save)
    # _load_mastery must return the cache untouched (no disk hydrate).
    async def _fake_load():
        return _st._mastery_cache
    monkeypatch.setattr(_st, "_load_mastery", _fake_load)
    return saves


@pytest.mark.asyncio
async def test_flag_off_saves_every_update(monkeypatch):
    saves = await _reset_mastery_state(monkeypatch, coalesce=False)
    for _ in range(5):
        await _st.update_mastery(["compliance"], correct=True, weight=1.0)
    # OFF path is inline: one persistence attempt per dirty update.
    assert saves["n"] == 5, f"flag OFF must save every update, got {saves['n']}"


@pytest.mark.asyncio
async def test_flag_on_coalesces_writes_but_score_updates(monkeypatch):
    saves = await _reset_mastery_state(monkeypatch, coalesce=True)
    for _ in range(5):
        await _st.update_mastery(["compliance"], correct=True, weight=1.0)
    # ON path: first update writes (last_save was 0), the rest coalesce within
    # the (huge) interval -> exactly ONE write for the burst.
    assert saves["n"] == 1, f"flag ON must coalesce the burst to 1 write, got {saves['n']}"
    # …but the in-memory EWMA reflects ALL 5 correct observations.
    m = _st._mastery_cache["compliance"]
    assert m["samples"] == 5
    assert m["score"] > _st.INITIAL_MASTERY
    # a write is still pending (dirty) — coalesced, not lost
    assert _st._mastery_dirty is True


@pytest.mark.asyncio
async def test_force_flush_persists_deferred_write(monkeypatch):
    saves = await _reset_mastery_state(monkeypatch, coalesce=True)
    await _st.update_mastery(["procurement"], correct=True)   # 1st write
    await _st.update_mastery(["procurement"], correct=True)   # coalesced (dirty)
    assert saves["n"] == 1 and _st._mastery_dirty is True
    wrote = await _st.flush_mastery()                          # force
    assert wrote is True
    assert saves["n"] == 2
    assert _st._mastery_dirty is False


@pytest.mark.asyncio
async def test_report_read_opportunistically_flushes(monkeypatch):
    saves = await _reset_mastery_state(monkeypatch, coalesce=True)
    await _st.update_mastery(["finance"], correct=True)       # 1st write
    await _st.update_mastery(["finance"], correct=True)       # coalesced
    assert saves["n"] == 1 and _st._mastery_dirty is True
    # A report refresh is time-gated by the interval; force elapse so the
    # opportunistic flush fires, proving the read path bounds the loss window.
    monkeypatch.setattr(_st, "_MASTERY_FLUSH_INTERVAL_S", 0.0)
    await _st.get_mastery_report()
    assert saves["n"] == 2, "report read should opportunistically persist the deferred write"
    assert _st._mastery_dirty is False
