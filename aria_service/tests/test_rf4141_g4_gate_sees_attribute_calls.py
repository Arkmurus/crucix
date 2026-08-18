"""R-F4141 (C-171) — the G4 on-loop vaccine matched only BARE names, so it was
blind to every real call site, and nine O(corpus) scans were running on the
event loop behind a green test.

R-F1910 built the G4 vaccine for exactly this failure class ("sync CPU on the
single event loop"). Its visitor:

```python
if (self.async_depth > 0
        and isinstance(func, ast.Name)      # <-- bare `fn(...)` ONLY
        and func.id in DENYLIST):
```

**Nothing in this tree is written that way.** Every call site is
module-qualified — `knowledge.search_knowledge(...)`,
`_kb.search_fact_records(...)`, `_pv.verify_premises(...)` — all `ast.Attribute`.
So the guard could not fire on real code from the day it shipped.

Its self-test hid that: `test_guard_actually_detects_a_violation` proved the
guard worked using a synthetic **bare** call, certifying it against a form that
does not occur. A guard that cannot fire, plus a self-test that cannot notice,
is the register's most-repeated defect — an absence rendered as a measurement.

**What it was hiding.** With the visitor matching attributes and the denylist
widened to `search_fact_records` and `verify_premises`, the repo-wide scan
found **nine** inline scans in async functions, each ~2.28s against 570,254
facts:

```
aria_service\\intel\\local_brain.py:679
aria_service\\intel\\memory_diagnostics.py:108
aria_service\\intel\\signal_correlator.py:874
aria_service\\routes\\aria.py:10128, 10874, 19234, 19839, 19844, 20354
```

All nine now go through `asyncio.to_thread`. The gate itself lives in
`test_g4_no_sync_cpu_on_loop.py`; this file covers the behavioural half — the
second, separate defect found at one of those nine.

**`signal_correlator:874` carried an always-zero bug on the same line.**
`search_knowledge` returns a formatted STRING, never a list — its own docstring
says so, and `routes/aria.py:10120` carries a 2026-04-21 comment about this
exact confusion. So:

```python
facts = knowledge.search_knowledge(country)
fact_count = len(facts) if isinstance(facts, list) else 0     # ALWAYS 0
```

The knowledge component never contributed its 0.2, silently capping coverage
confidence at 0.8, and `breakdown["knowledge_facts"]` always reported 0. Same
shape as C-169, where a wrong assumption about this module's API capped resolver
confidence at 0.5. Fixed by calling `search_fact_records`, which returns a list.
"""
from __future__ import annotations

import asyncio
import threading

import pytest

from aria_service.intel import knowledge as k
from aria_service.intel import signal_correlator as sc


@pytest.fixture(autouse=True)
def _corpus(monkeypatch):
    facts = [
        {"id": f"a{i}", "topic": "Angola procurement",
         "content": f"Angola defence procurement record {i}",
         "accessCount": 0, "confidence": 0.9, "createdAt": "2026-01-01"}
        for i in range(6)
    ]
    monkeypatch.setattr(k, "_cache", {"facts": facts}, raising=False)
    k._search_lc.clear()
    monkeypatch.setattr(k, "_search_lc_facts_id", 0, raising=False)
    yield


def test_knowledge_facts_are_actually_counted():
    """The always-zero bug. Before the fix `isinstance(str, list)` was False for
    every corpus that has ever existed, so this was structurally 0."""
    out = asyncio.run(sc.assess_coverage_confidence("Angola"))
    assert isinstance(out, dict), out
    got = (out.get("breakdown") or {}).get("knowledge_facts")
    assert got, (
        f"knowledge_facts is {got!r} with 6 matching facts in the store — the "
        "knowledge component of coverage confidence is still structurally zero")


def test_the_knowledge_component_can_lift_the_score():
    """It was worth 0.2 and contributed none of it, capping the score at 0.8.
    Compare against a store where nothing matches, so this asserts a
    DIFFERENCE rather than an absolute the rest of the score could satisfy."""
    with_facts = asyncio.run(sc.assess_coverage_confidence("Angola"))
    empty = asyncio.run(sc.assess_coverage_confidence("Zzqqxxvv"))
    assert with_facts.get("score", 0) > empty.get("score", 0), (with_facts, empty)


def test_the_scan_does_not_run_on_the_event_loop(monkeypatch):
    """The C-171 half. R-F4137's instrument measured signal_correlator as the
    sole on-loop caller after C-170: 11 calls, 13.99s, all on-loop."""
    seen: list[bool] = []

    def _probe(query, limit=10):
        seen.append(threading.current_thread() is threading.main_thread())
        return []

    monkeypatch.setattr(k, "search_fact_records", _probe, raising=True)
    asyncio.run(sc.assess_coverage_confidence("Angola"))
    assert seen, "the knowledge tier was not consulted at all"
    assert not any(seen), "the O(corpus) scan still runs on the event loop"


def test_a_failing_knowledge_tier_is_still_non_fatal(monkeypatch):
    """The original code swallowed errors into breakdown["knowledge_facts"]=0.
    to_thread re-raises in the awaiting task, so the except must still cover
    it — otherwise this fix converts a degraded score into a 500."""
    def boom(query, limit=10):
        raise RuntimeError("knowledge exploded")
    monkeypatch.setattr(k, "search_fact_records", boom, raising=True)
    out = asyncio.run(sc.assess_coverage_confidence("Angola"))
    assert isinstance(out, dict)
    assert (out.get("breakdown") or {}).get("knowledge_facts") == 0, out


def test_search_knowledge_really_does_return_a_string():
    """The premise behind the always-zero bug, pinned. If this ever starts
    returning a list, re-read C-171 before 'simplifying' the caller back."""
    out = k.search_knowledge("Angola")
    assert isinstance(out, str), type(out)


def test_search_fact_records_really_does_return_a_list():
    """And the premise behind the fix."""
    out = k.search_fact_records("Angola", limit=5)
    assert isinstance(out, list) and out, out
