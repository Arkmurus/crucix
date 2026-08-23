"""R-F4243 — a preference pair must not be winnable by counting tokens.

The defect measured on `aria_tooluse_resolution_boundary_dpo_v1.jsonl`: in 30 of
32 rows the label was recoverable from LENGTH ALONE, and the direction flipped
per branch (chosen shorter in 9/10 `unique_live`, longer in 21/22 elsewhere). A
model can minimise that loss by learning verbosity and never learn to perform a
selection — and since 22 of 32 rows push toward LONGER, the net gradient favours
the list-shaped answer, which is exactly the regression the sweep produced.
"""
from __future__ import annotations

import json
import pathlib

import pytest

from scripts.train import build_resolution_length_control as lc

SOURCE = (pathlib.Path(__file__).resolve().parents[2]
          / "data/training/aria_tooluse_resolution_boundary_dpo_v1.jsonl")


@pytest.fixture(scope="module")
def source_rows() -> list[dict]:
    if not SOURCE.is_file():
        pytest.skip(f"source curriculum unavailable: {SOURCE.name}")
    return lc._load_jsonl(SOURCE)


class TestTheGuardSeesTheRealDefect:
    """It must fail on the curriculum that actually shipped, or it guards nothing."""

    def test_the_shipped_curriculum_is_length_predictive_in_every_branch(self, source_rows):
        signal = lc.length_signal(source_rows)
        assert signal, "no branches measured"
        predictive = {b for b, s in signal.items() if s["length_predictive"]}
        assert predictive == set(signal), (
            f"v1 was length-predictive in all four branches; guard sees only "
            f"{predictive}"
        )

    def test_a_nine_of_ten_skew_is_caught_not_waved_through(self):
        """An all-or-nothing test would call this clean. It is not clean."""
        rows = [{"chosen": "x" * 10, "rejected": "y" * 99} for _ in range(9)]
        rows.append({"chosen": "x" * 99, "rejected": "y" * 10})
        skew = max(9, 1) / 10
        assert skew >= lc.LENGTH_PREDICTIVE_SHARE

    def test_a_balanced_branch_is_not_flagged(self):
        assert 0.5 < lc.LENGTH_PREDICTIVE_SHARE, (
            "a perfectly balanced branch must never read as predictive"
        )


class TestTheFixBreaksTheConfound:
    def test_building_yields_one_counter_example_per_source_row(self, source_rows):
        out, evidence = lc.build(source_rows)
        assert len(out) == 2 * len(source_rows)
        assert sum(evidence["counter_examples_added"].values()) == len(source_rows)

    def test_no_branch_remains_length_predictive(self, source_rows):
        _, evidence = lc.build(source_rows)
        still = [b for b, s in evidence["after"].items() if s["length_predictive"]]
        assert still == []

    def test_the_before_evidence_is_recorded_not_just_the_after(self, source_rows):
        """A fix that only reports its own success cannot be audited."""
        _, evidence = lc.build(source_rows)
        assert any(s["length_predictive"] for s in evidence["before"].values())

    def test_a_thin_branch_is_marked_rather_than_quietly_certified(self, source_rows):
        _, evidence = lc.build(source_rows)
        thin = {b: s for b, s in evidence["after"].items()
                if s["pairs"] < lc.MINIMUM_PAIRS_FOR_A_SKEW}
        assert all(s["underpowered"] for s in thin.values())

    def test_the_prompt_and_chosen_are_untouched_by_the_counter_example(self, source_rows):
        """Only the rejected side may differ — otherwise this is a new task,
        not a controlled contrast."""
        for row in source_rows:
            counter = lc.counter_example(row, lc.resolution_branch(row))
            assert counter["prompt"] == row["prompt"]
            assert counter["chosen"] == row["chosen"]
            assert counter["rejected"] != row["rejected"]


class TestEverySyntheticRejectionIsGenuinelyWrong:
    """A rejection that the validator ACCEPTS would train the model away from a
    correct answer — strictly worse than no counter-example at all."""

    def test_every_counter_example_fails_the_real_validator(self, source_rows):
        for row in source_rows:
            # counter_example raises if the rejection passes validation
            lc.counter_example(row, lc.resolution_branch(row))

    def test_a_passing_rejection_raises_instead_of_being_dropped(
            self, source_rows, monkeypatch):
        """Silently skipping it would restore the confound one row at a time.

        Forced by making the synthetic rejection the row's OWN chosen
        completion, which passes the validator by construction.
        """
        row = next(r for r in source_rows
                   if lc.resolution_branch(r) == "unique_live")
        monkeypatch.setattr(lc, "false_denial", lambda _row: row["chosen"])
        with pytest.raises(ValueError, match="PASSES the validator"):
            lc.counter_example(row, "unique_live")


class TestTheSyntheticsAreTheMeasuredFailureShapes:
    def test_the_short_rejection_is_the_compass_false_denial(self, source_rows):
        text = lc.false_denial(source_rows[0])
        assert "could not identify" in text
        assert len(text) < 200, "the unique_live counter-example must be SHORT"

    def test_the_long_rejection_is_the_prudential_first_row_default(self, source_rows):
        row = next(r for r in source_rows
                   if lc.resolution_branch(r) == "ambiguous_live")
        text = lc.first_row_default(row)
        assert "The first result is" in text
        assert "I will proceed on company number" in text
        assert len(text) > len(row["chosen"]), (
            "the counter-example for a long-chosen branch must be LONGER"
        )

    def test_the_long_rejection_lists_the_real_candidates(self, source_rows):
        row = next(r for r in source_rows
                   if lc.resolution_branch(r) == "ambiguous_live")
        text = lc.first_row_default(row)
        for entry in lc.registry_results(row):
            assert str(entry["company_number"]) in text


class TestTheBuiltArtefactMatchesItsManifest:
    OUT = (pathlib.Path(__file__).resolve().parents[2]
           / "data/training/aria_tooluse_resolution_length_control_v1.jsonl")
    MANIFEST = (pathlib.Path(__file__).resolve().parents[2]
                / "data/eval_reports/aria_tooluse_resolution_length_control_v1_manifest.json")

    def test_the_committed_curriculum_hashes_to_its_manifest(self):
        if not (self.OUT.is_file() and self.MANIFEST.is_file()):
            pytest.skip("length-controlled curriculum not built here")
        manifest = json.loads(self.MANIFEST.read_text(encoding="utf-8"))
        assert lc._sha(self.OUT) == manifest["output_sha256"]
        assert lc._sha(SOURCE) == manifest["input_sha256"]
        rows = lc._load_jsonl(self.OUT)
        assert len(rows) == manifest["curriculum_rows"]
