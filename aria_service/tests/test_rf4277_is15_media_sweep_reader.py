"""R-F4277 / C-235 — IS-15 is answered by the sweep that already runs.

`dd_standard` declares IS-15 (negative news) with
`pass_condition="A dedicated media sweep ran and a backend answered"` and, until
this fix, `reader=None` — so it took the `_unbuilt` branch and rendered

    IS-15  NOT_RUN  "no resolver is bound to this question in this build"

while the DD's adverse-media sweep was running, being paid for, and writing its
result to `report.adverse_media`. `coverage_pct` is computed over these
resolutions, so the customer's report understated what was established.

THE WHOLE DANGER OF THIS FIX is turning an honest NOT_RUN into a fabricated pass
(the C-39 failure: eight lists stamped CLEAN that were never queried). Most of
what follows is therefore about the sweeps that must NOT count — and the fields
to judge them by are not a matter of taste: **R-F2791 already established that
`templates_run` alone certified sweeps in which every backend call failed**, and
named `templates_searched` + `search_backends_answered` as the two a consumer
must read. IS-15's pass condition was written in those terms.
"""
from __future__ import annotations

import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aria_service.intel import dd_standard as ds  # noqa: E402

ANSWERED = {ds.EvidenceState.CORROBORATED.value, ds.EvidenceState.SINGLE_SOURCE.value}


def _report(adverse: object = None, **extra) -> dict:
    report = {"subject": {"name": "TEST LTD", "jurisdiction": "GB"}, **extra}
    if adverse is not None:
        report["adverse_media"] = adverse
    return report


def _is15(adverse: object = None, **extra) -> dict:
    rows = ds.assess(_report(adverse, **extra), tier="ENHANCED")["resolutions"]
    return {r["question_id"]: r for r in rows}["IS-15"]


def _swept(findings=(), *, searched=6, answered=1, partial=False) -> dict:
    """A completed sweep, in the shape `run_adverse_media_deep_search` returns."""
    return {
        "ok": True, "entity": "TEST LTD",
        "templates_run": searched, "templates_searched": searched,
        "search_backends_answered": answered,
        "templates_total_in_set": 30, "partial": partial, "timed_out": partial,
        "findings": list(findings), "findings_count": len(findings),
    }


# ── the defect ─────────────────────────────────────────────────────────────

def test_a_completed_sweep_is_no_longer_reported_as_unbound() -> None:
    """THE CAPABILITY TEST — this is the live symptom C-235 filed."""
    row = _is15(_swept())
    assert "no resolver is bound" not in str(row["reason"])
    assert row["state"] in ANSWERED, row


def test_a_clean_sweep_credits_coverage() -> None:
    """The user-visible outcome: the report stops understating what was done."""
    without = ds.assess(_report(), tier="ENHANCED")
    with_sweep = ds.assess(_report(_swept()), tier="ENHANCED")
    assert with_sweep["answered"] > without["answered"]
    assert with_sweep["coverage_pct"] > without["coverage_pct"]


def test_findings_are_reported_and_corroboration_follows_the_origins() -> None:
    findings = [
        {"source_url": "https://www.ft.com/x", "title": "Regulator opens probe"},
        {"source_url": "https://www.reuters.com/y", "title": "Second outlet reports"},
    ]
    row = _is15(_swept(findings, answered=2))
    assert row["state"] == ds.EvidenceState.CORROBORATED.value
    assert "probe" in str(row["reason"]).lower()


# ── never a fabricated clean ───────────────────────────────────────────────

def test_no_sweep_on_the_report_is_still_not_run() -> None:
    row = _is15()
    assert row["state"] == ds.EvidenceState.NOT_RUN.value
    assert row["state"] not in ANSWERED


def test_templates_entered_but_no_backend_answered_is_not_an_answer() -> None:
    """R-F2791's exact case, and the reason `templates_run` is not the field.

    Thirty templates can be ENTERED while every backend call fails. Reading that
    as a clean media sweep is precisely the C-39 defect — a clearance attributed
    to sources that never answered.
    """
    row = _is15(_swept(searched=30, answered=0))
    assert row["state"] == ds.EvidenceState.NOT_RUN.value
    assert "no search backend answered" in str(row["reason"])


def test_zero_templates_searched_is_not_an_answer() -> None:
    row = _is15(_swept(searched=0, answered=0))
    assert row["state"] == ds.EvidenceState.NOT_RUN.value


def test_an_unfinished_follow_up_is_not_an_answer() -> None:
    """R-F2657 defers the sweep; a process restart can leave it in_progress."""
    row = _is15({"status": "in_progress", "trigger": "GREEN-screen",
                 "started_at": 1.0})
    assert row["state"] == ds.EvidenceState.ATTEMPTED_INCONCLUSIVE.value
    assert row["state"] not in ANSWERED


def test_a_failed_sweep_is_attempted_not_clean() -> None:
    row = _is15({"error": "researcher timed out after 240s"})
    assert row["state"] == ds.EvidenceState.ATTEMPTED_INCONCLUSIVE.value
    assert "timed out" in str(row["reason"])


def test_ok_false_is_attempted_not_clean() -> None:
    row = _is15({"ok": False, "error": "entity_name required"})
    assert row["state"] == ds.EvidenceState.ATTEMPTED_INCONCLUSIVE.value


def test_a_TRUNCATED_sweep_that_found_nothing_cannot_support_a_clean_line() -> None:
    """R-F2667 marks a deadline-stopped sweep `partial`: an honest PARTIAL result.

    Findings from a partial sweep are real and are reported. Its SILENCE is not:
    'we ran out of time before finding anything' is not 'there is nothing'.
    """
    row = _is15(_swept(partial=True))
    assert row["state"] == ds.EvidenceState.ATTEMPTED_INCONCLUSIVE.value
    assert row["state"] not in ANSWERED


def test_a_truncated_sweep_that_DID_find_something_still_reports_it() -> None:
    """The finding is evidence regardless of how the sweep ended."""
    row = _is15(_swept([{"source_url": "https://www.ft.com/x", "title": "Probe"}],
                       partial=True))
    assert row["state"] in ANSWERED


def test_a_malformed_adverse_block_is_never_an_answer() -> None:
    for junk in ({}, [], "swept", 0, {"findings": None}):
        row = _is15(junk)
        assert row["state"] not in ANSWERED, junk


# ── it must not disturb anything else ──────────────────────────────────────

def test_binding_is15_changes_no_other_question() -> None:
    before = {r["question_id"]: r["state"]
              for r in ds.assess(_report(), tier="ENHANCED")["resolutions"]}
    after = {r["question_id"]: r["state"]
             for r in ds.assess(_report(_swept()), tier="ENHANCED")["resolutions"]}
    moved = {q for q in before if before[q] != after.get(q)}
    assert moved == {"IS-15"}, moved


def test_the_reader_is_actually_bound() -> None:
    """A reader that exists but is not attached is the R-F3099 shape."""
    assert ds.QUESTIONS_BY_ID["IS-15"].reader is not None


def test_a_crashing_reader_is_not_a_pass() -> None:
    """`assess` catches a raising reader; confirm the contract holds for ours."""
    row = _is15({"templates_searched": "six", "search_backends_answered": None,
                 "findings": {"not": "a list"}})
    assert row["state"] not in ANSWERED
