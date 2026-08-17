"""R-F4108 (C-141) — CAPABILITY: persisting one signal must not rewrite 35 MB.

`_flush_loop` runs on `FLUSH_DEBOUNCE_S = 2.0` and calls `_write_to_disk_atomic`,
which `json.dump`s the ENTIRE ledger — 81,971 signals, 35.5 MB on disk — plus
fsync, os.replace and a directory fsync, to persist however few signals changed.

Live on aria-intel 2026-08-17, `json/__init__.py:dump:182` took **52.0%** and
**59.3%** of two consecutive profiler snapshots, co-occurring with
`intel_ledger.py:_write_to_disk_atomic:218` in both, and the platform's own gap
detector recorded it twice unprompted as `[performance]`.

This is verbatim the write amplification C-95 removed from `knowledge.py` by
journalling. `intel_ledger` was never ported. §7 forbids eviction, so the cost
rises without bound — **the better ARIA's memory gets, the more starved she
becomes.**

WHY AN APPEND JOURNAL IS CORRECT HERE. §28 warns that a positional journal is
wrong when records are edited in place. Verified by AST: the only two
assignments into `signals` are whole-list REPLACEMENTS (`_prune`, and the
keyword purge) — structural changes, never an edit of an individual signal.
Signals are head-inserted and removed en masse, nothing else.

THE SAFETY DEFAULT IS LOAD-BEARING, and it is what makes the structural sites
correct for free: `_save()` with **no declared record** forces a FULL REWRITE.
So a mutation site added later degrades to the old behaviour instead of
silently losing data, and `_prune` / `purge_by_keywords` — which already call
bare `_save()` — compact automatically. Replaying an append journal over a
deletion would RESURRECT what was purged.

Run: python -m pytest aria_service/tests/test_rf4097_ledger_journal.py -v
"""
from __future__ import annotations

import asyncio
import json
import os

import pytest


@pytest.fixture()
def ledger(tmp_path, monkeypatch):
    from aria_service.intel import intel_ledger as il

    path = str(tmp_path / "aria_signals.json")
    monkeypatch.setattr(il, "_DISK_PATH", path, raising=False)
    monkeypatch.setattr(il, "_cache", {"signals": [], "version": 1}, raising=False)
    monkeypatch.setattr(il, "_dirty", False, raising=False)
    monkeypatch.setattr(il, "_journal_pending", [], raising=False)
    # Never start the background flusher in a test.
    monkeypatch.setattr(il, "_ensure_flusher", lambda: None, raising=False)
    try:
        os.unlink(il._journal_path())
    except OSError:
        pass
    return il, path


def _sig(i):
    return {"text": f"signal {i}", "source": "unit", "type": "test",
            "url": "", "countries": [], "products": [], "oems": [],
            "severity": "low", "ts": f"2026-08-17T00:00:{i:02d}Z"}


def _bytes_written(il, path, fn):
    """Total bytes the persistence path actually wrote."""
    written = {"n": 0}
    real = il._write_to_disk_atomic

    def _counting(data):
        blob = json.dumps(data)
        written["n"] += len(blob)
        return real(data)

    il._write_to_disk_atomic = _counting
    real_append = il._append_journal

    def _counting_append(records):
        written["n"] += sum(len(json.dumps(r)) for r in records)
        return real_append(records)

    il._append_journal = _counting_append
    try:
        fn()
    finally:
        il._write_to_disk_atomic = real
        il._append_journal = real_append
    return written["n"]


# ══════════════════════════════════════════════════════════════════════
# 1. THE COST — §3 proves a fix works, never what it costs
# ══════════════════════════════════════════════════════════════════════

def test_appending_signals_does_not_rewrite_the_whole_ledger(ledger):
    il, path = ledger
    # A realistic base: enough signals that a full rewrite is expensive.
    il._cache["signals"] = [_sig(i) for i in range(2000)]

    async def _baseline():
        await il._save()                      # bare save => full rewrite
        await il._flush_to_disk()
    asyncio.run(_baseline())                  # one honest full snapshot

    def _add_thirty():
        async def _go():
            for i in range(2000, 2030):
                il._cache["signals"].insert(0, _sig(i))
                await il._save(record=il._cache["signals"][0])
                await il._flush_to_disk()
        asyncio.run(_go())

    cost = _bytes_written(il, path, _add_thirty)
    full = len(json.dumps(il._cache))

    assert cost < full, (
        f"persisting 30 signals wrote {cost} bytes — more than ONE whole-ledger "
        f"snapshot ({full}). That is the amplification C-95 removed from "
        f"knowledge.py and this module never got."
    )


