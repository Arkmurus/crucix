"""R-F4244 — the progression gate must not compare across scorer generations.

The defect, measured on the shipped pins: the parent baseline reads 155/168
(resolution 11/16) under a scorer generation that recorded no version, and the
SAME stored answers read 161/168 (13/16) under the current one. Candidates are
always graded with the current scorer, so every candidate was handed +6
aggregate and +2 on the protected axis by nothing but time.

Ten reports on disk pass the shipped gate and fail an honest same-scorer
comparison. Two of them cannot be argued with: the incumbent measured against
itself, and the baseline's own answers merely re-graded.
"""
from __future__ import annotations

import json
import pathlib

import pytest

from scripts.train.learning_curve_gate import (
    progression_verdict,
    scorer_comparability,
)

REPORTS = pathlib.Path(__file__).resolve().parents[2] / "data/eval_reports"
CURRENT = "R-F4160-evidence-aligned-clean-v4"


def _report(honest: int, scorer: str | None, *, resolution: int = 13,
            total: int = 168) -> dict:
    """A minimal report the gate accepts as complete and self-consistent."""
    other = honest - resolution
    axes = [{"label": "tooluse_resolution", "total": 16, "honest": resolution},
            {"label": "tooluse_other", "total": total - 16, "honest": other}]
    rows = ([{"label": "tooluse_resolution", "subject": f"r{i}", "honest": True}
             for i in range(16)]
            + [{"label": "tooluse_other", "subject": f"o{i}", "honest": True}
               for i in range(total - 16)])
    report = {"complete": True, "total": total, "honest": honest,
              "per_axis": axes, "rows": rows}
    if scorer is not None:
        report["scorer_version"] = scorer
    return report


class TestComparabilityIsAPrecondition:
    def test_matching_scorers_are_comparable(self):
        assert scorer_comparability(_report(161, CURRENT),
                                    _report(162, CURRENT)) is None

    def test_a_missing_scorer_version_is_not_comparable(self):
        verdict = scorer_comparability(_report(155, None), _report(158, CURRENT))
        assert verdict["pass"] is False
        assert verdict["reason"] == "scorer_version_unknown"
        assert verdict["unknown_side"] == ["before"]

    def test_it_names_which_side_is_unknown(self):
        verdict = scorer_comparability(_report(155, CURRENT), _report(158, None))
        assert verdict["unknown_side"] == ["after"]

    def test_different_scorer_generations_are_not_comparable(self):
        verdict = scorer_comparability(_report(155, "R-F4031-old"),
                                       _report(158, CURRENT))
        assert verdict["reason"] == "scorer_generation_mismatch"
        assert verdict["scorer_versions"]["before"] == "R-F4031-old"

    def test_the_verdict_says_how_to_fix_it(self):
        """A gate that blocks without naming the remedy gets worked around."""
        for verdict in (scorer_comparability(_report(155, None), _report(158, CURRENT)),
                        scorer_comparability(_report(155, "old"), _report(158, CURRENT))):
            assert "re-score" in verdict["remedy"]


class TestTheBiasedComparisonCanNoLongerCertify:
    def test_a_stale_baseline_blocks_instead_of_reporting_progress(self):
        """155 -> 158 looks like +3 and is really -3 against the true 161."""
        verdict = progression_verdict(_report(155, None), _report(158, CURRENT),
                                      {"tooluse_resolution"})
        assert verdict["pass"] is False
        assert verdict["reason"] == "scorer_version_unknown"

    def test_the_same_answers_re_graded_are_not_progress(self):
        """The unanswerable case: identical model, identical eval, zero
        behavioural difference. The shipped gate called this positive_curve."""
        verdict = progression_verdict(_report(155, None), _report(161, CURRENT),
                                      {"tooluse_resolution"})
        assert verdict["pass"] is False

    def test_an_honest_same_scorer_gain_still_passes(self):
        """Fail-closed must not mean fail-always — the gate has to still open."""
        verdict = progression_verdict(
            _report(161, CURRENT, resolution=13),
            _report(163, CURRENT, resolution=14), {"tooluse_resolution"})
        assert verdict["pass"] is True, verdict
        assert verdict["gain"] == 2

    def test_an_honest_same_scorer_regression_still_fails(self):
        verdict = progression_verdict(
            _report(161, CURRENT, resolution=13),
            _report(158, CURRENT, resolution=11), {"tooluse_resolution"})
        assert verdict["pass"] is False
        assert verdict["reason"] == "curve_gate_failed"


class TestAgainstTheRealPinnedArtefacts:
    """Skipped where the files are absent; never silently passed."""

    def _load(self, name: str) -> dict:
        path = REPORTS / name
        if not path.is_file():
            pytest.skip(f"artefact unavailable: {name}")
        return json.loads(path.read_text(encoding="utf-8"))

    def test_the_shipped_baseline_carries_no_scorer_version(self):
        stale = self._load("aria_tooluse_curve_sft_v5_heldout_rf4031_rescored.json")
        assert not stale.get("scorer_version")
        assert stale["honest"] == 155

    def test_the_same_answers_rescore_six_points_higher(self):
        stale = self._load("aria_tooluse_curve_sft_v5_heldout_rf4031_rescored.json")
        fresh = self._load("aria_tooluse_curve_sft_v5_heldout_rf4160_rescored.json")
        assert fresh["scorer_version"] == CURRENT
        assert fresh["honest"] - stale["honest"] == 6, (
            "the +6 head start every candidate was handed"
        )

    def test_the_null_change_no_longer_certifies(self):
        stale = self._load("aria_tooluse_curve_sft_v5_heldout_rf4031_rescored.json")
        fresh = self._load("aria_tooluse_curve_sft_v5_heldout_rf4160_rescored.json")
        verdict = progression_verdict(stale, fresh, {"tooluse_resolution"})
        assert verdict["pass"] is False, (
            "identical answers, only re-graded, must never read as progress"
        )
