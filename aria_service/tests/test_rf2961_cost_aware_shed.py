"""R-F2961 (B3) — cost-aware graceful shed.

should_shed_paid() lets learning stay ON but drop the PAID Brave escalation when
the day's LLM spend nears the cap — reusing autonomous.safety's authoritative
daily-spend counter. This is the graceful-degrade that removes the reason the
learning feeds were ever hard-turned-OFF for cost.
"""
from __future__ import annotations

import asyncio
from unittest import mock

# R-F3757/§16 — NOT inspect.getsource: it slices at line numbers captured
# AT IMPORT, so an edit mid-run silently returns a DIFFERENT function's body.
from ._source_probe import function_source


def _run_cost_pressure(spent, cap, *, enabled=True):
    from aria_service.intel import load_governor as lg

    async def fake_check_cost_cap():
        return (spent < cap, spent)

    with mock.patch("aria_service.autonomous.safety.check_cost_cap", side_effect=fake_check_cost_cap), \
         mock.patch("aria_service.autonomous.safety.DAILY_COST_CAP_USD", cap), \
         mock.patch.dict("os.environ", {"ARIA_LOAD_GOVERNOR_ENABLED": "1" if enabled else "0"}, clear=False):
        return asyncio.run(lg.cost_pressure())


def _run_should_shed_paid(spent, cap, *, enabled=True, threshold="0.8"):
    from aria_service.intel import load_governor as lg

    async def fake_check_cost_cap():
        return (spent < cap, spent)

    with mock.patch("aria_service.autonomous.safety.check_cost_cap", side_effect=fake_check_cost_cap), \
         mock.patch("aria_service.autonomous.safety.DAILY_COST_CAP_USD", cap), \
         mock.patch.dict("os.environ",
                         {"ARIA_LOAD_GOVERNOR_ENABLED": "1" if enabled else "0",
                          "ARIA_PAID_SHED_THRESHOLD": threshold}, clear=False):
        return asyncio.run(lg.should_shed_paid())


def test_rf2961_cost_pressure_is_spent_over_cap():
    assert _run_cost_pressure(25.0, 50.0) == 0.5
    assert _run_cost_pressure(0.0, 50.0) == 0.0
    assert _run_cost_pressure(60.0, 50.0) == 1.0  # clamped at 1.0


def test_rf2961_shed_paid_fires_above_threshold():
    """At/above 80% of the daily cap, paid escalation sheds."""
    assert _run_should_shed_paid(45.0, 50.0) is True   # 0.9 >= 0.8
    assert _run_should_shed_paid(40.0, 50.0) is True   # 0.8 >= 0.8


def test_rf2961_shed_paid_off_below_threshold():
    """Plenty of budget → do NOT shed; Brave escalation runs."""
    assert _run_should_shed_paid(10.0, 50.0) is False  # 0.2 < 0.8


def test_rf2961_shed_paid_failsafe_when_governor_disabled():
    """Governor disabled → never shed (fail-safe: a bug can't starve learning)."""
    assert _run_should_shed_paid(49.0, 50.0, enabled=False) is False


def test_rf2961_cost_pressure_failsafe_on_zero_cap():
    """A zero/misconfigured cap must report 0.0 pressure, not divide-by-zero."""
    assert _run_cost_pressure(10.0, 0.0) == 0.0


def test_rf2961_student_loop_gates_brave_on_paid_shed():
    """The student loop's Pass-2 Brave escalation is gated by `not _paid_shed`
    (the free Pass-1 is not) — verified at the source so the wire can't silently
    regress."""
    import inspect
    from aria_service.intel import student
    src = function_source(student, "_study_weak_regional_cells")
    assert "should_shed_paid()" in src
    assert "and not _paid_shed" in src, "Brave escalation must be gated by the cost-shed flag"
