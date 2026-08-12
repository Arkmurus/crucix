"""R-F3924 — a full gc.collect() ran ON the event loop, freeing 0.0MB every time.

MEASURED LIVE 2026-08-12, every pass across a 15-cycle window:

    RSS 6690.5MB exceeds threshold 6144MB — triggering GC
    GC freed 0.0MB (RSS: 6690.5MB → 6690.5MB)

TWO DEFECTS, and the history makes both worse.

1. IT BLOCKED THE LOOP. `gc.collect()` walks every tracked object. At 6.7GB live
   that is precisely the traversal R-F3920 refused to add to THIS SAME monitoring
   loop, and precisely the starvation class R-F2144/R-F2200 already paid for. It ran
   synchronously, every 5 minutes. Now `asyncio.to_thread`.

2. IT KEPT PAYING FOR A REMEDY THAT MEASURABLY DOES NOT WORK. `GC freed 0.0MB` means
   the retained memory is LIVE — reachable state — so collection cannot reclaim it by
   construction. R-F1332 recorded this EXACT symptom ("GC freed 0.0MB every 5min
   while RSS stayed at 2588.4MB") and added torch-cache clearing; it is back at
   6690MB. Repeating a proven no-op forever is the band-aid §1 forbids, and here it
   is not even free.

The backoff is driven by MEASUREMENT, not a guessed cooldown: it widens only after
collections have demonstrably reclaimed nothing, and resets the moment one works.
The transition is announced ONCE and wired to the brain (§21a) — "the memory is live,
collection cannot help" is what the coder and operator need, instead of an endless
`freed 0.0MB`.
"""
from __future__ import annotations

from aria_service.intel import memory_leak_detector as mld


def _d():
    return mld.MemoryLeakDetector()


def test_gc_runs_off_the_event_loop():
    """The safety property. A multi-GB heap walk must never block the loop."""
    from aria_service.tests._source_probe import function_source

    src = function_source(mld.MemoryLeakDetector, "run_forever")
    assert "asyncio.to_thread(gc.collect)" in src, (
        "gc.collect() must be offloaded — at 6.7GB it walks every tracked object on "
        "the monitoring loop (R-F2144/R-F2200 starvation class)")
    for line in src.splitlines():
        st = line.strip()
        if st.startswith("gc.collect()"):
            raise AssertionError(f"bare synchronous gc.collect on the loop: {st}")


def test_an_effective_collection_keeps_the_fast_cadence():
    """The control (R-F3858): backoff must not trigger when GC actually works."""
    d = _d()
    for _ in range(5):
        d._note_gc_outcome(50.0)
    assert d._ineffective_gc_runs == 0
    assert d._gc_interval_s() == d._GC_BASE_INTERVAL_S


def test_repeated_no_op_collections_back_off():
    """THE CAPABILITY: stop paying every 5 minutes for a proven no-op."""
    d = _d()
    for _ in range(d._GC_GIVE_UP_AFTER):
        d._note_gc_outcome(0.0)
    assert d._gc_interval_s() == d._GC_BACKOFF_INTERVAL_S


def test_one_effective_collection_restores_the_fast_cadence():
    """Backoff is a measured state, not a latch — if GC starts working again the
    detector must resume, or this becomes the stale-forever class."""
    d = _d()
    for _ in range(d._GC_GIVE_UP_AFTER + 2):
        d._note_gc_outcome(0.0)
    assert d._gc_interval_s() == d._GC_BACKOFF_INTERVAL_S

    d._note_gc_outcome(25.0)
    assert d._ineffective_gc_runs == 0
    assert d._gc_interval_s() == d._GC_BASE_INTERVAL_S


def test_the_transition_is_announced_exactly_once(monkeypatch):
    """An alarm every 5 minutes is an alarm that gets muted. The news is the
    TRANSITION to 'GC is the wrong remedy here'."""
    fired: list = []
    monkeypatch.setattr(mld, "wire_failure", lambda **kw: fired.append(kw))

    d = _d()
    for _ in range(d._GC_GIVE_UP_AFTER + 6):
        d._note_gc_outcome(0.0)

    assert len(fired) == 1, f"expected one signal on transition, got {len(fired)}"
    detail = fired[0]["detail"]
    assert "reachable" in detail, "the signal must say WHY collection cannot help"
    assert "census" in detail, "it must point at the diagnosis that can act (R-F3920)"


def test_the_signal_never_breaks_the_loop(monkeypatch):
    def _boom(**kw):
        raise RuntimeError("brain down")

    monkeypatch.setattr(mld, "wire_failure", _boom)
    d = _d()
    for _ in range(d._GC_GIVE_UP_AFTER):
        d._note_gc_outcome(0.0)      # must not raise
