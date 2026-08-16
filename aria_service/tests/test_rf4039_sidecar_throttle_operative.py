"""R-F4039 (C-103) — the sidecar hedge throttle never throttled.

THE DEFECT. `_should_write_sidecar` is a crash hedge: outside a `final` flush it
should refresh the boot sidecar "at most once per SIDECAR_MIN_INTERVAL_S". But
the sidecar is only ever written from `_write_to_disk_atomic`, which C-95 made
COMPACTION-only, and:

    SIDECAR_MIN_INTERVAL_S = 600      # the throttle
    COMPACT_MAX_AGE_S      = 900      # the soonest the next call can arrive

A throttle shorter than its trigger's period can never fire. Every compaction
therefore paid a second full-graph write.

Measured live on aria-intel 2026-08-16, one compaction:

    aria_knowledge.json              410,841,606 B   13:35:49
    aria_knowledge.json.facts.jsonl  410,823,992 B   13:36:06   (+17s)

821 MB per compaction, and the IO sampler caught the cost: 6,573 ms then
10,330 ms of **FULL** io pressure across the two 30s windows spanning it —
`full` meaning every runnable task in the VM was blocked, which is exactly the
starved-event-loop signature (idle uvloop main thread, stale heartbeat, no
blocking Python frame). At ~96 compactions/day that is ~39 GB/day written for a
file that is read ONCE PER BOOT.

THE FIX is to encode the relationship rather than pick another magic number: the
hedge interval must never be shorter than the compaction cadence, or it silently
becomes a no-op again the next time either constant moves.

Skipping a sidecar write is SAFE BY CONSTRUCTION and the module says so: a stale
sidecar is detected by its marker and the reader falls back to the monolithic
load — the same route every fresh deploy already takes.
"""
from __future__ import annotations

import pytest

from aria_service.intel import knowledge as k


@pytest.fixture(autouse=True)
def _reset_sidecar_clock():
    prev = k._last_sidecar_write
    yield
    k._last_sidecar_write = prev


def test_hedge_interval_cannot_be_shorter_than_the_compaction_cadence():
    """The structural guard — this is what made the throttle inoperative."""
    assert k.SIDECAR_MIN_INTERVAL_S >= k.COMPACT_MAX_AGE_S, (
        f"sidecar hedge ({k.SIDECAR_MIN_INTERVAL_S}s) is shorter than the "
        f"compaction cadence ({k.COMPACT_MAX_AGE_S}s), so it can never throttle: "
        f"every compaction writes a second full-graph copy"
    )


def test_a_compaction_cadence_apart_does_not_rewrite_the_sidecar():
    """The behavioural contract: back-to-back compactions must not both write."""
    k._last_sidecar_write = 1000.0
    later = 1000.0 + k.COMPACT_MAX_AGE_S      # the very next compaction
    assert k._should_write_sidecar(final=False, now=later) is False, (
        "the next compaction rewrote the 410MB sidecar — the hedge is a no-op"
    )


def test_final_flush_always_writes():
    """Shutdown is the case the sidecar exists for — never throttle it."""
    k._last_sidecar_write = 1000.0
    assert k._should_write_sidecar(final=True, now=1000.1) is True


def test_first_write_in_a_process_still_happens():
    """The previous process's sidecar is stale; the first compaction must refresh it."""
    k._last_sidecar_write = None
    assert k._should_write_sidecar(final=False, now=1234.0) is True


def test_the_hedge_still_fires_eventually():
    """A throttle that never fires would be the opposite defect — no crash hedge at all."""
    k._last_sidecar_write = 1000.0
    assert k._should_write_sidecar(
        final=False, now=1000.0 + k.SIDECAR_MIN_INTERVAL_S
    ) is True
