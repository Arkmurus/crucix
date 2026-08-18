"""R-F4148 (C-174) — the amplification alert fired on every long-lived process,
and its landing in the ledger had never actually been observed.

Two defects, found by asking "is this delivering what it is supposed to?"
instead of assuming a green unit test meant yes.

### 1. A cumulative threshold cannot detect a rate

The trigger was `calls >= 500 or seconds >= 120.0`. Both are **cumulative
counters that only grow**, so given enough uptime EVERY process crosses them
whatever it is doing.

Measured normal load on aria-intel: **~28s of ranking per hour**. The 120s bar is
therefore reached at ~4.3 hours of uptime on completely ordinary traffic — and
the live 8h reading confirms it fired exactly that way:

```
total_calls=99  total_seconds=225.8   announced=True
   to_thread:autonomous_research  calls=86  secs=186.55
```

99 calls / 225.8s over 28,956s is **0.78% duty** — the normal rate this alert
exists to distinguish itself FROM. So it announced once per process and told
nobody anything, into the 500-slot capability ledger that §28 records being
filled by precisely this kind of flood.

**A guard that always fires is as useless as one that never does, and costs
more.** It is the same defect as an absence rendered as a measurement, inverted:
a presence that carries no information.

The honest measure is the **fraction of a caller's observed window** spent
scanning. Live normal: 0.78%. A research storm doing 500 scans in half an hour:
~69%. Two orders of magnitude apart, which is what makes a threshold between
them mean something. Three floors must hold together so a brand-new process
whose first call happens to be slow (5s inside the first 10s = 50% duty) cannot
trip it.

### 2. Nobody had ever seen the gap land

The existing coverage monkeypatched `wire_failure` and asserted it was called
once. That proves the CALLER, not the SINK. `wire_failure` dispatches
`capability_gaps.record_gap` through `_dispatch_fire_and_forget` — a task on the
running loop, or a daemon thread when there is none — and none of that was
exercised. The live ledger showed `ranking_amplification: 0`, which was
consistent with both "correctly quiet" and "silently broken".

The last test here drives the REAL path end to end: no patching of
`wire_failure`, no patching of `record_gap` — only the store is redirected, and
the gap is then read back out of it.
"""
from __future__ import annotations

import asyncio

import pytest

from aria_service.intel import knowledge as k


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


def _capture(monkeypatch) -> list[dict]:
    calls: list[dict] = []
    import aria_service.intel.engine_wiring as ew
    monkeypatch.setattr(ew, "wire_failure", lambda **kw: calls.append(kw), raising=True)
    return calls


def test_normal_production_load_does_NOT_announce(monkeypatch):
    """The headline regression: the exact live numbers that used to fire.

    99 calls / 225.8s over a 28,956s window = 0.78% duty. Under the old
    cumulative rule the 120s bar had long since been crossed and the alert had
    already announced. It must now stay silent."""
    calls = _capture(monkeypatch)
    monkeypatch.setattr(k, "_rank_stats", {
        "to_thread:autonomous_research": {
            "calls": 99, "seconds": 225.8, "facts_scanned": 0,
            "max_seconds": 5.63, "on_loop_calls": 0, "on_loop_seconds": 0.0,
            "first_at": k.time.monotonic() - 28_956,
        }
    }, raising=False)
    k._record_rank_call("to_thread:autonomous_research", k.time.perf_counter(), 0)
    assert calls == [], (
        "normal 0.78%-duty production traffic still announces — the alert is "
        "noise, exactly as it was before C-174")


def test_a_genuine_storm_DOES_announce(monkeypatch):
    """The other side, or the test above would pass on an alert that never
    fires at all (R-F3858). 500 scans in half an hour is ~69% duty."""
    calls = _capture(monkeypatch)
    monkeypatch.setattr(k, "_rank_stats", {
        "to_thread:autonomous_research": {
            "calls": 500, "seconds": 1250.0, "facts_scanned": 0,
            "max_seconds": 4.0, "on_loop_calls": 0, "on_loop_seconds": 0.0,
            "first_at": k.time.monotonic() - 1800,
        }
    }, raising=False)
    k._record_rank_call("to_thread:autonomous_research", k.time.perf_counter(), 0)
    assert len(calls) == 1, "a 69%-duty storm did not announce"
    assert "%" in calls[0]["detail"], calls[0]["detail"]


def test_a_slow_first_call_in_a_young_process_does_not_announce(monkeypatch):
    """Why three floors and not just duty. One 5s call 10s into a process is
    50% duty and means nothing — it is a cold cache, not amplification."""
    calls = _capture(monkeypatch)
    monkeypatch.setattr(k, "_rank_stats", {
        "boot": {"calls": 1, "seconds": 5.0, "facts_scanned": 0,
                 "max_seconds": 5.0, "on_loop_calls": 0, "on_loop_seconds": 0.0,
                 "first_at": k.time.monotonic() - 10},
    }, raising=False)
    k._record_rank_call("boot", k.time.perf_counter(), 0)
    assert calls == [], "a single cold-cache call tripped the amplification alert"


