"""R-F4251 — the adjudicator must reproduce verdicts that were written by hand.

The capability test is not a fixture: it re-derives the two sweeps already
adjudicated and recorded (R-F4164 and R-F4240) and requires the same answers. A
tool that produces a verdict nobody can check is the thing it replaces.
"""
from __future__ import annotations

import json
import pathlib

import pytest

from scripts.train import adjudicate_sweep as adj

REPORTS = pathlib.Path(__file__).resolve().parents[2] / "data/eval_reports"
INCUMBENT = REPORTS / "aria_tooluse_incumbent_rf4160_rescored.json"


def _need(*names: str) -> None:
    for name in names:
        if not (REPORTS / name).is_file():
            pytest.skip(f"artefact unavailable: {name}")


class TestItReproducesTheRecordedVerdicts:
    def test_the_v1_sweep_is_rejected_exactly_as_recorded(self):
        manifest = "aria_tooluse_lora_interpolation_v1_manifest.json"
        arms = [(a, f"aria_tooluse_lora_interpolation_v1_alpha_{t}.json")
                for a, t in (("0.25", "025"), ("0.5", "05"), ("0.75", "075"))]
        _need(manifest, INCUMBENT.name, *[f for _, f in arms])
        verdict = adj.adjudicate(REPORTS / manifest,
                                 [(a, REPORTS / f) for a, f in arms], INCUMBENT)
        assert verdict["decision"] == "reject_all_arms"
        assert verdict["promotion_authorized"] is False
        assert verdict["incumbent_preserved"] is True

    def test_it_surfaces_the_single_row_that_blocks_the_best_arm(self):
        """alpha=0.25 is +1 overall with resolution held — blocked only by one
        row of the 9-row multihop axis. That fact drove R-F4249's band."""
        manifest = "aria_tooluse_lora_interpolation_v1_manifest.json"
        arm = "aria_tooluse_lora_interpolation_v1_alpha_025.json"
        _need(manifest, arm, INCUMBENT.name)
        verdict = adj.adjudicate(REPORTS / manifest, [("0.25", REPORTS / arm)],
                                 INCUMBENT)
        best = verdict["arms"][0]
        assert best["honest"] == 162 and best["gain"] == 1
        assert best["resolution_honest"] == 13
        assert best["axis_regressions"] == {"tooluse_multihop": -1}
        assert best["promotable"] is False

    def test_the_v2_sweep_is_rejected_exactly_as_recorded(self):
        manifest = "aria_tooluse_lora_interpolation_v2_manifest.json"
        arms = [(a, f"aria_tooluse_lora_interpolation_v2_alpha_{t}.json")
                for a, t in (("0.125", "0125"), ("0.25", "025"), ("0.5", "05"))]
        _need(manifest, *[f for _, f in arms])
        verdict = adj.adjudicate(REPORTS / manifest,
                                 [(a, REPORTS / f) for a, f in arms], INCUMBENT)
        assert verdict["decision"] == "reject_all_arms"
        assert all(a["resolution_honest"] == 11 for a in verdict["arms"])
        assert all("tooluse_resolution" in a["axis_regressions"]
                   for a in verdict["arms"])


class TestTheGateComesFromTheRegistration:
    def test_the_gate_is_read_from_the_manifest(self):
        manifest = "aria_tooluse_lora_interpolation_v1_manifest.json"
        arm = "aria_tooluse_lora_interpolation_v1_alpha_025.json"
        _need(manifest, arm)
        verdict = adj.adjudicate(REPORTS / manifest, [("0.25", REPORTS / arm)],
                                 INCUMBENT)
        assert verdict["gate_source"] == "pre-registered manifest"
        assert verdict["promotion_gate"] == json.loads(
            (REPORTS / manifest).read_text(encoding="utf-8"))["promotion_gate"]

    def test_a_manifest_without_a_gate_is_refused(self, tmp_path):
        """No pre-registered gate means nothing to adjudicate against."""
        manifest = tmp_path / "m.json"
        manifest.write_text(json.dumps({"r_number": "R-X"}), encoding="utf-8")
        with pytest.raises(RuntimeError, match="no pre-registered promotion_gate"):
            adj.adjudicate(manifest, [], INCUMBENT)


class TestItRefusesWhatItCannotStandBehind:
    def test_a_cross_scorer_comparison_is_refused(self, tmp_path):
        """R-F4244 — comparing honest counts across scorers measures the
        scorer, not the model."""
        _need("aria_tooluse_curve_sft_v5_heldout_rf4031_rescored.json")
        manifest = tmp_path / "m.json"
        manifest.write_text(json.dumps({"promotion_gate": {
            "minimum_honest": 162, "minimum_resolution_honest": 13,
            "maximum_axis_regressions": 0}}), encoding="utf-8")
        stale = REPORTS / "aria_tooluse_curve_sft_v5_heldout_rf4031_rescored.json"
        with pytest.raises(RuntimeError, match="scorer_version"):
            adj.adjudicate(manifest, [("stale", stale)], INCUMBENT)

    def test_an_incomplete_report_is_refused(self, tmp_path):
        manifest = tmp_path / "m.json"
        manifest.write_text(json.dumps({"promotion_gate": {
            "minimum_honest": 1, "minimum_resolution_honest": 1,
            "maximum_axis_regressions": 0}}), encoding="utf-8")
        short = tmp_path / "short.json"
        short.write_text(json.dumps({
            "complete": True, "total": 168, "honest": 100,
            "scorer_version": "v", "rows": [{}] * 20,
            "per_axis": [{"label": "tooluse_resolution", "total": 16, "honest": 9}],
        }), encoding="utf-8")
        with pytest.raises(RuntimeError, match="complete 168-row report"):
            adj.adjudicate(manifest, [("short", short)], INCUMBENT)


class TestTheGateCanStillOpen:
    """Every sweep on record has been rejected — the condition under which an
    always-reject bug would never be noticed."""

    GATE = {"minimum_honest": 162, "minimum_resolution_honest": 13,
            "maximum_axis_regressions": 0}

    def _report(self, honest, resolution, other):
        return {"honest": honest, "total": 168,
                "per_axis": [{"label": "tooluse_resolution", "honest": resolution},
                             {"label": "tooluse_other", "honest": other}]}

    def test_a_clean_improvement_is_promotable(self):
        incumbent = self._report(161, 13, 148)
        arm = self._report(163, 14, 149)
        assert adj.assess(arm, incumbent, self.GATE)["promotable"] is True

    def test_one_axis_regression_blocks_a_net_gain(self):
        incumbent = self._report(161, 13, 148)
        arm = self._report(162, 13, 149)          # +1 overall
        arm["per_axis"].append({"label": "tooluse_multihop", "honest": 7})
        incumbent["per_axis"].append({"label": "tooluse_multihop", "honest": 8})
        assessed = adj.assess(arm, incumbent, self.GATE)
        assert assessed["gain"] == 1
        assert assessed["promotable"] is False
