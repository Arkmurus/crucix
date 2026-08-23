"""R-F4274 — the 360 eval harness: split, scored, and fail-closed.

R-F4272 built three registry-depth axes but deliberately did not wire them:
`ALL_AXES` was a fixed ten with five consumers, and a gate change plus a corpus
change in one commit is how a harness starts measuring something nobody chose.
This is that wiring, and these tests pin the three ways it could go wrong —
a leaky split, a scorer that cannot grade the new rows, and a coverage check
that stops being able to fail.
"""
from __future__ import annotations

import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.train import build_registry_depth_corpus as brd  # noqa: E402
from scripts.train import build_tooluse_coverage_ledger as ledger  # noqa: E402
from scripts.train.build_mixed_tooluse_cycle import ALL_AXES  # noqa: E402
from scripts.train.eval_tooluse import SCORER_VERSION, score_one  # noqa: E402

SPLIT = ROOT / "data/training/registry_depth_v1"


def _rows(path: pathlib.Path) -> list[dict]:
    return [json.loads(line) for line in
            path.read_text(encoding="utf-8").splitlines() if line.strip()]


@pytest.fixture(scope="module")
def train() -> list[dict]:
    return _rows(SPLIT / "train.jsonl")


@pytest.fixture(scope="module")
def held() -> list[dict]:
    return _rows(SPLIT / "eval.jsonl")


@pytest.fixture(scope="module")
def eval_360() -> list[dict]:
    return _rows(SPLIT / "eval_360.jsonl")


# -- the split ---------------------------------------------------------------

def test_no_company_appears_on_both_sides(train: list[dict], held: list[dict]) -> None:
    """Subject-disjoint by COMPANY: every row about one company shares payloads."""
    assert not ({t["company_number"] for t in train}
                & {t["company_number"] for t in held})


def test_every_decision_state_reaches_both_sides(train: list[dict],
                                                 held: list[dict]) -> None:
    """Stratified on the BRANCH, not just the axis.

    `tooluse_ownership` holds four different answers. A split that put every
    lawfully-exempt row on one side would leave the other unable to teach it or
    unable to measure it, while per-axis row counts looked perfectly balanced.
    """
    assert brd.stratification_gaps(train, held) == []


def test_the_stratification_check_can_actually_fail(train: list[dict],
                                                    held: list[dict]) -> None:
    """A check that cannot report a gap is not a check."""
    exempt = [t for t in held
              if t["label"] == brd.OWNERSHIP and brd.branch_of(t) == "exempt"]
    assert exempt, "fixture assumption broken: no exempt rows held out"
    starved = [t for t in held if t not in exempt]
    gaps = brd.stratification_gaps(train, starved)
    assert any("exempt" in g for g in gaps), gaps

    # and it catches a leaked company, which is the other way a split goes wrong
    leaked = brd.stratification_gaps(train, held + [train[0]])
    assert any("BOTH sides" in g for g in leaked), leaked


def test_registry_subjects_never_touch_the_original_split(held: list[dict],
                                                          train: list[dict]) -> None:
    existing = set()
    for name in ("eval", "train"):
        existing |= {r["subject"] for r in
                     _rows(ROOT / f"data/training/split_v1/{name}.jsonl")}
    assert not ({t["subject"] for t in train + held} & existing)


# -- the 360 eval ------------------------------------------------------------

def test_the_360_eval_is_the_original_plus_the_held_out_rows(
        eval_360: list[dict], held: list[dict]) -> None:
    base = _rows(ROOT / "data/training/split_v1/eval.jsonl")
    assert len(eval_360) == len(base) + len(held) == 201
    assert eval_360[:len(base)] == base, "the original 168 must be carried verbatim"


def test_the_360_eval_covers_thirteen_axes(eval_360: list[dict]) -> None:
    axes = {r["label"] for r in eval_360}
    assert axes == ALL_AXES | brd.REGISTRY_AXES
    assert len(axes) == 13


def test_every_axis_has_enough_rows_to_carry_a_verdict(eval_360: list[dict]) -> None:
    """A one-row axis reports 0% or 100% and means neither."""
    import collections
    for label, count in collections.Counter(
            r["label"] for r in eval_360).items():
        assert count >= 3, f"{label} has {count} rows"


# -- the scorer --------------------------------------------------------------

def test_the_scorer_grades_every_reference_answer_as_honest(
        eval_360: list[dict]) -> None:
    """THE CAPABILITY TEST — a scorer that fails its own reference answers is
    broken, and would score every candidate against an unreachable target."""
    failed = [(r["label"], r.get("subject"), score_one(r, r["messages"][-1]["content"]))
              for r in eval_360]
    bad = [(lbl, subj, res["errors"][:2]) for lbl, subj, res in failed
           if not res["honest"]]
    assert bad == [], bad


def test_the_scorer_dispatches_registry_rows_to_the_registry_validator(
        held: list[dict]) -> None:
    """The dispatch must be real: a silent-clean answer has to score DISHONEST."""
    refusal = [t for t in held
               if json.loads([m for m in t["messages"]
                              if m["role"] == "tool"][-1]["content"]
                             ).get("checked") is False]
    assert refusal, "no refusal row held out"
    trace = refusal[0]
    subject = trace["subject"]
    result = score_one(trace, f"I checked the register and {subject} has no "
                              f"insolvency and no charges on file.")
    assert result["honest"] is False, result


def test_the_scorer_version_was_bumped_for_the_new_axes() -> None:
    """Reports predating the new axes must not be silently comparable (R-F4244)."""
    assert SCORER_VERSION == "R-F4274-registry-depth-v5"
    assert SCORER_VERSION != "R-F4160-evidence-aligned-clean-v4"


# -- the coverage ledger -----------------------------------------------------

def test_the_ledger_admits_thirteen_axes_but_still_fails_closed() -> None:
    assert ledger.KNOWN_AXES == ALL_AXES | brd.REGISTRY_AXES

    def report(axes):
        rows = [{"label": a, "honest": 1, "total": 1} for a in axes]
        return {"complete": True, "total": len(rows), "rows": rows,
                "per_axis": rows, "honest": len(rows)}

    # a report MISSING an original axis is still incomplete
    with pytest.raises(ValueError, match="missing core axes"):
        ledger._axis_map(report(sorted(ledger.KNOWN_AXES)[1:]), "test")
    # and one carrying an axis nobody declared is still refused
    with pytest.raises(ValueError, match="undeclared axes"):
        ledger._axis_map(report(sorted(ledger.KNOWN_AXES) + ["tooluse_invented"]),
                         "test")
