"""R-F4094 (C-138) — the external ledger persisted a VERDICT without the
EVIDENCE, so a rule change could never be re-derived.

`record_external_call` stored a boolean `success`, and the flush incremented a
monotonic `errors` counter from it. The outcome that PRODUCED that boolean —
`ok` / `empty` / `timeout` / `rate_limited` / … — was discarded at record time.

So when R-F4083 (C-131) corrected the rule (an `empty` search result is an
ANSWER, not a failure), the correction could only apply to calls made after the
deploy. Every prior increment was already collapsed into a boolean whose
definition no longer existed, and the aggregate kept serving it. Measured live
2026-08-17, a full day after that fix shipped and was verified:

    /api/aria/cost/external   brave: calls 168, errors 71, error_rate 0.4226
    /api/aria/search/health   brave_usage.monthly:
                              {"total": 234, "ok": 135, "empty": 99}

Zero `rate_limited` / `auth_failed` / `http_error` / `timeout` — **zero real
errors** — while the command centre rendered a red "Fail rate 42%" for the
paid, DD-only search engine. The C-131 fix was real and is live
(`brave_usage.py:333`); it simply could not reach backwards, and nothing said so.

**The root is the shape, not Brave.** A derived verdict persisted without its
evidence is uncorrectable by construction, and this is the second time it has
bitten: C-131 fixed the rule, this fixes the reason the rule could not be
re-applied. The ledger now keeps the outcome breakdown and `errors` is DERIVED
AT READ TIME from `_NON_ERROR_OUTCOMES`. Reclassifying an outcome is now a
one-line change that retroactively corrects every historical reading, which is
exactly what was impossible before.

Legacy rows are not discarded and not silently reinterpreted: a service with no
breakdown keeps reporting its counter, labelled `error_source: "legacy_counter"`,
because we genuinely cannot know what those increments meant.
"""
from __future__ import annotations

import pytest

from aria_service.intel import cost_tracker as ct


def test_non_error_outcomes_are_declared_and_include_empty():
    """The read-time rule must be a named set, not a boolean frozen at write."""
    assert hasattr(ct, "_NON_ERROR_OUTCOMES"), (
        "the error rule must live at READ time so it can be re-applied to history")
    assert "ok" in ct._NON_ERROR_OUTCOMES
    assert "empty" in ct._NON_ERROR_OUTCOMES, (
        "R-F4083: an empty search result is an answer, not a failure")


def test_errors_are_derived_from_the_breakdown_not_the_counter():
    agg = {"brave": {"calls": 168, "cost_usd": 0.84, "errors": 71,
                     "by_outcome": {"ok": 135, "empty": 99}}}
    out = ct._apply_error_policy(agg)
    row = out["brave"]
    assert row["errors"] == 0, (
        "135 ok + 99 empty is zero failures; the stale 71 must not survive")
    assert row["error_rate"] == 0.0
    assert row["error_source"] == "outcome_breakdown"
    assert row["error_sample"] == 234


def test_the_stale_counter_is_preserved_not_deleted():
    """We correct the reading, we do not erase what was recorded."""
    agg = {"brave": {"calls": 168, "errors": 71,
                     "by_outcome": {"ok": 135, "empty": 99}}}
    row = ct._apply_error_policy(agg)["brave"]
    assert row["errors_legacy_counter"] == 71


def test_a_real_failure_still_counts():
    """The whole point is that this can still report a genuine failure."""
    agg = {"brave": {"calls": 10, "errors": 0,
                     "by_outcome": {"ok": 6, "empty": 1, "timeout": 2,
                                    "http_error": 1}}}
    row = ct._apply_error_policy(agg)["brave"]
    assert row["errors"] == 3, row
    assert row["error_sample"] == 10
    assert row["error_rate"] == 0.3


def test_a_service_without_a_breakdown_keeps_its_counter_and_says_so():
    """Legacy rows must not be silently reinterpreted as zero — we cannot know
    what those increments meant, and inventing a clean reading is the failure
    this whole batch is about."""
    agg = {"upstash": {"calls": 50, "errors": 5}}
    row = ct._apply_error_policy(agg)["upstash"]
    assert row["errors"] == 5
    assert row["error_rate"] == 0.1
    assert row["error_source"] == "legacy_counter"


def test_an_empty_breakdown_is_treated_as_absent_not_as_zero_errors():
    """`by_outcome: {}` means nothing has been recorded under the new scheme
    yet. Deriving 0/0 from it would render a confident 'no failures' from an
    empty set — an absence rendered as health."""
    agg = {"brave": {"calls": 168, "errors": 71, "by_outcome": {}}}
    row = ct._apply_error_policy(agg)["brave"]
    assert row["error_source"] == "legacy_counter"
    assert row["errors"] == 71


def test_a_malformed_row_cannot_take_the_panel_down():
    """R-F4064's lesson: one bad row must not raise out of a read endpoint."""
    agg = {"brave": None, "x": "nonsense", "ok": {"calls": 2, "errors": 0,
                                                  "by_outcome": {"ok": 2}}}
    out = ct._apply_error_policy(agg)
    assert out["ok"]["error_rate"] == 0.0


def test_the_recorder_passes_the_outcome_through():
    """Evidence has to reach the store, or the read-time rule has nothing to
    apply. `record_brave_call` is the only caller that knows the outcome."""
    import inspect

    src = inspect.getsource(ct.record_brave_call)
    assert "outcome" in src, "record_brave_call drops the outcome label"

    src_ext = inspect.getsource(ct.record_external_call)
    assert "outcome" in src_ext, "record_external_call has no outcome parameter"


def test_brave_usage_sends_its_outcome():
    import inspect

    from aria_service.intel import brave_usage

    src = inspect.getsource(brave_usage)
    assert "outcome=outcome" in src, (
        "brave_usage knows the outcome and must hand it to the ledger")
