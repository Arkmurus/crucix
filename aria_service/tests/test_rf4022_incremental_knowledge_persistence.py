"""R-F4022 (C-95) — knowledge persistence must cost O(change), not O(graph).

THE DEFECT. `_write_to_disk_atomic` rewrites the ENTIRE knowledge graph —
serialise + fsync + rename + dir-fsync — on every debounced flush, to persist
whatever changed since the last one. Measured live on aria-intel 2026-08-14:

    /data/aria_knowledge.json          389,197,582 bytes
    full rewrite completed every       ~18-26 s, back to back, continuously
    new content per rewrite            ~9-11 KB

That is ~39,000x write amplification, and a tmp file was present in EVERY
sample — the volume never stopped writing. `knowledge.py:_write_to_disk_atomic`
appeared in 18 of 18 event-loop stall dumps in a 20-minute window (median stall
6.8 s, max 10.8 s), 12 of them parked in the `os.fsync` at what was then line
717, while the main thread sat idle in `selectors.select` — the R-F3252
signature of starvation rather than a blocking call.

WHY IT IS SELF-WORSENING, and why a knob is not the fix. §7 forbids eviction,
so the graph only ever grows: the cost of persisting one fact rises without
bound as ARIA learns. Raising FLUSH_DEBOUNCE_S would trade durability for
latency and leave the O(graph) term untouched — the §1 band-aid this repo
forbids. The fix has to change the COMPLEXITY, not the cadence.

THE CONTRACT THESE TESTS PIN.
  1. A flush that only ADDED records journals them and does NOT rewrite the
     snapshot. This is the amplification test and it is the one that was RED.
  2. Journalled records survive a restart — the durability guarantee is not
     weakened by making the fast path cheap. §7: losing a FACT is never
     acceptable.
  3. A structural change (removal/merge) still forces a full rewrite: the
     journal is an upsert log and cannot express a deletion.
  4. A `_save()` that does not declare its record forces a full rewrite. This
     is the SAFETY DEFAULT: a future mutation site added by someone who never
     read this file degrades to today's behaviour rather than silently losing
     data.
  5. Compaction clears the journal, so replay cost stays bounded.
  6. Replay is idempotent and upserts by id — a record present in BOTH the
     snapshot and the journal must not duplicate, because compaction and
     journal-append legitimately race.
"""
import asyncio
import json
import os
import tempfile

import pytest

from aria_service.intel import knowledge


@pytest.fixture(autouse=True)
def _isolated_knowledge_store(tmp_path):
    """Point the module at a private file and restore ALL mutated globals.

    knowledge.py is a module-level singleton; leaking `_cache` between tests
    would make every assertion here order-dependent.
    """
    target = str(tmp_path / "aria_knowledge.json")
    saved = {
        "_DISK_PATH": knowledge._DISK_PATH,
        "_cache": knowledge._cache,
        "_dirty": knowledge._dirty,
        "_dirty_bookkeeping_since": knowledge._dirty_bookkeeping_since,
        # R-F4022's own state. Restoring these matters as much as `_cache`:
        # `_needs_compaction` leaking in as True would make the amplification
        # test pass for the wrong reason, and leaking out would change how the
        # next FILE's tests flush. That is the order-dependence the suite
        # baseline already records as its dominant flake class.
        "_needs_compaction": knowledge._needs_compaction,
        "_last_compaction_at": knowledge._last_compaction_at,
    }
    saved_pending = list(knowledge._pending_journal)
    saved_bk = dict(knowledge._pending_bookkeeping)

    knowledge._DISK_PATH = target
    knowledge._cache = None
    knowledge._dirty = False
    knowledge._dirty_bookkeeping_since = None
    knowledge._needs_compaction = True
    knowledge._last_compaction_at = None
    knowledge._pending_journal.clear()
    knowledge._pending_bookkeeping.clear()

    yield target

    for k, v in saved.items():
        setattr(knowledge, k, v)
    knowledge._pending_journal[:] = saved_pending
    knowledge._pending_bookkeeping.clear()
    knowledge._pending_bookkeeping.update(saved_bk)


