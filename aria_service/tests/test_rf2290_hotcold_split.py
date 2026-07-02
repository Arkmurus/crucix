"""R-F2290 — state_store hot/cold DB split, Phase 0 (flag-gated K/V routing).

The 907 MB single-writer ceiling: hot operational writes share one file+thread
with ~450k cold permanent rows (audit chains, verified facts/intel). Phase 0 adds
a router + a second cold DB file (own conn/queue/worker) and routes the K/V path
by key, GATED behind ARIA_STATE_HOTCOLD_SPLIT (default OFF).

These capability tests drive the REAL store: the pure router, the flag-OFF
byte-identical behaviour (no cold file, cold prefix stays in the hot DB), and the
flag-ON physical split (cold prefix lands in the cold file, hot prefix in the hot
file, both round-trip through get()).
"""
from __future__ import annotations

import os
import sqlite3
import tempfile

import pytest

from aria_service.intel import state_store as _ss


class TestRouterPure:
    @pytest.mark.parametrize("key,expected", [
        ("crucix:audit:by_hash:abc", "cold"),
        ("aria:verified_facts:GENERAL_CLAIM:x", "cold"),
        ("crucix:verified_intel:fact:99", "cold"),
        ("crucix:aria:reasoning_library:7", "cold"),
        ("crucix:aria:cost:2026-07", "hot"),
        ("crucix:audit:by_entity:deadbeef", "hot"),   # LIST → hot in Phase 0 (0b routes it)
        ("crucix:health:check", "hot"),
        ("", "hot"),
    ])
    def test_route(self, key, expected):
        assert _ss._route_db(key) == expected


class _StoreFixture:
    @pytest.fixture
    async def _store(self, monkeypatch, request):
        split = request.param
        d = tempfile.mkdtemp()
        hot = os.path.join(d, "aria_state.db")
        cold = os.path.join(d, "aria_knowledge_store.db")
        monkeypatch.setenv("ARIA_STATE_DB_PATH", hot)
        monkeypatch.setattr(_ss, "_HOTCOLD_SPLIT", split)
        if _ss._conn is not None:
            await _ss.close()
        await _ss.connect()
        yield {"dir": d, "hot": hot, "cold": cold, "split": split}
        try:
            await _ss.close()
        except Exception:
            pass


class TestFlagOffIsInert(_StoreFixture):
    @pytest.mark.parametrize("_store", [False], indirect=True)
    @pytest.mark.asyncio
    async def test_cold_prefix_stays_hot_no_cold_file(self, _store):
        await _ss.set_key("crucix:audit:by_hash:t1", "V1")
        await _ss._flush_write_queue()
        assert await _ss.get("crucix:audit:by_hash:t1") == "V1"
        # the split is OFF → the cold file must NOT exist
        assert not os.path.exists(_store["cold"]), "flag OFF must never create the cold DB"
        # …and the row is in the HOT db
        rows = [r[0] for r in sqlite3.connect(_store["hot"]).execute("SELECT key FROM state")]
        assert "crucix:audit:by_hash:t1" in rows


class TestFlagOnSplits(_StoreFixture):
    @pytest.mark.parametrize("_store", [True], indirect=True)
    @pytest.mark.asyncio
    async def test_cold_key_lands_in_cold_file_hot_in_hot(self, _store):
        await _ss.set_key("crucix:audit:by_hash:c1", "COLD1")
        await _ss.set_key("crucix:aria:cost:h1", "HOT1")
        await _ss._flush_write_queue()
        await _ss._flush_cold_queue()
        # both round-trip through the public get()
        assert await _ss.get("crucix:audit:by_hash:c1") == "COLD1"
        assert await _ss.get("crucix:aria:cost:h1") == "HOT1"
        assert os.path.exists(_store["cold"]), "flag ON must open the cold DB"
        cold_keys = [r[0] for r in sqlite3.connect(_store["cold"]).execute("SELECT key FROM state")]
        hot_keys = [r[0] for r in sqlite3.connect(_store["hot"]).execute("SELECT key FROM state")]
        # cold key physically in the COLD file, NOT the hot file
        assert "crucix:audit:by_hash:c1" in cold_keys
        assert "crucix:audit:by_hash:c1" not in hot_keys
        # hot key physically in the HOT file, NOT the cold file
        assert "crucix:aria:cost:h1" in hot_keys
        assert "crucix:aria:cost:h1" not in cold_keys

    @pytest.mark.parametrize("_store", [True], indirect=True)
    @pytest.mark.asyncio
    async def test_set_json_cold_roundtrips(self, _store):
        await _ss.set_json("crucix:verified_intel:fact:j1", {"claim": "x", "n": 3})
        await _ss._flush_cold_queue()
        got = await _ss.get_json("crucix:verified_intel:fact:j1")
        assert got == {"claim": "x", "n": 3}
        cold_keys = [r[0] for r in sqlite3.connect(_store["cold"]).execute("SELECT key FROM state")]
        assert "crucix:verified_intel:fact:j1" in cold_keys
