"""R-F4073 (C-125) — the journal was O(writes) when it only needs O(records).

C-95 made persistence cost O(change) by journalling instead of rewriting the
whole graph. C-105 then stopped a timer from forcing whole-graph rewrites on a
trivial journal. What remained is that the journal itself grows with every WRITE,
not with every distinct RECORD.

MEASURED LIVE on aria-intel 2026-08-16:

    journal bytes : 1,362,425
    entries       : 369
    distinct ids  : 64
    redundant     : 305  (82.7% of entries are repeat upserts)
    most-rewritten: [('09a8389b', 163), ('40ec7510', 90), ('d6ba5e8d', 15)]

One record was rewritten 163 times. Since compaction fires on journal SIZE
(`JOURNAL_MAX_BYTES`, and C-105's ratio floor), that redundancy pulls forward
every 411 MB snapshot rewrite by ~5.8x.

WHY THIS IS SAFE, AND WHY IT NEEDS NO FORMAT CHANGE. `_replay_journal` is
already an id-keyed UPSERT, so the final state depends only on the LAST write
per id. Dropping superseded entries is therefore semantically identical.

THE SUBTLE PART — and the reason naive dedupe would be WRONG. Replay inserts a
record it has not seen at the HEAD, to preserve the newest-first ordering
`store_fact`'s `insert(0, ...)` establishes. So head-insertion order follows
FIRST appearance, while content follows LAST write. Keeping only the last
occurrence would reorder new facts. Compaction therefore preserves
**first-appearance ORDER with last-write CONTENT**.

The rewrite is atomic (tmp + fsync + rename): the journal holds every fact
written since the last snapshot, so a torn write would lose real memory (§7).
"""
from __future__ import annotations

import json
import os

import pytest

from aria_service.intel import knowledge as k


def _write_journal(path, entries):
    with open(path, "w", encoding="utf-8") as fh:
        for e in entries:
            fh.write(json.dumps(e) + "\n")


def _read_journal(path):
    out = []
    with open(path, encoding="utf-8") as fh:
        for ln in fh:
            ln = ln.strip()
            if ln:
                out.append(json.loads(ln))
    return out


@pytest.fixture
def journal(tmp_path, monkeypatch):
    p = tmp_path / "aria_knowledge.json.journal.jsonl"
    monkeypatch.setattr(k, "_journal_path", lambda: str(p))
    return str(p)


def test_dedupe_keeps_last_content(journal):
    _write_journal(journal, [
        {"kind": "fact", "rec": {"id": "a", "content": "v1"}},
        {"kind": "fact", "rec": {"id": "a", "content": "v2"}},
        {"kind": "fact", "rec": {"id": "a", "content": "v3"}},
    ])
    k._compact_journal()
    out = _read_journal(journal)
    assert len(out) == 1, f"expected 1 entry, got {len(out)}"
    assert out[0]["rec"]["content"] == "v3", "last write must win"


def test_dedupe_preserves_first_appearance_order(journal):
    """THE subtle one: head-insertion order follows FIRST appearance."""
    _write_journal(journal, [
        {"kind": "fact", "rec": {"id": "a", "content": "a1"}},
        {"kind": "fact", "rec": {"id": "b", "content": "b1"}},
        {"kind": "fact", "rec": {"id": "a", "content": "a2"}},
    ])
    k._compact_journal()
    ids = [e["rec"]["id"] for e in _read_journal(journal)]
    assert ids == ["a", "b"], (
        f"order must follow FIRST appearance (a then b), got {ids} — keeping "
        f"the last occurrence would reorder newly inserted facts"
    )