def _seed(n_facts: int = 200) -> dict:
    """A cache big enough that a full rewrite is obviously distinguishable."""
    return {
        "version": 1,
        "facts": [
            {"id": f"seed{i}", "topic": f"t{i % 7}",
             "content": f"seeded fact {i} " + ("x" * 200),
             "confidence": "CONFIRMED", "accessCount": 0}
            for i in range(n_facts)
        ],
        "queries": [],
        "learnings": [],
    }


def _count_full_rewrites(monkeypatch) -> list:
    """Record every call to the full-snapshot writer."""
    calls = []
    real = knowledge._write_to_disk_atomic

    def _spy(data, write_sidecar=True):
        calls.append(len(data.get("facts", [])))
        return real(data, write_sidecar)

    monkeypatch.setattr(knowledge, "_write_to_disk_atomic", _spy)
    return calls


async def _compact_now():
    """Force a full snapshot so the following flush starts from a clean base."""
    knowledge._dirty = True
    await knowledge._flush_to_disk(final=True)


# ── 1. the amplification test — this is the one that was RED ────────────────

@pytest.mark.asyncio
async def test_additive_flush_does_not_rewrite_the_whole_snapshot(monkeypatch):
    knowledge._cache = _seed()
    await _compact_now()

    calls = _count_full_rewrites(monkeypatch)

    # One new fact — the overwhelmingly common mutation.
    new = {"id": "new1", "topic": "t1", "content": "a brand new fact",
           "confidence": "CONFIRMED", "accessCount": 0}
    knowledge._cache["facts"].insert(0, new)
    await knowledge._save(record=new, kind="fact")
    await knowledge._flush_to_disk()

    assert calls == [], (
        "R-F4022: adding one fact rewrote the entire snapshot "
        f"({calls} facts serialised). Persistence must cost O(change), not "
        "O(graph) — this is the 39,000x write amplification that saturates "
        "the volume and starves the event loop."
    )


# ── 2. durability is NOT weakened by the fast path (§7) ─────────────────────

@pytest.mark.asyncio
async def test_journalled_fact_survives_a_restart():
    knowledge._cache = _seed()
    await _compact_now()

    new = {"id": "survivor", "topic": "t2", "content": "must not be forgotten",
           "confidence": "CONFIRMED", "accessCount": 0}
    knowledge._cache["facts"].insert(0, new)
    await knowledge._save(record=new, kind="fact")
    await knowledge._flush_to_disk()

    # Simulate a restart: drop the in-memory cache and re-hydrate from disk.
    knowledge._cache = None
    reloaded = await knowledge._load()

    ids = {f["id"] for f in reloaded["facts"]}
    assert "survivor" in ids, (
        "R-F4022: a journalled fact was LOST across a restart. §7 — losing a "
        "counter is acceptable, losing a FACT never is."
    )
    assert len(reloaded["facts"]) == 201, "seeded facts must also survive"


@pytest.mark.asyncio
async def test_in_place_update_survives_a_restart():
    """An edit to an EXISTING fact is material and must replay, not just adds."""
    knowledge._cache = _seed()
    await _compact_now()

    target = knowledge._cache["facts"][5]
    target["content"] = "CORRECTED CONTENT"
    target["confidence"] = "VERIFIED"
    await knowledge._save(record=target, kind="fact")
    await knowledge._flush_to_disk()

    knowledge._cache = None
    reloaded = await knowledge._load()

    got = [f for f in reloaded["facts"] if f["id"] == target["id"]]
    assert len(got) == 1, "upsert by id must not duplicate the edited fact"
    assert got[0]["content"] == "CORRECTED CONTENT"
    assert got[0]["confidence"] == "VERIFIED"


# ── 3. a journal cannot express a deletion — structural forces compaction ───

@pytest.mark.asyncio
async def test_structural_change_forces_a_full_rewrite(monkeypatch):
    knowledge._cache = _seed()
    await _compact_now()

    calls = _count_full_rewrites(monkeypatch)

    # consolidate_facts / purge_by_keywords REPLACE the list (removals).
    knowledge._cache["facts"] = knowledge._cache["facts"][10:]
    await knowledge._save(structural=True)
    await knowledge._flush_to_disk()

    assert calls, (
        "R-F4022: a removal was journalled instead of compacted. The journal "
        "is an UPSERT log — replaying it would resurrect deleted facts."
    )


