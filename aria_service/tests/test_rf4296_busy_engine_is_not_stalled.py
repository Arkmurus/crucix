"""R-F4296 / C-250 — a BUSY autonomous engine is not a STALLED one.

Measured live 2026-08-24 15:07 on `GET /health`:

    status            degraded
    degraded_reasons  ['autonomous_loop_stalled', 'llm_vendor_credit_low_deepseek']
    autonomous        enabled: true, running: true,
                      seconds_since_last_tick: 216, tasks_loaded: 98

Sampled six times over the next four minutes the same field read
25 / 5 / 46 / 26 / 6 / 46 s — a clean ~60 s cadence — and the reason cleared
itself. The engine was never stalled; it had been executing a task.

THE MECHANISM. `_last_tick_at` is set once per polling-loop iteration, at the top
of the `while True`. While `execute_task` is awaited, R-F3824's
`_heartbeat_during_task` keeps the REGISTRY heartbeat fresh every 60 s but never
touches `_last_tick_at`. So for a task lasting T seconds `/health` reads
`seconds_since_last_tick` climbing to T + 60 while the R-F1146 blackout detector
correctly reads healthy — and `main.py`'s rollup flips the PUBLIC status endpoint
to degraded at 180 s. `POLL_INTERVAL_SECONDS` is 60, so any task over three
minutes does it, and DD runs, research sweeps and the reading loop routinely do.

This is R-F3824's own defect surviving on the other surface. Its docstring states
the diagnosis exactly: "One signal was standing for two very different states,
'busy' and 'wedged'." It fixed the registry heartbeat and left this gauge with
the identical fault.

THE BOUND IS WHAT KEEPS THIS FALSIFIABLE. The fix is NOT a higher threshold —
that would blind the check for exactly as long as it un-blinds it (the band-aid
§1 forbids). Busy is healthy only within R-F3824's OWN `_TASK_HEARTBEAT_MAX_BUSY_S`
(900 s), reusing that constant rather than coining a second one so the two
surfaces cannot drift apart the way they just did. Beyond it, R-F3824 deliberately
stops ticking the heartbeat so "a task hung forever" is still detectable — so
beyond it a busy engine reports stalled, exactly as a wedged one does.

A guard that cannot fail is not a guard (R-F3858), so the stalled cases below are
pinned as hard as the healthy ones.
"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aria_service.autonomous import engine as eng  # noqa: E402

NOW = 1_800_000_000.0


def _status(**kw) -> dict:
    base = {
        "enabled": True, "running": True,
        "last_tick_at": NOW - 5.0,
        "busy_task_id": None, "busy_since": None,
    }
    base.update(kw)
    return base


# ── the constants are shared, not coined twice ─────────────────────────────

def test_the_busy_grace_reuses_rf3824s_own_bound() -> None:
    """Two constants for one bound is how the surfaces drifted in the first place."""
    assert eng.TASK_BUSY_GRACE_S == eng._TASK_HEARTBEAT_MAX_BUSY_S


def test_the_stall_threshold_is_named_not_a_literal() -> None:
    assert eng.TICK_STALL_AFTER_S >= eng.POLL_INTERVAL_SECONDS * 2, (
        "a stall threshold below two poll intervals fires on ordinary jitter")


# ── the capability test: the live false alarm ──────────────────────────────

def test_a_busy_engine_within_the_bound_is_HEALTHY() -> None:
    """THE MEASURED SYMPTOM. 216 s of tick age with a task executing was reported
    as a stalled loop on the public status endpoint."""
    st = _status(last_tick_at=NOW - 216.0, busy_task_id="dd_report", busy_since=NOW - 216.0)
    assert eng.autonomy_is_healthy(st, now=NOW) is True


def test_a_long_but_plausible_task_is_still_healthy() -> None:
    st = _status(last_tick_at=NOW - 800.0, busy_task_id="deep_research", busy_since=NOW - 800.0)
    assert eng.autonomy_is_healthy(st, now=NOW) is True


# ── the guard must still FAIL — a wedge is not excused by "busy" ───────────

def test_a_task_beyond_the_bound_IS_stalled() -> None:
    """Past R-F3824's bound the heartbeat deliberately stops, so a hung task must
    surface here too. Otherwise 'busy' becomes an unfalsifiable excuse."""
    st = _status(last_tick_at=NOW - 1200.0, busy_task_id="wedged", busy_since=NOW - 1200.0)
    assert eng.autonomy_is_healthy(st, now=NOW) is False


def test_an_idle_engine_with_a_stale_tick_IS_stalled() -> None:
    """The original signal, unchanged: not busy, not ticking -> stalled."""
    st = _status(last_tick_at=NOW - 400.0)
    assert eng.autonomy_is_healthy(st, now=NOW) is False


def test_a_stopped_engine_IS_stalled_even_while_marked_busy() -> None:
    """A dead loop that never cleared its busy marker must not read healthy."""
    st = _status(running=False, busy_task_id="orphan", busy_since=NOW - 10.0)
    assert eng.autonomy_is_healthy(st, now=NOW) is False


def test_a_disabled_engine_is_not_a_fault() -> None:
    assert eng.autonomy_is_healthy(_status(enabled=False, running=False,
                                           last_tick_at=NOW - 99999.0), now=NOW) is True


def test_a_missing_tick_is_not_a_stall() -> None:
    """A freshly-started engine has no tick yet — unknown is not measured-failure."""
    assert eng.autonomy_is_healthy(_status(last_tick_at=None), now=NOW) is True


def test_junk_busy_state_never_grants_health() -> None:
    """A malformed busy marker must not become a free pass past the stall check."""
    for junk in ("soon", -1.0, float("nan"), NOW + 10_000.0):
        st = _status(last_tick_at=NOW - 400.0, busy_task_id="x", busy_since=junk)
        assert eng.autonomy_is_healthy(st, now=NOW) is False, junk


# ── the engine must actually REPORT the state, or the verdict reads nothing ─

def test_the_status_snapshot_carries_the_busy_state() -> None:
    st = eng.get_engine_status()
    assert "busy_task_id" in st and "busy_since" in st, (
        "get_engine_status() cannot express 'enabled and busy' — the third state")


def test_the_loop_records_busy_around_the_task() -> None:
    """A capability nothing sets is the R-F3099 shape: declared, never populated."""
    src = (ROOT / "aria_service/autonomous/engine.py").read_text(encoding="utf-8")
    assert "_busy_since" in src and "_busy_task_id" in src
    assert "finally" in src, "busy state must be cleared in a finally, or a raising task pins it"


# ── one measure, not a third fork (the §1 / R-F2639 rule) ──────────────────

def test_health_consumes_the_canonical_measure() -> None:
    """main.py must not keep its own copy of the rule — that fork is the defect."""
    src = (ROOT / "aria_service/main.py").read_text(encoding="utf-8")
    assert "autonomy_is_healthy" in src, "/health does not call the canonical measure"
    assert 'seconds_since_last_tick"] < 180' not in src, (
        "the inline 180s rule is still in main.py — two measures will drift again")


def test_health_publishes_the_busy_state_for_observers() -> None:
    src = (ROOT / "aria_service/main.py").read_text(encoding="utf-8")
    assert "busy_with" in src or "busy_seconds" in src, (
        "an observer still cannot tell 'busy' from 'stuck' on /health")
