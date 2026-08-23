"""R-F4259 — an advisory axis is measured and reported, never dropped.

Operator decision 2026-08-23: `tooluse_resolution` stops BLOCKING promotion,
because R-F4257 established it measures a configuration that never ships. The
danger in implementing that is obvious — "stop blocking" quietly becoming "stop
measuring" is the close-the-gate-by-measuring-less failure CLAUDE.md section 1
forbids. These tests exist to make that impossible.
"""
from __future__ import annotations

import json
import pathlib

import pytest

from scripts.train import adjudicate_sweep as adj

REPORTS = pathlib.Path(__file__).resolve().parents[2] / "data/eval_reports"
GATE = {"minimum_honest": 162, "minimum_resolution_honest": 13,
        "maximum_axis_regressions": 0}


def _report(honest, resolution, other, multihop=8):
    return {"honest": honest, "total": 168, "per_axis": [
        {"label": "tooluse_resolution", "honest": resolution},
        {"label": "tooluse_multihop", "honest": multihop},
        {"label": "tooluse_other", "honest": other}]}


class TestAdvisoryMeansMeasuredNotDropped:
    def test_an_advisory_regression_is_still_reported(self):
        incumbent = _report(161, 13, 140)
        arm = _report(162, 12, 142)
        assessed = adj.assess(arm, incumbent, GATE, {"tooluse_resolution"})
        assert assessed["advisory_regressions"] == {"tooluse_resolution": -1}, (
            "the regression must still be computed and surfaced"
        )
        assert assessed["promotable"] is True

    def test_blocking_and_advisory_are_separate_fields(self):
        """A reader must never mistake one for the other."""
        incumbent = _report(161, 13, 140)
        arm = _report(162, 12, 142, multihop=7)
        assessed = adj.assess(arm, incumbent, GATE, {"tooluse_resolution"})
        assert assessed["axis_regressions"] == {"tooluse_multihop": -1}
        assert assessed["advisory_regressions"] == {"tooluse_resolution": -1}
        assert assessed["promotable"] is False, (
            "a BLOCKING regression must still block even when another axis is advisory"
        )

    def test_the_resolution_minimum_cannot_re_block_through_the_back_door(self):
        """Declaring the axis advisory while still enforcing its minimum would
        be advisory in name only."""
        incumbent = _report(161, 13, 140)
        arm = _report(162, 9, 145)          # far below minimum_resolution_honest
        assert adj.assess(arm, incumbent, GATE, {"tooluse_resolution"})["promotable"] is True

    def test_without_the_declaration_nothing_changes(self):
        """Advisory must be opt-in per run, never a default."""
        incumbent = _report(161, 13, 140)
        arm = _report(162, 12, 142)
        assessed = adj.assess(arm, incumbent, GATE)
        assert assessed["axis_regressions"] == {"tooluse_resolution": -1}
        assert assessed["advisory_regressions"] == {}
        assert assessed["promotable"] is False


class TestTheDeclarationMustBeJustified:
    def test_advisory_axes_without_a_rationale_is_refused(self, tmp_path):
        manifest = tmp_path / "m.json"
        manifest.write_text(json.dumps({
            "promotion_gate": GATE, "advisory_axes": ["tooluse_resolution"]}),
            encoding="utf-8")
        with pytest.raises(RuntimeError, match="advisory_rationale"):
            adj.adjudicate(manifest, [], REPORTS /
                           "aria_tooluse_incumbent_rf4160_rescored.json")


class TestTheRecordedDecision:
    MANIFEST = REPORTS / "aria_tooluse_promotion_rf4259_manifest.json"

    def _manifest(self) -> dict:
        if not self.MANIFEST.is_file():
            pytest.skip("promotion decision not recorded here")
        return json.loads(self.MANIFEST.read_text(encoding="utf-8"))

    def test_it_names_who_decided_and_when(self):
        manifest = self._manifest()
        assert manifest["decided_by"] == "operator"
        assert manifest["decided_at"] == "2026-08-23"

    def test_it_states_what_would_reverse_it(self):
        """An axis that stopped blocking must say what would make it block again."""
        manifest = self._manifest()
        assert "enforce_resolution_response" in manifest["reversal_condition"]
        assert "test_rf4144" in manifest["reversal_condition"]

    def test_it_does_not_overstate_what_promotion_means(self):
        """ARIA-LLM is not wired into the live chain; promotion is a training
        lineage decision, not a deployment."""
        manifest = self._manifest()
        assert "does NOT deploy to production" in manifest["what_this_promotion_is"]

    def test_the_gate_itself_was_not_weakened(self):
        manifest = self._manifest()
        assert manifest["promotion_gate"] == GATE, (
            "the thresholds must be unchanged — only the axis classification moved"
        )

    def test_the_verdict_carries_the_advisory_regression(self):
        verdict_path = REPORTS / "aria_tooluse_promotion_rf4259_verdict.json"
        if not verdict_path.is_file():
            pytest.skip("verdict not recorded here")
        verdict = json.loads(verdict_path.read_text(encoding="utf-8"))
        assert verdict["decision"].startswith("promote:")
        assert verdict["advisory_axes"] == ["tooluse_resolution"]
        arm = verdict["arms"][0]
        assert arm["advisory_regressions"] == {"tooluse_resolution": -1}
        assert arm["axis_regressions"] == {}