# ── 4. the safety default: an undeclared mutation degrades to a full write ──

@pytest.mark.asyncio
async def test_save_without_a_record_forces_a_full_rewrite(monkeypatch):
    knowledge._cache = _seed()
    await _compact_now()

    calls = _count_full_rewrites(monkeypatch)

    # A future call site that mutates the cache and calls _save() with no
    # record. We cannot know what changed, so we must write everything.
    knowledge._cache["facts"][3]["content"] = "changed by an undeclared path"
    await knowledge._save()
    await knowledge._flush_to_disk()

    assert calls, (
        "R-F4022: _save() with no declared record took the journal fast path. "
        "An undeclared mutation MUST fall back to a full rewrite — otherwise a "
        "new mutation site silently loses data."
    )


# ── 5. compaction bounds replay cost ────────────────────────────────────────

@pytest.mark.asyncio
async def test_compaction_clears_the_journal():
    knowledge._cache = _seed()
    await _compact_now()

    new = {"id": "j1", "topic": "t0", "content": "journalled",
           "confidence": "CONFIRMED", "accessCount": 0}
    knowledge._cache["facts"].insert(0, new)
    await knowledge._save(record=new, kind="fact")
    await knowledge._flush_to_disk()

    jpath = knowledge._journal_path()
    assert os.path.exists(jpath) and os.path.getsize(jpath) > 0, \
        "the additive flush should have written a journal"

    await _compact_now()

    assert not os.path.exists(jpath) or os.path.getsize(jpath) == 0, (
        "R-F4022: the journal survived compaction — replay cost would grow "
        "without bound and boots would get slower forever."
    )


# ── 6. replay is idempotent (compaction and append legitimately race) ───────

@pytest.mark.asyncio
async def test_replay_is_idempotent_and_upserts_by_id():
    knowledge._cache = _seed(20)
    await _compact_now()

    dup = knowledge._cache["facts"][0]
    dup["content"] = "updated once"
    await knowledge._save(record=dup, kind="fact")
    await knowledge._flush_to_disk()

    # Replay the SAME journal twice — a crash between compaction and journal
    # truncation makes this a real sequence, not a synthetic one.
    knowledge._cache = None
    first = await knowledge._load()
    n_first = len(first["facts"])

    knowledge._cache = None
    second = await knowledge._load()

    assert len(second["facts"]) == n_first, (
        "R-F4022: replaying the journal twice duplicated records — upsert must "
        "key on id."
    )
    got = [f for f in second["facts"] if f["id"] == dup["id"]]
    assert len(got) == 1 and got[0]["content"] == "updated once"


# ── 7. queries and learnings are journalled too ─────────────────────────────

@pytest.mark.asyncio
async def test_queries_and_learnings_survive_a_restart(monkeypatch):
    knowledge._cache = _seed(20)
    await _compact_now()

    calls = _count_full_rewrites(monkeypatch)

    q = {"id": "q1", "query": "who is X", "summary": "s", "market": "",
         "category": "", "createdAt": "2026-08-14T00:00:00Z"}
    knowledge._cache["queries"].insert(0, q)
    await knowledge._save(record=q, kind="query")

    lr = {"id": "l1", "correction": "not that", "context": "ctx",
          "createdAt": "2026-08-14T00:00:00Z"}
    knowledge._cache["learnings"].insert(0, lr)
    await knowledge._save(record=lr, kind="learning")

    await knowledge._flush_to_disk()

    assert calls == [], "queries/learnings must journal, not force a rewrite"

    knowledge._cache = None
    reloaded = await knowledge._load()
    assert [x["id"] for x in reloaded["queries"]] == ["q1"]
    assert [x["id"] for x in reloaded["learnings"]] == ["l1"]


# ── 7b. bookkeeping must not drag a full rewrite back in ───────────────────

