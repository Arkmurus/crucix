"""R-F2413 — hot/cold split Phase 0b: the correctness gaps that make the cutover
safe. Flag STILL default OFF (byte-identical); these prove the split is *safe to
flip* once backfilled.

Closes four gaps from the R-F2408 cutover plan:
  1. scan_keys / scan_json UNION the cold connection when the flag is on, so a
     scan over a cold prefix (aria:verified_facts:* etc.) still finds cold rows
     after cutover (was hot-only → would blind every verified_facts scan).
  2. delete() dual-deletes hot+cold when the flag is on, so a cold-prefixed
     reasoning_library case key actually deletes cross-file.
  3. router tightened: crucix:verified_intel:fact: (with colon) routes cold, the
     churny plural K/V list crucix:verified_intel:facts stays HOT.
  4. sweep_expired() also sweeps the cold store's expiring rows.

These capability tests drive the REAL state_store: set → scan → delete → get
round-trips per cold prefix with the flag ON (asserting physical file placement,
union-read, and cross-file delete), and the flag-OFF byte-identical path (no cold
file created).
"""
from __future__ import annotations

import os
import sqlite3
import tempfile

import pytest

from aria_service.intel import state_store as _ss


# ── 1. Router tighten (pure) ────────────────────────────────────────────────
class TestRouterTighten:
    @pytest.mark.parametrize("key,expected", [
        ("crucix:verified_intel:fact:99", "cold"),        # singular fact key
        ("crucix:verified_intel:facts", "hot"),           # plural shared LIST → hot
        ("crucix:verified_intel:facts:whatever", "hot"),  # anything under plural → hot
        ("crucix:audit:by_hash:deadbeef", "cold"),
        ("aria:verified_facts:GENERAL_CLAIM:x", "cold"),
        ("crucix:aria:reasoning_library:case42", "cold"),
        ("crucix:aria:cost:2026-07", "hot"),
        ("crucix:audit:by_entity:deadbeef", "hot"),       # LIST → hot (0b unchanged)
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


_COLD_ROUNDTRIP_KEYS = [
    "crucix:audit:by_hash:rt1",
    "aria:verified_facts:GENERAL_CLAIM:rt1",
    "crucix:verified_intel:fact:rt1",
    "crucix:aria:reasoning_library:rt1",
]


# ── 2. Flag ON: per-prefix set→scan→delete→get round-trip across both files ──
class TestFlagOnRoundTrip(_StoreFixture):
    @pytest.mark.parametrize("_store", [True], indirect=True)
    @pytest.mark.parametrize("key", _COLD_ROUNDTRIP_KEYS)
    @pytest.mark.asyncio
    async def test_cold_prefix_scan_and_delete_cross_file(self, _store, key):
        await _ss.set_key(key, "V")
        await _ss._flush_write_queue()
        await _ss._flush_cold_queue()

        # physically written to the COLD file, not the hot file
        cold_keys = [r[0] for r in sqlite3.connect(_store["cold"]).execute("SELECT key FROM state")]
        hot_keys = [r[0] for r in sqlite3.connect(_store["hot"]).execute("SELECT key FROM state")]
        assert key in cold_keys, f"{key} should be in the cold file"
        assert key not in hot_keys, f"{key} should NOT be in the hot file"

        # UNION scan finds the cold-file key (was the #1 cutover blocker)
        found = await _ss.scan_keys(key.rsplit(":", 1)[0] + ":*")
        assert key in found, f"union scan_keys must find cold key {key}"

        # get() routes cold and returns the value
        assert await _ss.get(key) == "V"

        # cross-file delete removes it from the cold file
        assert await _ss.delete(key) is True
        # get() caches every key for 5s (R-F2156, pre-existing, not invalidated
        # by delete) — clear it so we read the ACTUAL store state, not the cache.
        _ss._error_log_cache.clear()
        assert await _ss.get(key) is None
        cold_keys_after = [r[0] for r in sqlite3.connect(_store["cold"]).execute("SELECT key FROM state")]
        assert key not in cold_keys_after, f"delete must remove {key} from the cold file"
        found_after = await _ss.scan_keys(key.rsplit(":", 1)[0] + ":*")
        assert key not in found_after

    @pytest.mark.parametrize("_store", [True], indirect=True)
    @pytest.mark.asyncio
    async def test_scan_json_unions_cold_and_hot(self, _store):
        await _ss.set_json("aria:verified_facts:GENERAL_CLAIM:j1", {"n": 1})  # cold
        await _ss.set_json("aria:verified_facts:GENERAL_CLAIM:j2", {"n": 2})  # cold
        await _ss.set_json("crucix:aria:cost:hotj", {"n": 3})                 # hot
        await _ss._flush_write_queue()
        await _ss._flush_cold_queue()
        got = dict(await _ss.scan_json("aria:verified_facts:*"))
        assert got.get("aria:verified_facts:GENERAL_CLAIM:j1") == {"n": 1}
        assert got.get("aria:verified_facts:GENERAL_CLAIM:j2") == {"n": 2}
        # a hot scan is unaffected by the union
        hot = dict(await _ss.scan_json("crucix:aria:cost:*"))
        assert hot.get("crucix:aria:cost:hotj") == {"n": 3}

    @pytest.mark.parametrize("_store", [True], indirect=True)
    @pytest.mark.asyncio
    async def test_plural_facts_list_stays_hot(self, _store):
        # the churny plural list must NOT be dragged into cold
        await _ss.set_json("crucix:verified_intel:facts", [{"a": 1}])
        await _ss._flush_write_queue()
        await _ss._flush_cold_queue()
        assert await _ss.get_json("crucix:verified_intel:facts") == [{"a": 1}]
        hot_keys = [r[0] for r in sqlite3.connect(_store["hot"]).execute("SELECT key FROM state")]
        cold_keys = [r[0] for r in sqlite3.connect(_store["cold"]).execute("SELECT key FROM state")]
        assert "crucix:verified_intel:facts" in hot_keys
        assert "crucix:verified_intel:facts" not in cold_keys

    @pytest.mark.parametrize("_store", [True], indirect=True)
    @pytest.mark.asyncio
    async def test_cold_expiry_sweep(self, _store):
        await _ss.set_key("crucix:aria:reasoning_library:exp1", "V", ex=3600)
        await _ss._flush_cold_queue()
        # backdate the cold row's expiry into the past, then sweep
        await _ss._cold_conn.execute(
            "UPDATE state SET expires_at = ? WHERE key = ?",
            (_ss._now() - 10, "crucix:aria:reasoning_library:exp1"))
        await _ss._cold_conn.commit()
        swept = await _ss.sweep_expired()
        assert swept >= 1
        cold_keys = [r[0] for r in sqlite3.connect(_store["cold"]).execute("SELECT key FROM state")]
        assert "crucix:aria:reasoning_library:exp1" not in cold_keys


# ── 3. Flag OFF: byte-identical single-file behaviour ───────────────────────
class TestFlagOffInert(_StoreFixture):
    @pytest.mark.parametrize("_store", [False], indirect=True)
    @pytest.mark.asyncio
    async def test_cold_prefix_stays_hot_scan_delete_no_cold_file(self, _store):
        key = "aria:verified_facts:GENERAL_CLAIM:off1"
        await _ss.set_key(key, "V")
        await _ss._flush_write_queue()
        # no cold file, key resolves from the single hot file
        assert not os.path.exists(_store["cold"]), "flag OFF must never create the cold DB"
        assert await _ss.get(key) == "V"
        assert key in await _ss.scan_keys("aria:verified_facts:*")
        assert await _ss.delete(key) is True
        _ss._error_log_cache.clear()  # R-F2156 get-cache (see round-trip test)
        assert await _ss.get(key) is None
        assert not os.path.exists(_store["cold"])
