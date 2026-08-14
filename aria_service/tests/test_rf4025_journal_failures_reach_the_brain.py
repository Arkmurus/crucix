"""R-F4025 (C-97) — R-F4022's own failure branches were DARK.

§21a defines wired: a path emits to `brain_hook.absorb` / `record_gap` /
`mistake_ledger` / a metric / a brain signal on BOTH its success and its failure
branch, and it says explicitly that "logged to console / except: pass / local
ring buffer" is **DARK, not wired**.

R-F4022 added four failure branches and every one of them was a bare `logger`
call:

    knowledge.py  journal truncate failed (non-fatal)   logger.warning
    knowledge.py  journal replay failed, snapshot only  logger.warning
    knowledge.py  journal append failed                 logger.error
    knowledge.py  disk flush failed                     logger.error

That is worse here than in an average module, because these are the branches
where ARIA FORGETS. A failed journal append means recent facts are held only in
memory; a failed replay means facts already on disk are not loaded. §7 says
losing a fact is never acceptable — and the brain could not see either event.

It is also the exact shape C-95 was: an instrument nobody reads. C-95 went
unnoticed for a day because `/health` never consulted a gauge it published
(C-96); this would have gone unnoticed because the only record was a log line
in a service that emits thousands per minute.

WHAT IS PINNED
  - each failure branch emits a brain failure signal, naming which branch;
  - repeated failures are RATE-LIMITED — the flusher runs every 2 s, so an
    unguarded per-failure signal is the ledger-filling flood that §17 and
    `loop_monitor` already record as the reason monitors are exempted from
    per-event wiring;
  - a compaction SUCCESS is wired too (§21a is both branches), rate-limited for
    the same reason;
  - wiring NEVER breaks persistence: if the brain itself throws, the flush must
    still complete. Observability must not become the outage.
"""
import asyncio

import pytest

from aria_service.intel import knowledge


@pytest.fixture(autouse=True)
def _isolate(tmp_path):
    saved = {
        "_DISK_PATH": knowledge._DISK_PATH,
        "_cache": knowledge._cache,
        "_dirty": knowledge._dirty,
        "_dirty_bookkeeping_since": knowledge._dirty_bookkeeping_since,
        "_needs_compaction": knowledge._needs_compaction,
        "_last_compaction_at": knowledge._last_compaction_at,
    }
    pend = list(knowledge._pending_journal)
    bk = dict(knowledge._pending_bookkeeping)

    knowledge._DISK_PATH = str(tmp_path / "aria_knowledge.json")
    knowledge._cache = None
    knowledge._dirty = False
    knowledge._dirty_bookkeeping_since = None
    knowledge._needs_compaction = True
    knowledge._last_compaction_at = None
    knowledge._pending_journal.clear()
    knowledge._pending_bookkeeping.clear()
    knowledge._reset_persistence_wire_state()

    yield

    for k, v in saved.items():
        setattr(knowledge, k, v)
    knowledge._pending_journal[:] = pend
    knowledge._pending_bookkeeping.clear()
    knowledge._pending_bookkeeping.update(bk)
    knowledge._reset_persistence_wire_state()


def _seed(n=20):
    return {
        "version": 1, "queries": [], "learnings": [],
        "facts": [{"id": f"s{i}", "topic": "t", "content": f"fact {i} " + "x" * 80,
                   "confidence": "CONFIRMED", "accessCount": 0} for i in range(n)],
    }


def _capture_failures(monkeypatch):
    seen = []
    monkeypatch.setattr(knowledge, "wire_failure",
                        lambda **kw: seen.append(kw))
    return seen


async def _compact(final=True):
    knowledge._dirty = True
    await knowledge._flush_to_disk(final=final)


# ── the four dark branches ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_journal_append_failure_reaches_the_brain(monkeypatch):
    knowledge._cache = _seed()
    await _compact()
    seen = _capture_failures(monkeypatch)

    def _boom(entries):
        raise OSError("disk full")
    monkeypatch.setattr(knowledge, "_append_journal", _boom)

    rec = {"id": "n1", "topic": "t", "content": "new " + "y" * 80,
           "confidence": "CONFIRMED"}
    knowledge._cache["facts"].insert(0, rec)
    await knowledge._save(record=rec, kind="fact")
    await knowledge._flush_to_disk()

    assert seen, (
        "R-F4025: a failed journal append means recent facts exist ONLY in "
        "memory, and the brain was told nothing (§21a: a logger call is DARK)."
    )
    assert any("journal" in str(k.get("source", "")) for k in seen), seen


@pytest.mark.asyncio
async def test_compaction_failure_reaches_the_brain(monkeypatch):
    knowledge._cache = _seed()
    await _compact()
    seen = _capture_failures(monkeypatch)

    def _boom(data, write_sidecar=True):
        raise OSError("volume gone")
    monkeypatch.setattr(knowledge, "_write_to_disk_atomic", _boom)

    knowledge._needs_compaction = True
    knowledge._dirty = True
    await knowledge._flush_to_disk()

    assert seen, "R-F4025: the snapshot write failed and nothing reached the brain"


