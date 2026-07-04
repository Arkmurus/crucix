"""R-F2415 — hot→cold state_store PHASE 1 backfill (COPY ONLY; flag stays OFF).

Copies every cold-prefix row from the HOT db to the COLD db, idempotently,
resumably, rate-limited, WITHOUT deleting from hot and WITHOUT flipping
ARIA_STATE_HOTCOLD_SPLIT (live routing unchanged). These capability tests drive
the REAL state_store.backfill_cold / reconcile_cold against temp hot+cold files:

  1. copies cold-prefix keys hot→cold (values equal), leaves hot untouched
  2. does NOT copy non-cold-prefix keys (hot stays hot)
  3. idempotent: a completed re-run copies 0 new rows
  4. resumable: interrupt at a checkpoint (max_pages) → resume copies the rest
  5. reconcile returns ok=True (per-prefix counts match + read-equal spot-check)
  6. flag OFF throughout: live get/set stay hot-only (backfill never changes routing)
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
    "crucix:aria:reasoning_library:k4": "a4",
}
_HOT_SEED = {
    "crucix:aria:cost:h1": "c1",
    "crucix:verified_intel:facts": "plural-stays-hot",  # plural list → hot
}


def _cold_rows(path):
    if not os.path.exists(path):
        return {}
    return {k: v for k, v in sqlite3.connect(path).execute("SELECT key, value FROM state")}


def _hot_rows(path):
    return {k: v for k, v in sqlite3.connect(path).execute("SELECT key, value FROM state")}


class _BackfillFixture:
    @pytest.fixture
    async def store(self, monkeypatch):
        d = tempfile.mkdtemp()
        hot = os.path.join(d, "aria_state.db")
        cold = os.path.join(d, "aria_knowledge_store.db")
        monkeypatch.setenv("ARIA_STATE_DB_PATH", hot)
        monkeypatch.setattr(_ss, "_HOTCOLD_SPLIT", False)   # live routing OFF throughout
        # reset backfill module state so tests don't bleed into each other
        monkeypatch.setattr(_ss, "_backfill_lock", None)
        monkeypatch.setattr(_ss, "_backfill_state", {
            "running": False, "done": False, "copied": 0, "copied_this_run": 0,
            "scanned": 0, "last_rowid": 0, "started_at": None,
            "updated_at": None, "error": None})
        if _ss._conn is not None:
            await _ss.close()
        await _ss.connect()
        # seed HOT (flag OFF → every write lands in the hot db)
        for k, v in {**_COLD_SEED, **_HOT_SEED}.items():
            await _ss.set_key(k, v)
        await _ss._flush_write_queue()
        yield {"dir": d, "hot": hot, "cold": cold}
        try:
            await _ss.close()
        except Exception:
            pass


class TestBackfill(_BackfillFixture):
    @pytest.mark.asyncio
    async def test_copies_cold_only_and_leaves_hot_untouched(self, store):
        hot_before = _hot_rows(store["hot"])
        res = await _ss.backfill_cold(page_size=2, sleep_s=0.0)  # small page → exercise paging
        assert res["done"] is True
        assert res["copied_this_run"] == len(_COLD_SEED)
        # cold file has exactly the cold-prefix keys, with equal values
        cold = _cold_rows(store["cold"])
        for k, v in _COLD_SEED.items():
            assert cold.get(k) == v, f"{k} should be copied to cold"
        # non-cold-prefix keys NOT copied (incl. the plural facts list)
        for k in _HOT_SEED:
            assert k not in cold, f"{k} must NOT be in cold"
        # HOT is byte-for-byte unchanged (copy-only, reversible)
        assert _hot_rows(store["hot"]) == hot_before

    @pytest.mark.asyncio
    async def test_ensure_cold_open_opens_destination_with_flag_off(self, store):
        """R-F2415: ensure_cold_open() opens the cold store as a backfill destination
        EVEN with _HOTCOLD_SPLIT OFF (live routing stays hot) — and is idempotent."""
        assert _ss._HOTCOLD_SPLIT is False           # live routing off throughout
        ok = await _ss.ensure_cold_open()
        assert ok is True                            # cold write conn available
        assert _ss._cold_conn is not None            # destination handle exists
        assert await _ss.ensure_cold_open() is True  # idempotent second call

    @pytest.mark.asyncio
    async def test_idempotent_rerun_copies_zero(self, store):
        first = await _ss.backfill_cold(page_size=100, sleep_s=0.0)
        assert first["done"] is True and first["copied_this_run"] == len(_COLD_SEED)
        cold_after_first = _cold_rows(store["cold"])
        await _ss._flush_write_queue()
        second = await _ss.backfill_cold(page_size=100, sleep_s=0.0)  # resume from checkpoint
        assert second["done"] is True
        assert second["copied_this_run"] == 0, "re-run must copy no new rows"
        assert _cold_rows(store["cold"]) == cold_after_first

    @pytest.mark.asyncio
    async def test_resumable_from_checkpoint(self, store):
        # interrupt after 1 page (reset the cursor first for a clean start)
        partial = await _ss.backfill_cold(page_size=2, sleep_s=0.0, max_pages=1, reset=True)
        assert partial["done"] is False
        assert partial["last_rowid"] > 0
        await _ss._flush_write_queue()
        # resume → copies the remaining cold rows, finishes
        rest = await _ss.backfill_cold(page_size=2, sleep_s=0.0)
        assert rest["done"] is True
        cold = _cold_rows(store["cold"])
        for k, v in _COLD_SEED.items():
            assert cold.get(k) == v, f"{k} must be present after resume"

    @pytest.mark.asyncio
    async def test_reconcile_ok_after_full_backfill(self, store):
        await _ss.backfill_cold(page_size=100, sleep_s=0.0)
        await _ss._flush_write_queue()
        rec = await _ss.reconcile_cold(sample_n=10)
        assert rec["ok"] is True, rec
        # every cold prefix reconciles hot==cold
        for p, info in rec["prefixes"].items():
            assert info["hot"] == info["cold"], (p, info)
        assert rec["spot_check"]["mismatch_count"] == 0

    @pytest.mark.asyncio
    async def test_reconcile_detects_missing(self, store):
        # partial copy only → reconcile must NOT say ok (cold missing rows)
        await _ss.backfill_cold(page_size=1, sleep_s=0.0, max_pages=1, reset=True)
        await _ss._flush_write_queue()
        rec = await _ss.reconcile_cold(sample_n=10)
        assert rec["ok"] is False, "reconcile must fail when cold is incomplete"

    @pytest.mark.asyncio
    async def test_flag_off_live_routing_unchanged(self, store):
        await _ss.backfill_cold(page_size=100, sleep_s=0.0)
        await _ss._flush_write_queue()
        # flag is OFF → a live read of a cold-prefix key resolves from HOT
        assert await _ss.get("crucix:audit:by_hash:k1") == "a1"
        # a NEW cold-prefix write with the flag OFF must land in HOT, not cold
        await _ss.set_key("crucix:audit:by_hash:new1", "n1")
        await _ss._flush_write_queue()
        assert "crucix:audit:by_hash:new1" in _hot_rows(store["hot"])
        assert "crucix:audit:by_hash:new1" not in _cold_rows(store["cold"])
        # and the split is still reported OFF
        st = await _ss.backfill_status()
        assert st["hotcold_split_live"] is False
