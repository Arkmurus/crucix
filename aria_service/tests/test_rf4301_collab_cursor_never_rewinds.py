"""R-F4301 / C-254 — an unreadable cursor must not rewind the teacher corpus.

MEASURED on the live volume 2026-08-24:

    /data/claude_distill   56,529 records ... 41 UNIQUE TEXTS
    unique content         48 KB   (30 MB on disk -> ~450x amplification)
    most-duplicated        cb_39, captured 1,250 times

The corpus that exists so a future SFT/DPO cycle can distil Claude's reasoning
into ARIA-LLM is ~99.9% duplicate. Consuming it as-is would train on 1,250 copies
of one note.

THE MECHANISM. `drain_for_aria` is cursor-guarded and the guard is correct —
`last_seq` is seeded from the cursor, not from 0. The hole is one level down:

    async def get_cursor(reader) -> int:
        v = await rs.get(_CURSOR_KEY.format(reader=reader))
        return int(v) if v else 0          # <- a FAILED read lands here too

`rs.get` carries the R-F1 None-on-error contract: it returns None both when the
key is genuinely absent AND when the store could not be read (dead connection,
reconnect window). Both collapse to `0`, which rewinds the drain to the very
beginning and re-ingests every message ever sent. `set_cursor` swallows its own
failures too, so a failed write leaves the old cursor and the next cycle repeats
the whole thing.

This is the exact absence-reads-as-a-value class CLAUDE.md §1 records for three
Phase A gates, §17 for the cost meter, C-39 for sanctions coverage and C-41 for
the quota latch. Here it is not a wrong verdict but a corrupted training input,
which is worse: a wrong verdict is read by a human, a corrupted corpus is read by
a fine-tune.

THE FIX IS TRI-STATE, and the direction matters. `get_cursor` returns None for
UNREADABLE, and the drain SKIPS that cycle rather than starting from zero.
Skipping costs one 2-minute interval; rewinding costs the corpus. "I could not
read how far I got" must never mean "I have not started".

Absent-and-readable is still 0 — a genuinely fresh reader must drain from the
beginning exactly once, so the fix must not break first-run.
"""
from __future__ import annotations

import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aria_service.intel import collab_bridge as cb  # noqa: E402
from aria_service.intel.redis_store import StoreReadError  # noqa: E402


# ── get_cursor: three states, never two ────────────────────────────────────

@pytest.mark.asyncio
async def test_absent_cursor_is_zero(monkeypatch) -> None:
    """A genuinely fresh reader starts at 0 — first-run must still work."""
    async def _get(_k):
        return None
    monkeypatch.setattr(cb.rs, "get_strict", _get, raising=False)
    assert await cb.get_cursor("aria") == 0


@pytest.mark.asyncio
async def test_a_stored_cursor_is_returned(monkeypatch) -> None:
    async def _get(_k):
        return "174"
    monkeypatch.setattr(cb.rs, "get_strict", _get, raising=False)
    assert await cb.get_cursor("aria") == 174


@pytest.mark.asyncio
async def test_an_UNREADABLE_cursor_is_None_not_zero(monkeypatch) -> None:
    """THE DEFECT. A store failure used to be indistinguishable from 'no cursor'."""
    async def _boom(_k):
        raise StoreReadError("no read connection (reconnect in progress)")
    monkeypatch.setattr(cb.rs, "get_strict", _boom, raising=False)
    assert await cb.get_cursor("aria") is None


@pytest.mark.asyncio
async def test_a_corrupt_cursor_value_is_UNREADABLE_not_zero(monkeypatch) -> None:
    """Garbage in the key is not evidence the reader is fresh."""
    async def _junk(_k):
        return "not-a-number"
    monkeypatch.setattr(cb.rs, "get_strict", _junk, raising=False)
    assert await cb.get_cursor("aria") is None


# ── the drain: skip, never rewind ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_the_drain_SKIPS_when_the_cursor_is_unreadable(monkeypatch) -> None:
    """THE CAPABILITY TEST — this is what produced 450x amplification.

    An unreadable cursor must not be treated as 0. If it were, `poll` would be
    asked for everything after seq 0 and the entire corpus would be re-captured.
    """
    polled: list[int] = []

    async def _cursor(_r):
        return None

    async def _poll(_reader, after_seq=0):
        polled.append(after_seq)
        return [{"seq": 1, "id": "cb_1", "frm": "claude", "to": "aria",
                 "kind": "note", "text": "should NOT be re-drained"}]

    monkeypatch.setattr(cb, "get_cursor", _cursor)
    monkeypatch.setattr(cb, "poll", _poll)

    out = await cb.drain_for_aria()
    assert polled == [], "poll was called with an unreadable cursor — that is the rewind"
    assert out.get("drained") == 0
    assert out.get("skipped") or out.get("reason"), (
        "a skipped cycle must SAY it was skipped, or the caller reads it as 'nothing new'")


@pytest.mark.asyncio
async def test_a_readable_cursor_still_drains_from_it(monkeypatch) -> None:
    """The guard must not disable the drain — it must only refuse the rewind."""
    polled: list[int] = []

    async def _cursor(_r):
        return 174

    async def _poll(_reader, after_seq=0):
        polled.append(after_seq)
        return []

    monkeypatch.setattr(cb, "get_cursor", _cursor)
    monkeypatch.setattr(cb, "poll", _poll)
    out = await cb.drain_for_aria()
    assert polled == [174], f"expected a drain from the stored cursor, polled={polled}"
    assert out.get("drained") == 0


@pytest.mark.asyncio
async def test_a_fresh_reader_still_drains_from_zero(monkeypatch) -> None:
    """Absent-and-readable is 0, and must still work — otherwise this fix would
    silently stop a brand-new deployment from ever ingesting anything."""
    polled: list[int] = []

    async def _cursor(_r):
        return 0

    async def _poll(_reader, after_seq=0):
        polled.append(after_seq)
        return []

    monkeypatch.setattr(cb, "get_cursor", _cursor)
    monkeypatch.setattr(cb, "poll", _poll)
    await cb.drain_for_aria()
    assert polled == [0]


# ── end-to-end: a failing STORE, through the real get_cursor ───────────────

@pytest.mark.asyncio
async def test_a_failing_store_never_reaches_poll_with_zero(monkeypatch) -> None:
    """THE INTEGRATION CASE, and the one that matters.

    The other drain test patches `get_cursor` itself, so it proves the drain
    HANDLES None but never exercises how None is produced. A mutation check
    confirmed that gap: reverting `get_cursor` to `return 0` on failure left that
    test green. This drives the REAL get_cursor with a store that raises, which
    is the actual production failure — a reconnect window — and asserts the whole
    chain refuses to rewind.
    """
    polled: list[int] = []

    async def _boom(_k):
        raise StoreReadError("no read connection (reconnect in progress)")

    async def _poll(_reader, after_seq=0):
        polled.append(after_seq)
        return [{"seq": 1, "id": "cb_1", "frm": "claude", "to": "aria",
                 "kind": "note", "text": "must not be re-ingested"}]

    monkeypatch.setattr(cb.rs, "get_strict", _boom, raising=False)
    monkeypatch.setattr(cb, "poll", _poll)

    out = await cb.drain_for_aria()
    assert polled == [], (
        f"a store failure reached poll() as after_seq={polled} — that is the "
        "rewind that amplified 48 KB into 30 MB")
    assert out.get("skipped") == "cursor_unreadable"