@pytest.mark.asyncio
async def test_replay_failure_reaches_the_brain(monkeypatch):
    """The most dangerous branch: facts ARE on disk and are not loaded."""
    knowledge._cache = _seed()
    await _compact()

    rec = {"id": "j1", "topic": "t", "content": "journalled " + "z" * 80,
           "confidence": "CONFIRMED"}
    knowledge._cache["facts"].insert(0, rec)
    await knowledge._save(record=rec, kind="fact")
    await knowledge._flush_to_disk()

    seen = _capture_failures(monkeypatch)

    real_open = open

    def _bad_open(path, *a, **kw):
        if str(path).endswith(".journal.jsonl"):
            raise OSError("permission denied")
        return real_open(path, *a, **kw)
    monkeypatch.setattr("builtins.open", _bad_open)

    knowledge._cache = None
    await knowledge._load()

    assert seen, (
        "R-F4025: the journal could not be replayed — facts on disk were NOT "
        "loaded — and the brain was told nothing. §7: never forget."
    )


@pytest.mark.asyncio
async def test_truncate_failure_reaches_the_brain(monkeypatch):
    knowledge._cache = _seed()
    await _compact()

    rec = {"id": "j2", "topic": "t", "content": "journalled " + "z" * 80,
           "confidence": "CONFIRMED"}
    knowledge._cache["facts"].insert(0, rec)
    await knowledge._save(record=rec, kind="fact")
    await knowledge._flush_to_disk()

    seen = _capture_failures(monkeypatch)

    def _boom(size_before):
        raise OSError("cannot truncate")
    monkeypatch.setattr(knowledge, "_truncate_journal_after_compaction", _boom)

    knowledge._needs_compaction = True
    await _compact()

    assert seen, "R-F4025: journal truncation failure was dark"


# ── the flood guard: this path runs every 2 seconds ────────────────────────

@pytest.mark.asyncio
async def test_repeated_failures_are_rate_limited(monkeypatch):
    """An unguarded signal here fills the 500-slot ledger in ~17 minutes."""
    knowledge._cache = _seed()
    await _compact()
    seen = _capture_failures(monkeypatch)

    def _boom(entries):
        raise OSError("disk full")
    monkeypatch.setattr(knowledge, "_append_journal", _boom)

    for i in range(25):
        rec = {"id": f"n{i}", "topic": "t", "content": f"new {i} " + "y" * 80,
               "confidence": "CONFIRMED"}
        knowledge._cache["facts"].insert(0, rec)
        await knowledge._save(record=rec, kind="fact")
        await knowledge._flush_to_disk()

    assert len(seen) == 1, (
        f"R-F4025: 25 consecutive failures emitted {len(seen)} signals. The "
        "flusher runs every 2s — a per-failure signal is the ledger-filling "
        "flood that loop_monitor and cost_tracker are already exempted for."
    )


# ── observability must never become the outage ─────────────────────────────

@pytest.mark.asyncio
async def test_a_throwing_brain_does_not_break_persistence(monkeypatch):
    knowledge._cache = _seed()
    await _compact()

    def _explode(**kw):
        raise RuntimeError("brain unreachable")
    monkeypatch.setattr(knowledge, "wire_failure", _explode)

    def _boom(entries):
        raise OSError("disk full")
    monkeypatch.setattr(knowledge, "_append_journal", _boom)

    rec = {"id": "n1", "topic": "t", "content": "new " + "y" * 80,
           "confidence": "CONFIRMED"}
    knowledge._cache["facts"].insert(0, rec)
    await knowledge._save(record=rec, kind="fact")

    # Must not raise, and the record must stay pending rather than be dropped.
    await knowledge._flush_to_disk()
    assert any(r.get("id") == "n1" for _k, r in knowledge._pending_journal), (
        "an unwritten record must remain pending (§7), even when wiring throws"
    )


# ── §21a is BOTH branches ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_compaction_success_is_wired(monkeypatch):
    seen = []
    monkeypatch.setattr(knowledge, "wire_success", lambda **kw: seen.append(kw))
    knowledge._cache = _seed()
    await _compact()
    assert seen, (
        "§21a wants the SUCCESS branch too — 'the graph was persisted' is real "
        "observability, not a formality."
    )


@pytest.mark.asyncio
async def test_success_wiring_is_rate_limited(monkeypatch):
    seen = []
    monkeypatch.setattr(knowledge, "wire_success", lambda **kw: seen.append(kw))
    knowledge._cache = _seed()
    for _ in range(6):
        knowledge._needs_compaction = True
        await _compact()
    assert len(seen) == 1, (
        f"6 compactions emitted {len(seen)} success signals; COMPACT_MAX_AGE_S "
        "is tunable, so this must not be able to flood."
    )


@pytest.mark.asyncio
async def test_a_success_must_not_silence_the_failure_that_follows(monkeypatch):
    """The compaction path emits BOTH outcomes through one helper.

    Keyed by source alone, the success signal starts the cooldown and the next
    failure — within 300 s, which is every failure that matters — is dropped.
    A reporting path that goes quiet precisely when things start failing is the
    same blind-guard shape as C-96.
    """
    knowledge._cache = _seed()
    await _compact()                     # emits the compaction SUCCESS signal

    seen = _capture_failures(monkeypatch)

    def _boom(data, write_sidecar=True):
        raise OSError("volume gone")
    monkeypatch.setattr(knowledge, "_write_to_disk_atomic", _boom)
    knowledge._needs_compaction = True
    knowledge._dirty = True
    await knowledge._flush_to_disk()     # same source, opposite outcome

    assert seen, (
        "a compaction SUCCESS suppressed the compaction FAILURE that followed "
        "it — the cooldown must be keyed by source AND outcome"
    )
