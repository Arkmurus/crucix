"""R-F4137 (C-166) — the ranking instrument's first LIVE reading named nothing,
and the fix adds the one signal C-166 actually needs.

R-F4136 shipped and was read through the running server. It worked, and the
answer was useless:

```
total_calls 1  total_seconds 5.356  mean 5.3559
   concurrent.futures.thread    calls=1  secs=5.36  max=5.36
```

`concurrent.futures.thread` is the worker pool, not a caller. Most ranking call
sites go through `asyncio.to_thread(search_knowledge, ...)`, and
`search_knowledge` **is the to_thread target** — so by the time the scan runs,
the caller's frames are on a different thread and the walk legitimately finds
only pool plumbing. Exactly the "the caller's stack is gone" problem
`cost_tracker` documents for `record_call` inside `create_task`, arriving in the
instrument written to avoid guessing.

Two fixes, both measured before being relied on:

1. **A fallback that crosses the hop.** `asyncio.to_thread` runs its target
   inside `contextvars.copy_context()`, so a `feature()` scope set on the async
   side is visible in the worker. Verified directly:

   ```
   direct call         -> walk='__main__'  feature='uncategorized'
   to_thread, no scope -> feature='uncategorized'
   to_thread, scoped   -> feature='deep_researcher'      <-- survives
   ```

2. **An on-loop / off-loop split**, which is the signal C-166 needs and neither
   the totals nor the caller names provide. C-166 is about event-loop
   STARVATION: a scan on a worker thread starves the loop only through GIL
   contention, while a scan on the loop thread blocks it outright. Different
   severity, different fix. The loop runs on the main thread, so this is read
   directly rather than inferred.

The three attribution answers stay distinguishable on purpose — the useless one
must not be able to look like the useful one:

  * `<module>`               — a real, named, in-thread caller
  * `to_thread:<feature>`    — offloaded, identified by its cost scope
  * `to_thread:unattributed` — offloaded with no scope. Honest, never a guess.

That last one matters: collapsing it into `unknown`, or worse into a plausible
module name, would be the register's most-repeated defect — an absence rendered
as a measurement.

Note the 5.36s in that live reading is the BOOT WARMUP call, which also builds
the 568k-entry lowercase cache. It is not the steady-state per-call cost
(0.27-0.88s), and reading it as such would over-state the problem.
"""
from __future__ import annotations

import asyncio
import threading

import pytest

from aria_service.intel import knowledge as k
from aria_service.intel import cost_tracker as ct


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.setattr(k, "_rank_stats", {}, raising=False)
    monkeypatch.setattr(k, "_rank_amplification_announced", False, raising=False)
    facts = [{"id": f"f{i}", "topic": f"t{i}", "content": f"sanctions guidance {i}",
              "accessCount": 0, "confidence": 0.9, "createdAt": "2026-01-01"}
             for i in range(20)]
    monkeypatch.setattr(k, "_cache", {"facts": facts}, raising=False)
    k._search_lc.clear()
    monkeypatch.setattr(k, "_search_lc_facts_id", 0, raising=False)
    yield


def test_the_worker_pool_is_never_reported_as_the_caller():
    """The exact live symptom. `concurrent.futures.thread` names nothing, and
    while it sat in the `callers` map the instrument looked like it was working
    and answered nothing."""
    async def run():
        return await asyncio.to_thread(k.search_knowledge, "sanctions")
    asyncio.run(run())
    names = list(k.ranking_stats()["callers"])
    assert names, "nothing recorded at all"
    assert not any(n.startswith("concurrent.futures") or n.startswith("threading")
                   for n in names), names


def test_an_offloaded_call_is_identified_by_its_cost_scope():
    """The fallback that crosses the thread hop."""
    async def run():
        with ct.feature("deep_researcher"):
            return await asyncio.to_thread(k.search_knowledge, "sanctions")
    asyncio.run(run())
    assert "to_thread:deep_researcher" in k.ranking_stats()["callers"], \
        k.ranking_stats()["callers"]


def test_an_offloaded_call_with_no_scope_says_so_rather_than_guessing():
    """`uncategorized` is the cost module's default, i.e. NOT an identification.
    Rendering it as a caller name would be inventing one."""
    async def run():
        return await asyncio.to_thread(k.search_knowledge, "sanctions")
    asyncio.run(run())
    assert "to_thread:unattributed" in k.ranking_stats()["callers"], \
        k.ranking_stats()["callers"]


def test_an_in_thread_caller_is_still_named_directly():
    """The fallback must not swallow the case that already worked."""
    k.search_knowledge("sanctions")
    assert any("test_rf4137" in n for n in k.ranking_stats()["callers"]), \
        k.ranking_stats()["callers"]


def test_a_call_on_the_loop_thread_is_counted_as_on_loop():
    """The headline signal for C-166: seconds the loop was BLOCKED, not merely
    contended."""
    async def run():
        return k.search_knowledge("sanctions")     # deliberately NOT offloaded
    asyncio.run(run())
    s = k.ranking_stats()
    assert s["on_loop_calls"] == 1, s
    assert s["on_loop_seconds"] >= 0.0
    row = next(iter(s["callers"].values()))
    assert row["on_loop_calls"] == 1, row


def test_an_offloaded_call_is_NOT_counted_as_on_loop():
    """If everything counted as on-loop the split would certify nothing — the
    'guard that cannot fail' shape (R-F3858)."""
    async def run():
        return await asyncio.to_thread(k.search_knowledge, "sanctions")
    asyncio.run(run())
    s = k.ranking_stats()
    assert s["total_calls"] == 1 and s["on_loop_calls"] == 0, s


def test_the_two_are_distinguished_in_the_same_process():
    """Both in one reading, which is how production will present them."""
    async def run():
        k.search_knowledge("sanctions")                        # on loop
        await asyncio.to_thread(k.search_knowledge, "sanctions")  # offloaded
    asyncio.run(run())
    s = k.ranking_stats()
    assert s["total_calls"] == 2 and s["on_loop_calls"] == 1, s


def test_a_sync_call_from_a_plain_worker_thread_is_not_on_loop():
    """Not every non-main thread is asyncio's. A plain threading.Thread must
    also read as off-loop, or the split would over-report blockage."""
    out: list = []
    t = threading.Thread(target=lambda: out.append(k.search_knowledge("sanctions")))
    t.start(); t.join()
    assert k.ranking_stats()["on_loop_calls"] == 0, k.ranking_stats()


def test_a_broken_feature_reader_degrades_honestly(monkeypatch):
    """Fail-open, and never into a fabricated name."""
    monkeypatch.setattr(ct, "get_current_feature",
                        lambda: (_ for _ in ()).throw(RuntimeError("ctx gone")),
                        raising=True)

    async def run():
        return await asyncio.to_thread(k.search_knowledge, "sanctions")
    asyncio.run(run())
    assert "to_thread:unattributed" in k.ranking_stats()["callers"], \
        k.ranking_stats()["callers"]


def test_the_instrument_still_cannot_break_recall(monkeypatch):
    """Re-asserted after the rewrite — the property is easy to lose in an
    edit and expensive to lose in production."""
    monkeypatch.setattr(k, "_rank_caller",
                        lambda: (_ for _ in ()).throw(RuntimeError("probe died")),
                        raising=True)
    assert k.search_knowledge("sanctions")