# ══════════════════════════════════════════════════════════════════════
# 2. CORRECTNESS — nothing may be lost or reordered
# ══════════════════════════════════════════════════════════════════════

def test_journalled_signals_survive_a_reload(ledger):
    il, path = ledger
    il._cache["signals"] = [_sig(i) for i in range(5)]

    async def _baseline():
        await il._save()                      # bare save => real snapshot on disk
        await il._flush_to_disk()
    asyncio.run(_baseline())

    async def _go():
        for i in range(5, 9):
            il._cache["signals"].insert(0, _sig(i))
            await il._save(record=il._cache["signals"][0])
        await il._flush_to_disk()
    asyncio.run(_go())

    expected = [s["text"] for s in il._cache["signals"]]
    il._cache = None
    reloaded = asyncio.run(il._load())

    assert [s["text"] for s in reloaded["signals"]] == expected, (
        "journal replay lost or reordered signals — head-insert ordering is "
        "the ledger's contract (newest first)"
    )


# ══════════════════════════════════════════════════════════════════════
# 3. THE SAFETY DEFAULTS — the parts that must not be simplified away
# ══════════════════════════════════════════════════════════════════════

def test_an_undeclared_save_forces_a_full_rewrite(ledger):
    il, path = ledger
    il._cache["signals"] = [_sig(i) for i in range(3)]

    async def _go():
        await il._save()                  # no record declared
        await il._flush_to_disk()
    asyncio.run(_go())

    on_disk = json.load(open(path, encoding="utf-8"))
    assert len(on_disk["signals"]) == 3, (
        "'I was told nothing' must mean 'write everything' — otherwise a "
        "mutation site added later silently loses data"
    )


def test_a_journal_without_its_snapshot_is_never_created(ledger):
    """A journal replays OVER a snapshot. Without one, the appended records are
    orphaned — which is data loss, and is what `test_f110_ledger_disk::
    test_disk_round_trip_survives_cache_reset` caught during this fix."""
    il, path = ledger
    assert not os.path.exists(path)

    async def _go():
        il._cache["signals"].insert(0, _sig(1))
        await il._save(record=il._cache["signals"][0])
        await il._flush_to_disk()
    asyncio.run(_go())

    assert os.path.exists(path), (
        "the first write to a fresh ledger journalled instead of creating the "
        "snapshot, so nothing on disk could ever replay it"
    )
    il._cache = None
    reloaded = asyncio.run(il._load())
    assert [s["text"] for s in reloaded["signals"]] == ["signal 1"]


def test_a_structural_purge_compacts_and_does_not_resurrect(ledger):
    """Replaying an append journal over a deletion RESURRECTS what was purged."""
    il, path = ledger
    il._cache["signals"] = [_sig(i) for i in range(6)]

    async def _go():
        # journal a couple of appends first
        for i in (6, 7):
            il._cache["signals"].insert(0, _sig(i))
            await il._save(record=il._cache["signals"][0])
        await il._flush_to_disk()
        # now a STRUCTURAL change: drop everything but two
        il._cache["signals"] = [_sig(0), _sig(1)]
        await il._save()                  # bare save => full rewrite
        await il._flush_to_disk()
    asyncio.run(_go())

    il._cache = None
    reloaded = asyncio.run(il._load())
    texts = [s["text"] for s in reloaded["signals"]]

    assert texts == ["signal 0", "signal 1"], (
        f"purged signals came back: {texts}. A structural change MUST compact; "
        f"replaying the journal over it resurrects deleted rows."
    )


def test_the_journal_is_removed_on_compaction(ledger):
    il, path = ledger
    il._cache["signals"] = [_sig(0)]

    async def _baseline():
        await il._save()                      # snapshot must exist to journal
        await il._flush_to_disk()
    asyncio.run(_baseline())

    async def _go():
        il._cache["signals"].insert(0, _sig(1))
        await il._save(record=il._cache["signals"][0])
        await il._flush_to_disk()
        assert os.path.exists(il._journal_path()), "nothing was journalled"
        await il._save()                  # compaction
        await il._flush_to_disk()
    asyncio.run(_go())

    assert not os.path.exists(il._journal_path()) or \
        os.path.getsize(il._journal_path()) == 0, (
        "a stale journal after compaction would replay old rows on next load"
    )
