"""R-F2847 — the adverse-media budget must be arithmetically satisfiable.

THE DEFECT. run_adverse_media_deep_search ran up to 30 templates against a 180s
deadline with a hardcoded 10.0s per-search bound. Phase-timed on the brain
(R-F2846), a real search costs ~12.9s. So:

    30 templates x ~13s = ~390s   vs   a 180s deadline      -> impossible
    per-search bound 10.0s        vs   measured cost 12.9s  -> every search times out

The live SOCAR run recorded exactly that: templates_run 18, `templates_searched: 0`,
`search_backends_answered: False`. Adverse media — 20% of the decision scorecard —
returned ZERO evidence on every run, not because the sources were empty but because
the budget could never be met.

THE FIX IS NOT A BIGGER TIMEOUT. §1 forbids a band-aid, and raising 10s alone would
still leave 30 x 13s against 180s. The structural error is that the template count and
the per-search bound had NO relationship to the deadline they had to fit inside. So:

  * the per-search bound is set from MEASURED cost plus headroom, not from a number
    that sits below what a search actually takes;
  * the number of templates ATTEMPTED is derived from the deadline — we run what can
    actually COMPLETE rather than starting work we know will be cut off.

HONESTY IS THE POINT, not throughput. Completing ~9 of 34 templates and saying so is
strictly better than attempting 34 and completing 0: R-F2791's `templates_searched` /
`search_backends_answered` fields exist precisely so a zero-finding sweep can be
distinguished from a sweep that never ran. This change turns adverse media from
"no evidence, cause unknown" into "partial evidence, extent stated".

The cap must be VISIBLE (`templates_capped_at`, `templates_total_in_set`) — a silently
truncated sweep would read as a completed one, which is the false-clean family.
"""
import inspect

import pytest

from aria_service.intel import researcher as R

# R-F3772/§16 — NOT inspect.getsource: it slices at line numbers captured AT
# IMPORT, so a mid-run edit silently returns a DIFFERENT function's body. A CLASS
# target scopes the lookup to that class's own body (R-F3771).
from ._source_probe import function_source


def test_the_per_search_bound_is_not_below_measured_cost():
    """A bound beneath the real cost guarantees 100% timeouts."""
    assert hasattr(R, "ADVERSE_SEARCH_TIMEOUT_S"), (
        "the per-search bound must be a named, calibratable constant — it was "
        "hardcoded at 10.0s, below the measured ~12.9s, so nothing could complete"
    )
    # In-app distribution (measured in-container, adverse-media call shape):
    # 24.73 / 4.47 / 1.92 / 4.64 s -> median 4.55s, max 24.73s. The bound is a HANG
    # guard, so it must clear the observed MAX, not the median.
    assert R.ADVERSE_SEARCH_TIMEOUT_S >= 25.0, (
        f"bound {R.ADVERSE_SEARCH_TIMEOUT_S}s does not clear the measured in-app max "
        "(24.73s); a bound inside the variance cuts legitimate searches"
    )
    # And it must stay under the caller's wait_for backstop: deadline(180)+bound<210.
    assert R.ADVERSE_SEARCH_TIMEOUT_S <= 30.0, (
        "bound must leave the 210s backstop room: 180 + bound < 210"
    )


def test_the_deadline_check_is_what_bounds_the_sweep():
    """No count cap — the calibrated bound plus the existing deadline check suffice.

    A first attempt DID add a derived cap (deadline / timeout). It was over-
    conservative: sizing on the timeout CEILING rather than expected cost, it cut
    R-F2791's legitimate 4-template/30s sweep down to 1 and, via `partial`, stopped a
    working screen ever counting as evidence. With the bound now ABOVE measured cost,
    searches complete and R-F2667's pre-existing deadline check stops the loop when
    the budget is spent — which is the correct mechanism and was already there.
    """
    src = function_source(R, "run_adverse_media_deep_search")
    assert "deadline_s is not None" in src, (
        "the R-F2667 deadline check must remain — it is what bounds the sweep"
    )
    assert not hasattr(R, "affordable_template_count"), (
        "the derived count cap was abandoned; leaving it would invite reuse of a "
        "design the tests showed to be wrong"
    )


def test_the_loop_uses_the_calibrated_bound():
    """The calibrated bound must actually be wired in, not merely defined."""
    src = function_source(R, "run_adverse_media_deep_search")
    assert "ADVERSE_SEARCH_TIMEOUT_S" in src, (
        "the loop must use the calibrated bound, not a hardcoded 10.0"
    )
    assert "timeout=10.0" not in src, (
        "the hardcoded 10.0s bound — below measured cost — must be gone"
    )


def test_the_cap_is_reported_not_hidden():
    """A silently truncated sweep would read as a completed one."""
    src = function_source(R, "run_adverse_media_deep_search")
    for field in ("templates_capped_at", "templates_total_in_set", "templates_searched"):
        assert field in src, (
            f"{field} must be reported so the customer can see the sweep was bounded "
            "— an invisible cap is the false-clean family"
        )
