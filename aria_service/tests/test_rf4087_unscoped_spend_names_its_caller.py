"""R-F4087 (C-134) — 53% of LLM spend was bucketed `uncategorized`.

Measured live on aria-intel 2026-08-17, month-to-date::

    uncategorized        46.2561   52.8%     <- the largest bucket by far
    self_improve         18.3593   21.0%
    research_extraction  13.3800   15.3%
    ...
    TOTAL                87.5727

360 of the last 1000 LLM calls carried no feature. The ledger record holds
`{cost_usd, feature, id, model, success, total_tokens, ts}` and **no caller
identity of any kind**, so the spend cannot be attributed retroactively — the
evidence was never written down. A cost meter that cannot say who spent the
majority of the money is the §1 "absence rendered as a measurement" shape
applied to the budget, and §17 records the consequence: the RULE ONE breach
that drained the Anthropic credit hid in `self_improve` + `uncategorized`.

**Why not a call-site sweep.** Adding `with feature(...)` at every LLM call site
is whack-a-mole — the ninth site re-opens it silently, which is exactly why
R-F3946 moved the Brave policy off a curated route list and onto the single
decision point. So attribution happens once, where the call is made.

**Why not inside `record_call`.** `metered._record_cost` fires it through
`asyncio.create_task(...)`, so by then the stack is the new task's and the
caller's frames are gone. The contextvar survives (create_task copies the
context) but the stack does not. The capture therefore happens at the entry of
`MeteredProvider.complete`/`.stream`, which run on the caller's own stack.

A real `feature()` scope always wins: this only names a caller that declared
nothing, and it never overwrites an explicit label.
"""
from __future__ import annotations

import pytest

from aria_service.intel import cost_tracker as ct


def test_an_explicit_scope_is_never_overridden():
    """The whole mechanism must be invisible to correctly-scoped callers."""
    with ct.feature("dd_report"):
        assert ct.attribute_unscoped_caller() == ""


def test_an_unscoped_caller_is_named_after_its_module():
    label = ct.attribute_unscoped_caller()
    assert label.startswith("unscoped:"), label
    # This test file is the caller, so it must name THIS module — not
    # cost_tracker, and not an asyncio internal.
    assert "cost_tracker" not in label
    assert "rf4087" in label.lower(), label


def test_the_label_is_low_cardinality():
    """A per-line or per-function label would explode `by_feature` into
    thousands of rows and make the panel unreadable — module granularity is
    the point."""
    a = ct.attribute_unscoped_caller()
    b = ct.attribute_unscoped_caller()
    assert a == b, (a, b)
    assert ":" in a and a.count(":") == 1
    assert len(a) <= 64, a


def test_plumbing_frames_are_skipped():
    """Called through the metering layer's own modules, the answer must still
    be the real caller — otherwise every row would read `unscoped:metered`."""
    from aria_service.llm import metered  # noqa: F401

    def _pretend_plumbing():
        return ct.attribute_unscoped_caller()

    # A frame inside this test module is a legitimate caller; the guard is that
    # cost_tracker/llm plumbing modules never win.
    label = _pretend_plumbing()
    assert not label.startswith("unscoped:llm."), label
    assert not label.startswith("unscoped:intel.cost_tracker"), label


def test_it_never_raises_and_degrades_to_uncategorized(monkeypatch):
    """Fail-open: attribution is bookkeeping and must never break an LLM call.
    A broken probe must return the neutral label, not an exception and not a
    misleading name."""
    def _boom(*a, **k):
        raise RuntimeError("no stack for you")

    monkeypatch.setattr(ct, "_caller_module", _boom)
    assert ct.attribute_unscoped_caller() == ""


@pytest.mark.parametrize("method", ["complete", "stream"])
def test_both_metered_paths_capture_the_caller(method):
    """§13 stream-bypass: `stream` is a subset-fork of `complete`, so any new
    hook must be mirrored into BOTH. A fix that only lands on `complete` leaves
    streaming spend unattributed, and streaming is the user-facing path."""
    import inspect

    from aria_service.llm import metered

    src = inspect.getsource(getattr(metered.MeteredProvider, method))
    assert "_cost_attribution()" in src, (
        f"MeteredProvider.{method} does not capture the unscoped caller — "
        "§13 requires the hook in both paths")
    # …and the captured label must actually reach the recorder, not be
    # computed and dropped.
    assert "_feature_name" in src, (
        f"MeteredProvider.{method} computes the label but never forwards it")