def test_replay_result_is_identical_before_and_after(journal):
    """The property that makes this safe: same final state, fewer bytes."""
    entries = [
        {"kind": "fact", "rec": {"id": "a", "content": "a1"}},
        {"kind": "fact", "rec": {"id": "b", "content": "b1"}},
        {"kind": "fact", "rec": {"id": "a", "content": "a2"}},
        {"kind": "fact", "rec": {"id": "c", "content": "c1"}},
        {"kind": "fact", "rec": {"id": "b", "content": "b2"}},
    ]
    _write_journal(journal, entries)
    before = k._replay_journal({"facts": [], "queries": [], "learnings": []})

    _write_journal(journal, entries)
    k._compact_journal()
    after = k._replay_journal({"facts": [], "queries": [], "learnings": []})

    assert before == after, (
        "compaction changed the replayed state — it must be a pure size "
        f"reduction.\nbefore={before}\nafter={after}"
    )


def test_entries_without_an_id_are_preserved(journal):
    """An unkeyed entry cannot be deduped; dropping it would lose a write."""
    _write_journal(journal, [
        {"kind": "fact", "rec": {"content": "no-id-1"}},
        {"kind": "fact", "rec": {"id": "a", "content": "a1"}},
        {"kind": "fact", "rec": {"content": "no-id-2"}},
    ])
    k._compact_journal()
    out = _read_journal(journal)
    assert len(out) == 3, f"unkeyed entries must survive, got {out}"


def test_corrupt_lines_do_not_destroy_the_journal(journal):
    """A journal holding real memory must never be truncated by a parse error."""
    with open(journal, "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"kind": "fact", "rec": {"id": "a", "content": "a1"}}) + "\n")
        fh.write("{ this is not json\n")
        fh.write(json.dumps({"kind": "fact", "rec": {"id": "b", "content": "b1"}}) + "\n")

    k._compact_journal()
    # Read leniently: the corrupt line is preserved verbatim by design, so a
    # strict json reader would choke on the very thing being asserted.
    ids = []
    with open(journal, encoding="utf-8") as fh:
        raw_lines = [ln.strip() for ln in fh if ln.strip()]
    for ln in raw_lines:
        try:
            ids.append((json.loads(ln).get("rec") or {}).get("id"))
        except Exception:
            ids.append("<corrupt>")
    assert "a" in ids and "b" in ids, (
        f"a corrupt line must not cost the surrounding records: {raw_lines}"
    )
    assert "<corrupt>" in ids, (
        "the unparseable line was DROPPED — the journal holds every fact "
        "written since the last snapshot, so guessing is not worth losing "
        f"memory over (§7): {raw_lines}"
    )


def test_missing_journal_is_a_noop(journal):
    assert not os.path.exists(journal)
    k._compact_journal()          # must not raise
    assert not os.path.exists(journal), "compaction must not create a journal"


def test_no_temp_file_is_left_behind(journal):
    _write_journal(journal, [
        {"kind": "fact", "rec": {"id": "a", "content": "a1"}},
        {"kind": "fact", "rec": {"id": "a", "content": "a2"}},
    ])
    k._compact_journal()
    leftovers = [f for f in os.listdir(os.path.dirname(journal)) if f.endswith(".tmp")]
    assert not leftovers, f"atomic rewrite left a temp file: {leftovers}"


def test_compaction_is_actually_wired_into_the_flush():
    """An uncalled compactor is the C-101 unused-guard shape."""
    import inspect

    src = inspect.getsource(k._flush_to_disk_locked)
    assert "_compact_journal" in src, (
        "journal compaction is never invoked from the flush — the redundancy "
        "it removes would keep pulling whole-graph rewrites forward"
    )
    assert "JOURNAL_COMPACT_MIN_BYTES" in src, (
        "compaction must be size-gated, or every debounced flush pays a full "
        "journal read+rewrite"
    )
    # It must run BEFORE the size checks it exists to influence.
    assert src.index("_compact_journal") < src.index("_journal_due = "), (
        "compaction runs after the size check, so the check still sees the "
        "un-deduped size — the fix would be inert"
    )


def test_threshold_is_below_the_compaction_trigger():
    """Redundancy must be removed before it can force a snapshot rewrite."""
    assert k.JOURNAL_COMPACT_MIN_BYTES < k.JOURNAL_MAX_BYTES, (
        "journal compaction must fire below JOURNAL_MAX_BYTES, or the snapshot "
        "rewrite happens first and the dedupe never helps"
    )
