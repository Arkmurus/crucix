"""R-F1871 — state_store.scan_keys must filter in SQL by the literal prefix, not
fetch the entire keyspace and fnmatch every row in Python.

Wedge DD 2026-06-24: the event loop stalled 5-215s because scan_keys ran
`SELECT key FROM state` (no filter) + a Python fnmatch over EVERY row — O(all
keys) — and absorption_quarantine.stats() calls it on every /api/aria/health hit
(the health endpoint wedging itself; web_integrity escalated /api/aria/health
timing out >215s to CRITICAL). Fix: push the literal prefix into
`key GLOB 'prefix*'` (PK range scan) and keep fnmatch for exact Redis-glob
semantics over the narrowed result.

Capability test: drive the real state_store.scan_keys against a temp DB and
assert prefix narrowing + glob/exact/fallback semantics are all correct (the
behavior the wedge fix must preserve).
"""
from __future__ import annotations

import asyncio

from aria_service.intel import state_store


async def _setup(db) -> None:
    await state_store.connect(str(db))
    await state_store.set_json("p:a:1", {"v": 1})
    await state_store.set_json("p:a:2", {"v": 2})
    await state_store.set_json("p:b:1", {"v": 3})
    await state_store.set_json("other:x", {"v": 4})


def test_prefix_scan_returns_only_matching_prefix(tmp_path):
    async def run():
        await _setup(tmp_path / "s.db")
        assert set(await state_store.scan_keys("p:a:*")) == {"p:a:1", "p:a:2"}
        assert set(await state_store.scan_keys("p:*")) == {"p:a:1", "p:a:2", "p:b:1"}
        assert set(await state_store.scan_keys("other:*")) == {"other:x"}
    asyncio.run(run())


def test_no_match_prefix_returns_empty(tmp_path):
    async def run():
        await _setup(tmp_path / "s.db")
        assert await state_store.scan_keys("zzz:*") == []
    asyncio.run(run())


def test_single_char_and_charclass_glob(tmp_path):
    async def run():
        await _setup(tmp_path / "s.db")
        # '?' single-char + the fnmatch over the SQL-narrowed result
        assert set(await state_store.scan_keys("p:a:?")) == {"p:a:1", "p:a:2"}
    asyncio.run(run())


def test_exact_key_pattern(tmp_path):
    async def run():
        await _setup(tmp_path / "s.db")
        assert set(await state_store.scan_keys("other:x")) == {"other:x"}
    asyncio.run(run())


def test_leading_metachar_falls_back_to_full_scan(tmp_path):
    async def run():
        await _setup(tmp_path / "s.db")
        # pattern starts with '*' → no usable prefix → full scan + fnmatch
        assert set(await state_store.scan_keys("*:1")) == {"p:a:1", "p:b:1"}
    asyncio.run(run())


def test_count_limit_respected(tmp_path):
    async def run():
        await _setup(tmp_path / "s.db")
        got = await state_store.scan_keys("p:*", count=1)
        assert len(got) == 1
    asyncio.run(run())
