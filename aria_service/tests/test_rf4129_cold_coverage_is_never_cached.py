"""R-F4129 (C-164) — a coverage matrix built during boot was cached for an hour
and served as fact.

This is the root cause of the operator's "the heatmap is not displaying any
values", and R-F4128's instrument is what made it findable.

The sequence, all of it measured:

  1. aria-intel boots for ~10 minutes (§11c: ~223k facts loaded before /health
     goes green), during which `knowledge._cache["facts"]` is empty.
  2. Anything requesting `/api/aria/learning/coverage` in that window gets a
     matrix built from ZERO facts — 867 cells, 867 gaps, coverage_score 0.0.
  3. `_write_heatmap_redis_cache` persists it with **ex=3600**.
  4. For the next hour every caller is served that empty matrix, long after the
     facts finished loading. `from_cache: True`, and nothing says the reading was
     taken blind.

Eight deploys landed on 2026-08-17, each restarting the machine and opening a
fresh ten-minute poisoning window. Live evidence across the same day, same code
path: `populated_cells 0 / gap_count 867 / score 0.0` earlier, and
`populated_cells 282 / gap_count 585 / score 0.122` once an entry happened to be
built warm.

**The defect is not the emptiness — it is persisting it.** A build that saw no
facts is a measurement taken before the instrument was ready, and §1 has a name
for publishing those: an absence rendered as a measurement. The same shape as
C-152, where a store that could not be read was allowed to overwrite the durable
copy, and as R-F2664 before it.

So: a cold build is still SERVED (the caller asked, and returning nothing would
be worse) but it is never WRITTEN to the hour-long cache, and it is labelled so
the reader can tell. The next request after warmup rebuilds and caches a real
one.
"""
from __future__ import annotations

import pytest

from aria_service.intel import coverage_heatmap as ch


def _payload(facts_seen: int, cache_facts, populated: int = 0) -> dict:
    return {
        "matrix": {"d": {"j": {}}},
        "summary": {"cells": 867, "gap_count": 867 - populated,
                    "populated_cells": populated},
        "coverage_score": 0.0 if not populated else 0.12,
        "matcher_diagnostics": {
            "facts_seen": facts_seen,
            "knowledge_cache_facts": cache_facts,
        },
    }


def test_a_build_that_saw_no_facts_is_refused_by_the_cache():
    assert ch.is_cacheable(_payload(facts_seen=0, cache_facts=0)) is False, (
        "a matrix built from zero facts must never be persisted for an hour — "
        "that is how a boot-window reading becomes the answer all day")


def test_a_warm_build_is_cacheable():
    assert ch.is_cacheable(_payload(facts_seen=533_000, cache_facts=533_000,
                                    populated=282)) is True


def test_an_unmeasurable_diagnostic_does_not_block_caching():
    """`knowledge_cache_facts: None` means COULD NOT MEASURE. Refusing to cache
    on that would disable caching entirely on any deployment where the probe
    fails — a self-inflicted outage of the cache. Only a POSITIVE reading of
    zero facts blocks the write."""
    assert ch.is_cacheable(_payload(facts_seen=12, cache_facts=None)) is True


def test_a_payload_with_no_diagnostics_is_still_cacheable():
    """Back-compat: a payload from before R-F4128 carries no diagnostics block.
    Treating its absence as "cold" would refuse every legacy write."""
    assert ch.is_cacheable({"matrix": {"d": {}}, "summary": {}}) is True


def test_a_cold_build_is_labelled_so_the_reader_can_tell():
    """It is still SERVED — the caller asked, and returning nothing is worse —
    but it must not read as a settled measurement."""
    cold = _payload(facts_seen=0, cache_facts=0)
    ch.mark_cacheability(cold)
    assert cold.get("built_cold") is True, cold
    warm = _payload(facts_seen=99, cache_facts=99, populated=5)
    ch.mark_cacheability(warm)
    assert warm.get("built_cold") is False


def test_the_route_consults_the_guard_before_writing():
    """The guard is worthless if the write path does not call it."""
    import inspect
    from aria_service.routes import aria as a

    src = inspect.getsource(a.learning_coverage_ep)
    assert "is_cacheable" in src, (
        "learning_coverage_ep still writes every build to the 1h Redis cache")


def test_the_guard_can_fail():
    """A guard that cannot refuse is not a guard."""
    assert ch.is_cacheable(_payload(0, 0)) is False
    assert ch.is_cacheable(_payload(1, 1, populated=1)) is True
