"""R-F2504 — hot/cold PHASE 3 reclaim: after backfill_cold copies cold-prefix rows
HOT→COLD, reclaim_hot() DELETEs them from the hot db (+ VACUUM) to shrink it. Safety
that these tests pin:
  1. refuses when the split flag is OFF (never mutate with wrong routing)
  2. dry_run counts what WOULD delete but mutates NOTHING
  3. real run deletes cold-prefix keys FROM HOT that are confirmed in COLD
  4. NEVER deletes a cold-prefix key that is NOT in cold (data-safety) — nor non-cold keys
"""
from __future__ import annotations

import os
import sqlite3
import tempfile

import pytest

from aria_service.intel import state_store as _ss

_COLD_SEED = {
    "crucix:audit:by_hash:k1": "a1",
    "aria:verified_facts:GENERAL_CLAIM:k2": "a2",
    "crucix:verified_intel:fact:k3": "a3",
}
_HOT_SEED = {"crucix:aria:cost:h1": "c1"}
_ORPHAN = "crucix:audit:by_hash:orphan"   # cold-prefix, in HOT only (never backfilled)


def _rows(path):
    if not os.path.exists(path):
        return {}
    return {k: v for k, v in sqlite3.connect(path).execute("SELECT key, value FROM state")}


class _Fix:
    @pytest.fixture
    async def store(self, monkeypatch):
        d = tempfile.mkdtemp()
        hot = os.path.join(d, "aria_state.db")
        monkeypatch.setenv("ARIA_STATE_DB_PATH", hot)
        monkeypatch.setattr(_ss, "_HOTCOLD_SPLIT", False)
        monkeypatch.setattr(_ss, "_backfill_lock", None)
        monkeypatch.setattr(_ss, "_reclaim_running", False)
        monkeypatch.setattr(_ss, "_backfill_state", {
            "running": False, "done": False, "copied": 0, "copied_this_run": 0,
            "scanned": 0, "last_rowid": 0, "started_at": None, "updated_at": None, "error": None})
        if _ss._conn is not None:
            await _ss.close()
        await _ss.connect()
        for k, v in {**_COLD_SEED, **_HOT_SEED}.items():
            await _ss.set_key(k, v)
        await _ss._flush_write_queue()
        await _ss.backfill_cold(page_size=100, sleep_s=0.0)   # cold gets _COLD_SEED
        await _ss.set_key(_ORPHAN, "orphan")                  # flag OFF → lands in HOT only
        await _ss._flush_write_queue()
        yield {"dir": d, "hot": hot, "cold": os.path.join(d, "aria_knowledge_store.db")}
        try:
            await _ss.close()
        except Exception:
            pass


class TestReclaim(_Fix):
    @pytest.mark.asyncio
    async def test_refuses_when_flag_off(self, store):
        assert _ss._HOTCOLD_SPLIT is False
        res = await _ss.reclaim_hot(dry_run=False, do_vacuum=False)
        assert "error" in res and "flag OFF" in res["error"]
        # hot untouched
        assert set(_COLD_SEED) <= set(_rows(store["hot"]))

    @pytest.mark.asyncio
    async def test_dry_run_counts_but_mutates_nothing(self, store, monkeypatch):
        monkeypatch.setattr(_ss, "_HOTCOLD_SPLIT", True)
        hot_before = _rows(store["hot"])
        res = await _ss.reclaim_hot(dry_run=True, do_vacuum=False)
        assert res["deleted"] == 0
        assert res["would_delete"] == len(_COLD_SEED), res   # the 3 backfilled keys
        assert _rows(store["hot"]) == hot_before             # nothing mutated

    @pytest.mark.asyncio
    async def test_real_reclaim_deletes_only_backfilled_cold_keys(self, store, monkeypatch):
        monkeypatch.setattr(_ss, "_HOTCOLD_SPLIT", True)
        res = await _ss.reclaim_hot(dry_run=False, do_vacuum=False)
        assert res["deleted"] == len(_COLD_SEED), res
        hot = _rows(store["hot"])
        # backfilled cold-prefix keys are GONE from hot
        for k in _COLD_SEED:
            assert k not in hot, f"{k} should be reclaimed from hot"
        # …but still safe in COLD
        cold = _rows(store["cold"])
        for k, v in _COLD_SEED.items():
            assert cold.get(k) == v
        # DATA-SAFETY: the orphan (cold-prefix but NOT in cold) survives in hot
        assert hot.get(_ORPHAN) == "orphan", "must NOT delete a key absent from cold"
        # non-cold keys untouched
        assert hot.get("crucix:aria:cost:h1") == "c1"
