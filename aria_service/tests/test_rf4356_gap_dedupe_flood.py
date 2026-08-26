"""R-F4356 (C-301) — a capability gap must not flood the ledger.

MEASURED LIVE on aria-intel 2026-08-26, in the boot window after the bc0164e5
deploy. One LLM outage episode wrote **56 `llm_provider_failure` gaps in 79
seconds** (09:06:42 → 09:08:01) — 81% of every gap recorded in that window and
~11% of the entire 500-slot ledger, from a single repeating fact.

TWO INDEPENDENT CAUSES, and the flood needs both fixed.

1. THE FINGERPRINT COULD NEVER MATCH ITSELF. `fallback.py:1217` builds
   ``detail=f"Provider {name} failed: kind={kind} failures={stats['failures']}
   error={error}"`` and ``failures`` is a MONOTONIC COUNTER, so
   ``_gap_fingerprint`` hashed a fresh string on every call and the 1h dedupe
   window was structurally unable to fire. Observed live: failures=45, 46, 47…

   This is R-F3695's defect in a second gap type. That fix added
   ``no_symbolic_rule`` to an allow-list; the list is still one entry long. An
   allow-list rots every time a new gap type is written, so this normalises
   volatile fields **by shape** — the next gap type is covered on the day it is
   authored, not the day someone notices the flood.

2. THE DEDUPE READ FAILED OPEN. Line 575 used non-strict ``rs.get``, whose
   None-on-error contract makes "the store timed out" indistinguishable from
   "no sentinel" — so a struggling store answers "not a duplicate" and the gap
   is written. Measured: 13 ``capability_gaps:dedupe:*`` reads timed out at
   09:06:38 and the burst began at 09:06:42. **A dedupe that fails open under
   load amplifies exactly the flood it exists to prevent** — the §17
   ``spent_usd: 0.0`` shape applied to a guard.

   On an unreadable store the gap is now DEFERRED, not written, and the reason
   is not squeamishness: the write path is ``lpush(critical=True)`` on the SAME
   store, and its failure branch logs at ERROR, which R-F3695 traced all the way
   to a Phase A gate #3 reset. Attempting the write when we already know the
   store is unhealthy converts a dedupe failure into a gate reset.
"""
from __future__ import annotations

import pytest

from aria_service.intel import capability_gaps as cg


# ── the real strings, copied off the wire ───────────────────────────────────
LIVE_DETAILS = [
    "Provider aria_llm failed: kind=server failures=45 "
    "error=[aria_llm] ARIA-LLM unavailable (cold/unproven or breaker OPEN)",
    "Provider aria_llm failed: kind=server failures=46 "
    "error=[aria_llm] ARIA-LLM unavailable (cold/unproven or breaker OPEN)",
    "Provider aria_llm failed: kind=server failures=47 "
    "error=[aria_llm] ARIA-LLM unavailable (cold/unproven or breaker OPEN)",
]


# ══ 1. the fingerprint ══════════════════════════════════════════════════════

def test_the_live_flood_collapses_to_one_fingerprint() -> None:
    """THE DEFECT. 56 gaps in 79s were 56 distinct fingerprints because of an
    incrementing counter. They describe one fact and must dedupe to one key."""
    prints = {cg._gap_fingerprint("llm_provider_failure", d) for d in LIVE_DETAILS}
    assert len(prints) == 1, (
        f"the counter still leaks into the dedupe key: {len(prints)} distinct "
        "fingerprints for one repeating failure")


def test_wall_clock_ceiling_variants_also_collapse() -> None:
    """The first half of the same episode carried a DURATION, which varies per
    attempt. Same fact, so same key."""
    a = "Provider aria_llm failed: kind=server failures=3 error=[aria_llm] attempt exceeded its 30s wall-clock ceiling"
    b = "Provider aria_llm failed: kind=server failures=9 error=[aria_llm] attempt exceeded its 45.5s wall-clock ceiling"
    assert cg._gap_fingerprint("llm_provider_failure", a) == \
           cg._gap_fingerprint("llm_provider_failure", b)


@pytest.mark.parametrize("a,b,why", [
    ("Provider aria_llm failed: kind=server failures=1 error=x",
     "Provider deepseek failed: kind=server failures=1 error=x",
     "different PROVIDER"),
    ("Provider aria_llm failed: kind=server failures=1 error=x",
     "Provider aria_llm failed: kind=timeout failures=1 error=x",
     "different failure KIND"),
    ("Provider aria_llm failed: kind=server failures=1 error=breaker OPEN",
     "Provider aria_llm failed: kind=server failures=1 error=auth rejected",
     "different ERROR text"),
])
def test_genuinely_different_gaps_do_not_collapse(a: str, b: str, why: str) -> None:
    """THE COUNTER-GUARD, and the one that keeps this honest. Over-normalising
    would merge distinct defects into a single entry and hide real signal —
    strictly worse than the flood, because the flood is at least visible."""
    assert cg._gap_fingerprint("llm_provider_failure", a) != \
           cg._gap_fingerprint("llm_provider_failure", b), \
        f"gaps that differ by {why} were collapsed into one fingerprint"


def test_hex_ids_and_timestamps_are_volatile_too() -> None:
    """Shape-based, so run ids and timestamps are covered without anyone
    remembering to add their gap type to a list."""
    t1 = "run 3fa9c1b28e4d5f60 failed at 2026-08-26T09:06:38Z"
    t2 = "run 88ce01aa77b93d12 failed at 2026-08-26T11:47:02Z"
    assert cg._gap_fingerprint("engine_failure", t1) == \
           cg._gap_fingerprint("engine_failure", t2)