def test_the_attribution_helper_reaches_the_real_implementation():
    """`_cost_attribution` is a lazy-import shim; prove it delegates rather
    than quietly returning a constant."""
    from aria_service.llm import metered

    with ct.feature("dd_report"):
        assert metered._cost_attribution() == ""
    label = metered._cost_attribution()
    assert label.startswith("unscoped:"), label
    # Called from this test, the plumbing modules must not win.
    assert not label.startswith("unscoped:llm."), label


def _as_if_defined_in(module_name: str, fn):
    """Rebind `fn` so its frame reports `__name__ == module_name`.

    The walk reads `frame.f_globals["__name__"]`, so this reproduces the exact
    production frame shape without importing anything into a real module. A
    first attempt at the C-136 guard just decorated a function in THIS module
    and passed with the fix removed — useless, because the test module's own
    frame wins before the wrapper is ever reached. The defect needs the
    decorated function to live in a SKIPPED module, which is what production
    has and what this builds.
    """
    import types

    g = dict(fn.__globals__)
    g["__name__"] = module_name
    return types.FunctionType(fn.__code__, g, fn.__name__, fn.__defaults__,
                              fn.__closure__)


def test_a_wiring_decorator_never_wins_the_walk():
    """R-F4090 (C-136). Live 9 minutes after R-F4087 deployed: **30 of 33** LLM
    calls attributed to `unscoped:intel.wire`. `wire.py` makes no LLM calls —
    it is a `functools.wraps` decorator module — and `MeteredProvider.complete`
    carries `@fail_wire` (`metered.py:247`). So the production stack is

        attribute_unscoped_caller   (cost_tracker      — skipped)
        _cost_attribution           (llm.metered       — skipped)
        complete                    (llm.metered       — skipped)
        fail_wire wrapper           (intel.wire        — WON every time)
        the real caller             (never reached)

    That is the original defect one level up, and worse in one specific way:
    30 distinct callers collapsed into a single label that LOOKS like an
    answer, where `uncategorized` at least looked like a gap. A decorator is
    never the spender.
    """
    from aria_service.intel.wire import fail_wire

    def _inner():
        return ct.attribute_unscoped_caller()

    # Sits in a skipped module, exactly like `MeteredProvider.complete`.
    inner = _as_if_defined_in("aria_service.llm.metered", _inner)
    wrapped = fail_wire(module="test_rf4090", gap_type="engine_failure")(inner)

    label = wrapped()
    assert label.startswith("unscoped:"), label
    assert "intel.wire" not in label, (
        "the @fail_wire wrapper frame won the walk — a decorator is plumbing, "
        f"never the spender: {label}")
    # The walk must continue past the wrapper to the real caller: this test.
    assert "rf4087" in label.lower(), label


def test_the_decorator_is_invisible_to_attribution():
    """R-F4090 (C-136): wrapping a function cannot change who is billed."""
    from aria_service.intel.wire import fail_wire

    def _inner():
        return ct.attribute_unscoped_caller()

    plain = _as_if_defined_in("aria_service.llm.metered", _inner)
    wrapped = fail_wire(module="test_rf4090", gap_type="engine_failure")(
        _as_if_defined_in("aria_service.llm.metered", _inner))

    assert plain() == wrapped()


def test_record_call_honours_an_explicitly_passed_feature():
    """The captured label reaches the ledger via `feature_name=`, which must
    take precedence over the contextvar exactly as it did before."""
    import inspect

    src = inspect.getsource(ct.record_call)
    assert 'feature_name or get_current_feature() or "uncategorized"' in src, (
        "record_call's precedence changed — the captured label is passed as "
        "feature_name and depends on it winning over the contextvar")


def test_the_metered_recorder_forwards_the_label():
    """The capture happens at `complete`/`stream` entry but the record is
    written in `_record_cost`, so the label has to survive the hand-off."""
    import inspect

    from aria_service.llm import metered

    src = inspect.getsource(metered.MeteredProvider._record_cost)
    assert "feature_name" in src, (
        "_record_cost drops the captured label before record_call sees it")
