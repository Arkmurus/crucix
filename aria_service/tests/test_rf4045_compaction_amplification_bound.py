"""R-F4045 (C-105) — age-triggered compaction rewrote the whole graph to retire
a trivial journal.

C-95 (R-F4022) made the HOT path cost O(change): the flusher appends changed
records to a journal and the full ~410MB snapshot is rewritten only on
compaction. But `_flush_to_disk_locked` also carries:

    _age_due = _last_compaction_at is None or elapsed >= COMPACT_MAX_AGE_S
    must_compact = final or _needs_compaction or _bk_undeclared or _journal_due or _age_due

so every 900 s ANY dirty state forced a whole-graph rewrite regardless of how
little had changed. Measured live on aria-intel 2026-08-16: the journal grows
~120 KB / 150 s (~2.9 MB/hour), so a 15-minute cycle rewrote **410 MB to retire
~1.4 MB** — ~290x write amplification, and one compaction cost ~6.6 s + ~10.3 s
of FULL io pressure (every runnable task in the VM blocked, which is the
starved-event-loop signature).

That is C-95's own defect at a slower cadence, and C-95's comment names the
principle it violates: *"Raising FLUSH_DEBOUNCE_S ... would leave the O(graph)
term intact — the §1 band-aid. The complexity had to change, not the cadence."*

WHAT THE AGE TRIGGER WAS FOR, and why gating it loses nothing. The stated
requirement is "boot replay stays small and the snapshot never drifts far from
the cache". Replay size is bounded by `JOURNAL_MAX_BYTES` (32 MB) — the journal
IS the replay, and `_replay_journal` streams it as id-keyed upserts, so a bigger
journal costs boot time proportional to the journal, never correctness. Age
bounds nothing that journal size does not already bound.

THE RULE, stated as a relationship so it cannot silently rot (the C-103 lesson):
never spend an O(N) whole-graph rewrite to retire a journal smaller than a fixed
fraction of N. That caps write amplification at 1/ratio BY CONSTRUCTION.

These tests pin the RULE, not a tuning value.
"""
from __future__ import annotations

from aria_service.intel import knowledge as k


def _sizes(monkeypatch, journal: int, snapshot: int) -> None:
    monkeypatch.setattr(k, "_journal_size", lambda: journal)
    monkeypatch.setattr(k, "_snapshot_size", lambda: snapshot)


def test_amplification_is_bounded_by_construction():
    """The knob must be a RATIO, so the bound survives the graph growing."""
    assert 0 < k.COMPACT_MIN_JOURNAL_RATIO < 1, (
        "the minimum-journal rule must be a fraction of the snapshot; a fixed "
        "byte threshold silently becomes a no-op as the graph grows (§7 forbids "
        "eviction, so it only grows)"
    )
    assert k.COMPACT_MIN_JOURNAL_BYTES > 0, (
        "a byte floor is required too, or an empty/small snapshot makes the "
        "ratio zero and every tick compacts again"
    )


def test_trivial_journal_does_not_justify_a_whole_graph_rewrite(monkeypatch):
    """THE defect: 410MB rewritten to retire ~1.4MB."""
    _sizes(monkeypatch, journal=1_400_000, snapshot=410_000_000)
    assert k._journal_worth_compacting() is False, (
        "a 1.4MB journal triggered a 410MB rewrite — ~290x write amplification, "
        "which is C-95's defect on a timer"
    )


def test_a_meaningful_journal_does_justify_it(monkeypatch):
    """The schedule must still work — this must not become a never-compact bug."""
    snapshot = 410_000_000
    floor = max(
        k.COMPACT_MIN_JOURNAL_BYTES,
        int(k.COMPACT_MIN_JOURNAL_RATIO * snapshot),
    )
    _sizes(monkeypatch, journal=floor + 1, snapshot=snapshot)
    assert k._journal_worth_compacting() is True


def test_small_snapshot_uses_the_byte_floor(monkeypatch):
    """Early life: a tiny snapshot must not make the ratio ~0 and compact constantly."""
    _sizes(monkeypatch, journal=1, snapshot=100)
    assert k._journal_worth_compacting() is False


def test_empty_journal_is_never_worth_compacting(monkeypatch):
    _sizes(monkeypatch, journal=0, snapshot=410_000_000)
    assert k._journal_worth_compacting() is False


def test_unmeasurable_snapshot_fails_safe_and_still_compacts(monkeypatch):
    """If the snapshot size cannot be read we must NOT skip persistence.

    Same safety default as `_save`'s "no declared record => full rewrite": when
    we cannot measure, do the durable thing. Skipping a compaction on an
    unknown is how data quietly stops being written.
    """
    monkeypatch.setattr(k, "_journal_size", lambda: 1)
    monkeypatch.setattr(k, "_snapshot_size", lambda: -1)   # unreadable
    assert k._journal_worth_compacting() is True


def test_the_age_trigger_is_gated_by_the_rule():
    """Wiring: the gate must actually sit on `_age_due`, not merely exist."""
    import inspect

    src = inspect.getsource(k._flush_to_disk_locked)
    assert "_journal_worth_compacting()" in src, (
        "the minimum-journal rule is not wired into the compaction decision — "
        "an unused guard is the C-101 shape"
    )
    # The bounds that must NOT be gated: these persist or bound replay.
    for unconditional in ("final", "_needs_compaction", "_journal_due"):
        assert unconditional in src, f"{unconditional} trigger disappeared"


def test_journal_size_bound_still_forces_compaction(monkeypatch):
    """`_journal_due` is what bounds boot replay and must be independent of the rule."""
    import inspect

    src = inspect.getsource(k._flush_to_disk_locked)
    assert "_journal_size() >= JOURNAL_MAX_BYTES" in src, (
        "the replay bound was removed — a journal could then grow without limit "
        "and boot replay with it"
    )
