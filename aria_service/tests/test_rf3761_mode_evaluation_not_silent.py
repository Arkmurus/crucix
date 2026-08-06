"""R-F3761 — CAPABILITY: the only route out of DEGRADED must not fail silently.

`ecosystem_reassess` calls `operating_modes.evaluate_auto_transition()` — the ONLY
thing that returns the platform to NORMAL. Its handler was
`except Exception: logger.debug(...)`. DEBUG is not emitted at the running log
level, so a failure there was invisible.

That matters because DEGRADED suppresses ALL external delivery
(`should_deliver_external` returns `mode == NORMAL`). A silent failure means
customer-facing output stays off and nothing, anywhere, says why.

Measured 2026-08-06: aria-intel sat DEGRADED from 2026-08-05T18:00Z. Driving the
task on demand returned status=ok in 1.3s and did NOT transition — no history
entry, no log line, no signal. The transition logic is sound (grounded_rate None
-> 1.0 -> target NORMAL != current DEGRADED -> set_mode), so something threw and
landed in that discarded handler. 26 health samples over 78 minutes confirmed it
was stuck rather than merely unscheduled.

Run: python -m pytest aria_service/tests/test_rf3761_mode_evaluation_not_silent.py -v
"""
from __future__ import annotations

from ._source_probe import function_source


def _src() -> str:
    from aria_service.autonomous import tasks
    return function_source(tasks, "_run_tool")


def _block() -> str:
    """The mode-evaluation handler, wherever it sits in the dispatcher."""
    from aria_service.autonomous import tasks
    from ._source_probe import module_source
    s = module_source(tasks)
    i = s.find("evaluate_auto_transition")
    assert i > 0, "the mode-evaluation call disappeared from tasks.py"
    return s[max(0, i - 1800): i + 2200]


def test_a_failed_mode_evaluation_is_not_logged_at_debug():
    """THE DEFECT: debug is not emitted, so the failure did not exist."""
    b = _block()
    assert "logger.debug(\"operating mode evaluation failed" not in b, (
        "the mode-evaluation failure is back at DEBUG level. It is the only route "
        "out of DEGRADED, and DEGRADED suppresses all external delivery — a "
        "failure here must never be invisible."
    )


def test_a_failed_mode_evaluation_is_reported_as_an_error():
    b = _block()
    assert "logger.error" in b, "the failure path no longer logs at ERROR"
    assert "R-F3761" in b


def test_the_failure_is_wired_to_the_brain():
    """§21a — losing the platform's only recovery path must reach a sink."""
    b = _block()
    assert "wire_failure" in b, (
        "a failure that keeps the platform in DEGRADED reaches no brain sink"
    )


def test_the_outcome_is_returned_in_the_report():
    """Diagnosis must not require log access at a level nobody runs at.

    /autonomous/run-now returns this report, so the operator sees the outcome in
    the HTTP response — which is how this defect was chased in the first place.
    """
    b = _block()
    assert "mode_evaluation_error" in b, (
        "a failure is not surfaced in the task report, so run-now still returns "
        "status=ok with no indication the mode evaluation blew up"
    )
    assert "mode_evaluated" in b, (
        "the no-change case is not recorded, so 'evaluated, nothing to do' is "
        "still indistinguishable from 'never ran' — the ambiguity that cost 78 "
        "minutes of watching"
    )