def test_a_widening_counter_past_the_200_char_cut_still_collapses() -> None:
    """ORDER MATTERS: normalise BEFORE truncating.

    The fingerprint hashes only the first 200 chars. When the counter GROWS A
    DIGIT (9→10, 99→100) every following character shifts by one, so truncating
    first leaves two different tails and normalising them afterwards cannot undo
    it — the collapse silently stops working exactly as an episode gets long
    enough to matter. Live, the burst ran to failures=47; a longer one crosses
    99→100.
    """
    tail = " error=" + "x" * 200          # push the cut well past the counter
    a = f"Provider aria_llm failed: kind=server failures=9{tail}"
    b = f"Provider aria_llm failed: kind=server failures=10{tail}"
    assert cg._gap_fingerprint("llm_provider_failure", a) == \
           cg._gap_fingerprint("llm_provider_failure", b), \
        "a counter that gained a digit shifted the 200-char tail and defeated dedupe"


def test_normalisation_does_not_touch_the_stored_detail() -> None:
    """R-F3695's stated contract: only the dedupe KEY is collapsed; the full
    detail is still stored and still read by a human."""
    detail = LIVE_DETAILS[0]
    assert cg._normalise_for_fingerprint(detail) != detail, "fixture expects normalisation"
    assert "failures=45" in detail, "the caller's string must not be mutated"


# ══ 2. the dedupe read ══════════════════════════════════════════════════════

class _Store:
    """Minimal rs double: records what the gap path actually did."""

    def __init__(self, *, sentinel=None, raise_read=False):
        self._sentinel, self._raise_read = sentinel, raise_read
        self.lpush_calls: list = []
        self.set_calls: list = []

    async def get_strict(self, key):
        if self._raise_read:
            # the REAL class — the production code catches this one, so a double
            # raising a look-alike would prove nothing
            from aria_service.intel.redis_store import StoreReadError
            raise StoreReadError("state_store.get timed out after 5s")
        return self._sentinel

    async def get(self, key):
        return self._sentinel

    async def lpush(self, key, val, critical=False):
        self.lpush_calls.append(key)

    async def ltrim(self, *a, **k):
        pass

    async def set(self, key, val, ex=None):
        self.set_calls.append(key)


@pytest.mark.asyncio
async def test_unreadable_store_defers_instead_of_writing(monkeypatch) -> None:
    """THE AMPLIFIER. A store-read failure must not read as 'not a duplicate'.
    Writing here costs four ops on a store already timing out, and the write
    path's ERROR branch is R-F3695's route to a gate #3 reset."""
    store = _Store(raise_read=True)
    monkeypatch.setattr(cg, "rs", store, raising=False)
    monkeypatch.setattr(cg, "_UNREADABLE_ANNOUNCED", False, raising=False)

    out = await cg.record_gap("llm_provider_failure", LIVE_DETAILS[0])

    assert out.get("deferred") is True, "an unreadable store must defer the gap"
    assert not store.lpush_calls, "the gap was written despite an unreadable store"
    assert not store.set_calls, "a dedupe sentinel was set on an unreadable store"
    # additive, per R-F3703's convention — existing consumers still work
    assert out.get("type") == "llm_provider_failure"
    assert out.get("detail") == LIVE_DETAILS[0]


@pytest.mark.asyncio
async def test_a_readable_empty_store_still_records(monkeypatch) -> None:
    """THE GUARD. 'Readable and no sentinel' is the NORMAL path and must be
    untouched — deferring here would silently stop gap recording entirely."""
    store = _Store(sentinel=None)
    monkeypatch.setattr(cg, "rs", store, raising=False)

    out = await cg.record_gap("llm_provider_failure", LIVE_DETAILS[0])

    assert not out.get("deferred"), "a healthy store must not defer"
    assert store.lpush_calls, "the gap was not written on the healthy path"
    assert store.set_calls, "the dedupe sentinel was not set after a good write"


@pytest.mark.asyncio
async def test_deferrals_are_visible_on_the_consumed_surface(monkeypatch) -> None:
    """§21a — the defer counter must reach a surface something READS.

    `get_gap_summary` is what routes/aria.py consumes. Left in a module global,
    the count could tell nobody the ledger had gone lossy, and "0 gaps recorded"
    would be indistinguishable from "nothing went wrong" — the same
    absence-reads-as-health shape this fix exists to remove.
    """
    class _SummaryStore(_Store):
        async def lrange(self, *a, **k):
            return []

    store = _SummaryStore(raise_read=True)
    monkeypatch.setattr(cg, "rs", store, raising=False)
    monkeypatch.setattr(cg, "_UNREADABLE_ANNOUNCED", False, raising=False)
    monkeypatch.setattr(cg, "_deferred_counts", {}, raising=False)

    await cg.record_gap("llm_provider_failure", LIVE_DETAILS[0])
    await cg.record_gap("llm_provider_failure", LIVE_DETAILS[1])

    summary = await cg.get_gap_summary()
    assert summary["deferred_store_unreadable"] == {"llm_provider_failure": 2}


@pytest.mark.asyncio
async def test_an_existing_sentinel_still_dedupes(monkeypatch) -> None:
    """The dedupe itself must keep working — that is the point of all of this."""
    store = _Store(sentinel="1")
    monkeypatch.setattr(cg, "rs", store, raising=False)

    out = await cg.record_gap("llm_provider_failure", LIVE_DETAILS[0])

    assert out.get("deduped") is True
    assert not store.lpush_calls, "a deduped gap must not be written"
