"""R-F4271 / C-232 — the tool-use eval sees 6 of ARIA's 24 fundamentals.

The promoted parent scores 162/168 and four of its six misses are on an ADVISORY
axis, leaving two addressable rows in the whole harness. Thirteen candidates in a
row failed to promote and curriculum design took the blame every time. This
ledger states the real constraint as a number, from the LIVE standard.
"""
from __future__ import annotations

import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aria_service.intel.dd_standard import QUESTIONS_BY_ID  # noqa: E402
from scripts.train import fundamentals_coverage as fc  # noqa: E402

EVAL = ROOT / "data/training/split_v1/eval.jsonl"


@pytest.fixture(scope="module")
def rows() -> list[dict]:
    return [json.loads(line) for line in
            EVAL.read_text(encoding="utf-8").splitlines() if line.strip()]


@pytest.fixture(scope="module")
def ledger(rows: list[dict]) -> dict:
    return fc.coverage(rows)


# -- the measurement ---------------------------------------------------------

def test_the_eval_sees_a_quarter_of_the_standard(ledger: dict) -> None:
    assert ledger["fundamentals_total"] == 24
    assert ledger["fundamentals_covered"] == 6
    assert ledger["coverage_fraction"] == 0.25


def test_two_whole_clusters_have_no_eval_row(ledger: dict) -> None:
    """FINANCIAL_STANDING and LEGITIMACY_REGULATION are entirely unmeasured."""
    for cluster in ("FINANCIAL_STANDING", "LEGITIMACY_REGULATION"):
        assert ledger["by_cluster"][cluster]["covered"] == 0, cluster
    assert set(ledger["by_cluster"]["FINANCIAL_STANDING"]["uncovered"]) == {
        "FS-9", "FS-10", "FS-11", "FS-12"}
    assert set(ledger["by_cluster"]["LEGITIMACY_REGULATION"]["uncovered"]) == {
        "LR-18", "LR-19", "LR-20"}


def test_ninety_percent_of_rows_sit_on_one_saturated_cluster(rows: list[dict]) -> None:
    """The shape of the problem: rows are not scarce, BREADTH is."""
    report = json.loads((
        ROOT / "data/eval_reports"
        / "aria_tooluse_resolution_failure_correction_v1_rf4163_rescored.json"
    ).read_text(encoding="utf-8"))
    head = fc.headroom(report, fc.coverage(rows))
    integrity = head["INTEGRITY_SCREENING"]
    assert integrity["total"] == 152           # 90% of 168
    assert integrity["headroom"] == 2          # and only two rows left to win
    assert head["FINANCIAL_STANDING"]["total"] == 0
    assert head["LEGITIMACY_REGULATION"]["total"] == 0


# -- it refuses to infer, and it refuses to shrink ---------------------------

def test_coverage_is_never_inferred_from_a_shared_resolver(ledger: dict) -> None:
    """THE ANTI-C-39 PROPERTY, on a real collision.

    OC-5 (trace the natural persons who ultimately control the entity) declares
    resolver `companies_house` — the very family the eval already calls through
    `companies_house_search` and `companies_house_officers`. Inferring coverage
    from resolver overlap would stamp the UBO chain as measured when not one row
    walks a PSC. That is C-39 exactly: a successful call to one source used to
    certify sources that were never queried.
    """
    assert "companies_house" in QUESTIONS_BY_ID["OC-5"].resolvers
    assert ledger["covered_by"]["OC-5"] == []
    assert "OC-5" in ledger["fundamentals_uncovered"]


def test_a_new_fundamental_shows_up_as_uncovered(monkeypatch) -> None:
    """The registry is iterated live, so the denominator cannot silently rot."""
    extra = type("Q", (), {"id": "XX-25", "cluster": "EXISTENCE_IDENTITY"})()
    monkeypatch.setattr(fc, "QUESTIONS", tuple(QUESTIONS_BY_ID.values()) + (extra,))
    ledger = fc.coverage([])
    assert ledger["fundamentals_total"] == 25
    assert "XX-25" in ledger["fundamentals_uncovered"]


def test_an_undeclared_eval_axis_is_an_error() -> None:
    """An axis may not claim coverage by omission."""
    errors = fc.declaration_errors({"tooluse_something_new"})
    assert any("tooluse_something_new" in e for e in errors)
    with pytest.raises(RuntimeError, match="tooluse_something_new"):
        fc.coverage([{"label": "tooluse_something_new"}])


def test_a_declaration_naming_a_removed_fundamental_fails_loudly(monkeypatch) -> None:
    """Deleting a question must not make coverage look better."""
    monkeypatch.setitem(fc.AXIS_COVERAGE, "tooluse_trace",
                        {"kind": fc.FUNDAMENTAL, "fundamentals": ("IS-99",),
                         "why": "declares a question the standard does not have"})
    errors = fc.declaration_errors()
    assert any("IS-99" in e for e in errors)


def test_every_declaration_records_why() -> None:
    """A coverage claim nobody can audit is a guess wearing a verdict's clothes."""
    for axis, entry in fc.AXIS_COVERAGE.items():
        assert entry.get("why"), axis
        assert entry["kind"] in (fc.FUNDAMENTAL, fc.BEHAVIOUR), axis


def test_behaviour_axes_are_reported_separately(ledger: dict) -> None:
    """51 rows of honesty behaviour must never read as breadth."""
    assert set(ledger["behaviour_axes"]) == {
        "tooluse_challenge", "tooluse_challenge_unavailable", "tooluse_contradiction"}
    for axis in ledger["behaviour_axes"]:
        assert fc.AXIS_COVERAGE[axis]["kind"] == fc.BEHAVIOUR


def test_an_axis_declared_but_absent_from_this_eval_covers_nothing_here() -> None:
    """Coverage is a property of the rows present, not of the declaration table."""
    only_person = fc.coverage([{"label": "tooluse_person"}])
    assert only_person["covered_by"]["IS-13b"] == ["tooluse_person"]
    assert only_person["covered_by"]["IS-15"] == []
    assert only_person["fundamentals_covered"] == 1


def test_the_declaration_table_is_sound_as_shipped() -> None:
    """Every declared fundamental exists in the standard, today."""
    assert fc.declaration_errors() == []
