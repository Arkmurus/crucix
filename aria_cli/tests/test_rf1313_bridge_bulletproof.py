"""R-F1313 capability tests — bulletproof the Claude<->ARIA bridge.

Invokes the two paths that were broken (per CLAUDE.md §3c: a capability test
must call the broken path and assert the user-visible outcome):

  1. Collision-proof ids: the old `_gen_id(seq)` used a racy `len(_all())` seq,
     so two appends in the same millisecond produced the SAME id and a message
     was silently dropped on read. We assert ids stay unique even when the
     observed sequence collides, and that read_new() returns BOTH messages.

  2. Crash-safe seen-state: a seen file truncated by a mid-write kill used to
     read back as an empty set, so every message replayed as "new". We assert
     read_new() is idempotent (no replay) and that the writer leaves no partial
     temp files behind.
"""
from __future__ import annotations

import json

from aria_cli import bridge


def test_gen_id_unique_under_same_sequence():
    """Same observed seq + same wall-clock must NOT yield a duplicate id."""
    ids = {bridge._gen_id(0) for _ in range(2000)}
    # Old form (m{ms:x}000) would collapse to ~1 id per millisecond → heavy
    # collisions. The salted form must keep them effectively unique.
    assert len(ids) >= 1990, f"id collisions: only {len(ids)}/2000 unique"


def test_concurrent_appends_both_delivered_once(tmp_path):
    """Two messages to claude must each be delivered exactly once (no drop)."""
    bridge.send(tmp_path, frm="aria", to="claude", text="msg-A")
    bridge.send(tmp_path, frm="aria", to="claude", text="msg-B")

    first = bridge.read_new(tmp_path, "claude")
    texts = sorted(m["text"] for m in first)
    assert texts == ["msg-A", "msg-B"], f"message dropped/duplicated: {texts}"

    # Idempotent: a second poll returns nothing (both already consumed).
    assert bridge.read_new(tmp_path, "claude") == []


def test_seen_write_is_atomic_and_idempotent(tmp_path):
    """A consumed message must stay consumed and no .tmp turds are left."""
    bridge.send(tmp_path, frm="aria", to="claude", text="only")
    assert len(bridge.read_new(tmp_path, "claude")) == 1

    seen_file = bridge._seen_path(tmp_path, "claude")
    assert seen_file.exists()
    # Valid JSON list (not a half-written file).
    assert isinstance(json.loads(seen_file.read_text(encoding="utf-8")), list)
    # No leftover temp files from the atomic replace.
    leftovers = list(seen_file.parent.glob(f"{seen_file.name}.*.tmp"))
    assert leftovers == [], f"atomic write left temp files: {leftovers}"

    # Re-poll: the message must NOT replay.
    assert bridge.read_new(tmp_path, "claude") == []


def test_legacy_message_without_id_does_not_crash(tmp_path):
    """A legacy/corrupt line addressed to the reader but missing an 'id' must be
    skipped, not crash read_new with KeyError (the 2026-06-04 channel break)."""
    bridge.send(tmp_path, frm="aria", to="claude", text="well-formed")
    # Inject a legacy line addressed to claude with NO id (old schema).
    mf = bridge._messages_path(tmp_path)
    with mf.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"to": "claude", "from": "aria", "body": "legacy"}) + "\n")

    got = bridge.read_new(tmp_path, "claude")  # must not raise
    assert [m["text"] for m in got] == ["well-formed"]
    assert bridge.read_new(tmp_path, "claude") == []  # idempotent


def test_corrupt_seen_does_not_crash(tmp_path):
    """A pre-existing corrupt seen file must degrade gracefully, then self-heal
    into a valid file on the next save (no exception bubbles to the caller)."""
    bridge.send(tmp_path, frm="aria", to="claude", text="x")
    seen_file = bridge._seen_path(tmp_path, "claude")
    seen_file.write_text("{ this is not json", encoding="utf-8")  # simulate truncation

    # Must not raise; returns the message (treated as new since seen was lost).
    got = bridge.read_new(tmp_path, "claude")
    assert [m["text"] for m in got] == ["x"]
    # And the file is now valid again (atomic save healed it).
    assert isinstance(json.loads(seen_file.read_text(encoding="utf-8")), list)