def test_many_cheap_calls_do_not_announce(monkeypatch):
    """Volume alone is not the problem — GIL time is. 10,000 sub-millisecond
    calls total 10s and harm nobody, which is why the count trigger was removed
    rather than merely raised."""
    calls = _capture(monkeypatch)
    monkeypatch.setattr(k, "_rank_stats", {
        "chatty": {"calls": 10_000, "seconds": 10.0, "facts_scanned": 0,
                   "max_seconds": 0.01, "on_loop_calls": 0, "on_loop_seconds": 0.0,
                   "first_at": k.time.monotonic() - 3600},
    }, raising=False)
    k._record_rank_call("chatty", k.time.perf_counter(), 0)
    assert calls == [], "10k cheap calls announced — the alert is keying on volume"


def test_duty_is_measured_PER_CALLER_not_per_process(monkeypatch):
    """A caller that starts an hour in must not have its duty diluted by an
    hour it did not exist. `first_at` is per row for exactly this reason."""
    calls = _capture(monkeypatch)
    monkeypatch.setattr(k, "_rank_stats", {
        "old_quiet": {"calls": 30, "seconds": 61.0, "facts_scanned": 0,
                      "max_seconds": 3.0, "on_loop_calls": 0,
                      "on_loop_seconds": 0.0,
                      "first_at": k.time.monotonic() - 36_000},
        "new_hot": {"calls": 30, "seconds": 200.0, "facts_scanned": 0,
                    "max_seconds": 9.0, "on_loop_calls": 0, "on_loop_seconds": 0.0,
                    "first_at": k.time.monotonic() - 300},
    }, raising=False)
    k._record_rank_call("new_hot", k.time.perf_counter(), 0)
    assert len(calls) == 1 and "new_hot" in calls[0]["detail"], calls


def test_the_duty_cycle_is_reported_so_a_reader_can_see_why(monkeypatch):
    """The number the alert keys on must be visible, or the next reader
    reverse-engineers it from a cumulative total and gets it wrong."""
    monkeypatch.setattr(k, "_rank_stats", {
        "x": {"calls": 4, "seconds": 2.0, "facts_scanned": 0, "max_seconds": 1.0,
              "on_loop_calls": 0, "on_loop_seconds": 0.0,
              "first_at": k.time.monotonic() - 100},
    }, raising=False)
    row = k.ranking_stats()["callers"]["x"]
    assert 0.015 < row["duty"] < 0.025, row
    assert row["window_s"] >= 99, row


def test_END_TO_END_the_gap_actually_LANDS_in_the_real_ledger(monkeypatch):
    """The question this whole file exists to answer: does it deliver?

    Everything else patches `wire_failure` and asserts the CALLER. That proves
    nothing about the SINK — `wire_failure` dispatches
    `capability_gaps.record_gap` through `_dispatch_fire_and_forget`, and the
    live ledger reading of `ranking_amplification: 0` was equally consistent
    with "correctly quiet" and "silently broken".

    Here nothing on the path is patched. The real `_record_rank_call` calls the
    real `wire_failure`, which schedules the real `record_gap`, and the gap is
    read back out of the store afterwards.
    """
    from aria_service.intel import capability_gaps as cg

    stored: list[dict] = []

    async def _fake_record(gap_type, detail, **kw):
        stored.append({"gap_type": gap_type, "detail": detail, **kw})
        return {"ok": True}

    # Redirect ONLY the terminal write. wire_failure, its dual-sink logic and
    # the fire-and-forget dispatch all run for real.
    monkeypatch.setattr(cg, "record_gap", _fake_record, raising=True)

    async def drive():
        monkeypatch.setattr(k, "_rank_stats", {
            "to_thread:autonomous_research": {
                "calls": 500, "seconds": 1250.0, "facts_scanned": 0,
                "max_seconds": 4.0, "on_loop_calls": 0, "on_loop_seconds": 0.0,
                "first_at": k.time.monotonic() - 1800,
            }
        }, raising=False)
        k._record_rank_call("to_thread:autonomous_research",
                            k.time.perf_counter(), 0)
        # let the scheduled task run
        for _ in range(20):
            await asyncio.sleep(0.01)
            if stored:
                break

    asyncio.run(drive())

    assert stored, (
        "the gap never reached capability_gaps.record_gap — the announcement "
        "is wired to nothing")
    got = stored[0]
    assert got["gap_type"] == "ranking_amplification", got
    assert "autonomous_research" in got["detail"], got
    assert "%" in got["detail"], got
