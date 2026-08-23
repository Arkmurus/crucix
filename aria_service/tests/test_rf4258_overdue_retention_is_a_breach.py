"""R-F4258 / C-223 — an overdue retention file was reported as a SUCCESS.

`GET /api/aria/vetting/retention` walks every case, computes
`RetentionVerdict.overdue` correctly (`due <= as_of`), surfaces it per row and
returns an `overdue_count`. All of that was already right.

Then it did this, for every review:

    overdue = [r for r in rows if r["overdue"]]
    wire_success(module=_MODULE,
                 summary=f"retention reviewed: {len(overdue)} overdue of {len(rows)}")

**A file held past its lawful disposal date landed as a positive signal.** The
count was in the summary STRING, but the signal TYPE was success — so
`capability_gaps`, the self-heal loop and every operator surface that reads
failures saw nothing. A number published where no verdict consumes it is the C-96
defect, here sitting on a UK GDPR / BS7858 retention obligation.

## The distinction being drawn

The review SUCCEEDING and the review FINDING something are different facts. A
clean review is still a success and stays one — silence is the correct answer when
nothing is overdue, and paging on every look is how an alert becomes background
noise (the R-F4024 cry-wolf rule).

Flood control is `record_gap`'s existing dedupe rather than a new latch here,
because this endpoint is view-triggered and could be polled.

## What is NOT fixed, deliberately

**Nothing schedules disposal.** `POST /case/{case_id}/dispose` is manual, and no
task or loop scans for overdue cases — so this signal still only fires when
someone looks. Building a background sweeper was considered and rejected as
disproportionate today: production holds **1 vetting case across 1 tenant**
(measured 2026-08-23), so a boot-path loop would be machinery watching a
near-empty set — the "guard whose universe is empty" shape in reverse. The
trigger to revisit is case volume, and C-223 records it.
"""
from __future__ import annotations

import ast
import pathlib

import pytest

from ._source_probe import repo_path


def _endpoint_src() -> str:
    src = pathlib.Path(repo_path("aria_service/routes/vetting.py")).read_text(
        encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and \
                node.name == "vetting_retention_ep":
            return ast.get_source_segment(src, node) or ""
    raise AssertionError("vetting_retention_ep not found — re-point this guard")


class TestABreachIsRoutedToAFailureSink:

    def test_the_overdue_branch_wires_a_failure(self):
        src = _endpoint_src()
        assert "wire_failure(" in src, (
            "an overdue retention file must reach a FAILURE sink — routing it "
            "to wire_success means capability_gaps and the self-heal loop never "
            "see a UK GDPR retention breach")

    def test_it_is_classified_as_a_data_protection_violation(self):
        assert 'gap_type="data_protection_violation"' in _endpoint_src()

    def test_the_gap_names_the_action_and_that_nothing_schedules_it(self):
        src = _endpoint_src()
        assert "OPERATOR ACTION" in src
        assert "dispose" in src
        assert "nothing schedules it" in src.lower() or "MANUAL" in src, (
            "the operator must be told disposal will not happen on its own — "
            "that is the difference between a backlog and a breach")

    def test_a_clean_review_is_still_a_success(self):
        """Silence when nothing is overdue. Paging on every look is noise."""
        src = _endpoint_src()
        assert "wire_success(" in src
        i_fail, i_ok = src.find("wire_failure("), src.find("wire_success(")
        assert 0 < i_fail < i_ok, (
            "the failure branch must be the `if overdue:` one and success the "
            "`else` — inverted, a clean review would page and a breach would not")


class TestTheNameActuallyResolves:
    """py_compile passes on a NameError, and this one fires ONLY on the rare path."""

    def test_wire_failure_is_imported(self):
        from aria_service.routes import vetting as v
        assert hasattr(v, "wire_failure"), (
            "wire_failure is referenced in the overdue branch but not imported — "
            "a NameError that only triggers when a breach is found is the worst "
            "possible failure mode for a compliance signal (§3b: verify the name)")

    def test_both_sinks_are_bound(self):
        from aria_service.routes import vetting as v
        assert hasattr(v, "wire_success")


class TestTheUnderlyingVerdictIsUnchanged:
    """R-F4258 re-routes a signal; it must not touch how overdue is DERIVED."""

    def test_overdue_is_still_due_on_or_before_as_of(self):
        import datetime as dt
        from aria_service.vetting.retention import RetentionVerdict

        v = RetentionVerdict(dt.date(2020, 1, 1), "past", True)
        assert v.overdue is True
        v2 = RetentionVerdict(None, "clock not started", False)
        assert v2.overdue is False and v2.due_date is None, (
            "a case whose retention clock has not started is NOT overdue — "
            "guessing a date would be the failure retention.py exists to avoid")