@pytest.mark.asyncio
async def test_due_bookkeeping_rides_the_journal_not_a_compaction(monkeypatch):
    """The regression that would have quietly halved this fix.

    C-61 defers a counter bump for BOOKKEEPING_MAX_AGE_S and then forces a
    flush. If that flush is a COMPACTION, production compacts every 300 s —
    because a crawl loop re-encountering known pages is the single most common
    mutation there is. Declaring the record lets it ride a ~1 KB journal line.
    """
    knowledge._cache = _seed()
    await _compact_now()

    calls = _count_full_rewrites(monkeypatch)

    f = knowledge._cache["facts"][0]
    f["accessCount"] = f.get("accessCount", 0) + 1
    await knowledge._save(material=False, record=f, kind="fact")

    # Make it overdue.
    knowledge._dirty_bookkeeping_since = (
        __import__("time").monotonic() - knowledge.BOOKKEEPING_MAX_AGE_S - 1
    )
    await knowledge._flush_to_disk()

    assert calls == [], (
        "R-F4022: due bookkeeping forced a whole-graph rewrite. With a crawl "
        "loop this happens every BOOKKEEPING_MAX_AGE_S, which reinstates most "
        "of the amplification this change removes."
    )
    assert knowledge._dirty_bookkeeping_since is None, \
        "the deferral must clear once the counter is on disk"

    knowledge._cache = None
    reloaded = await knowledge._load()
    got = [x for x in reloaded["facts"] if x["id"] == f["id"]]
    assert got and got[0]["accessCount"] == 1, "the counter did not persist"


@pytest.mark.asyncio
async def test_repeat_bookkeeping_on_one_record_costs_one_journal_entry():
    """Dedup by id — otherwise the journal grows at crawl rate."""
    knowledge._cache = _seed()
    await _compact_now()

    f = knowledge._cache["facts"][0]
    for _ in range(50):
        f["accessCount"] = f.get("accessCount", 0) + 1
        await knowledge._save(material=False, record=f, kind="fact")

    assert len(knowledge._pending_bookkeeping) == 1, (
        "50 re-encounters of the SAME fact must collapse to one pending entry"
    )

    knowledge._dirty_bookkeeping_since = (
        __import__("time").monotonic() - knowledge.BOOKKEEPING_MAX_AGE_S - 1
    )
    await knowledge._flush_to_disk()

    with open(knowledge._journal_path(), encoding="utf-8") as fh:
        lines = [ln for ln in fh if ln.strip()]
    assert len(lines) == 1, f"expected 1 journal line, got {len(lines)}"


# ── 7c. undeclared bookkeeping still compacts (the safety default again) ────

@pytest.mark.asyncio
async def test_undeclared_due_bookkeeping_still_compacts(monkeypatch):
    knowledge._cache = _seed()
    await _compact_now()

    calls = _count_full_rewrites(monkeypatch)

    knowledge._cache["facts"][0]["accessCount"] = 99
    await knowledge._save(material=False)          # no record declared
    knowledge._dirty_bookkeeping_since = (
        __import__("time").monotonic() - knowledge.BOOKKEEPING_MAX_AGE_S - 1
    )
    await knowledge._flush_to_disk()

    assert calls, (
        "R-F4022: bookkeeping that did not declare its record must fall back "
        "to a full rewrite — we cannot journal bytes we were never told about."
    )


# ── 8. end-to-end through the real public API ──────────────────────────────

@pytest.mark.asyncio
async def test_store_fact_end_to_end_is_incremental_and_durable(monkeypatch):
    """§3c — drive the real entry point, not just the helper."""
    knowledge._cache = _seed(50)
    await _compact_now()

    calls = _count_full_rewrites(monkeypatch)

    await knowledge.store_fact(
        topic="rf4022_topic",
        content=(
            "An end-to-end fact stored through the public store_fact API, "
            "long enough to clear the R-F1526 minimum-content guard so this "
            "test exercises the real persistence path rather than the reject "
            "branch."
        ),
        source="test",
        confidence="CONFIRMED",
        skip_semantic_index=True,
    )
    await knowledge._flush_to_disk()

    assert calls == [], (
        "R-F4022: store_fact still triggers a whole-graph rewrite on the "
        "real path."
    )

    knowledge._cache = None
    reloaded = await knowledge._load()
    topics = {f.get("topic") for f in reloaded["facts"]}
    assert "rf4022_topic" in topics, "the stored fact did not survive a restart"
