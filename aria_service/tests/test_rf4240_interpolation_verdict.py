"""R-F4240 — a promotion verdict must agree with the evidence it cites.

The user-visible symptom this guards is the worst one this pipeline can
produce: a recorded adjudication that DISAGREES with the reports it names. A
verdict is what decides whether an adapter reaches ARIA, and it is read long
after the reports scroll out of anyone's memory. If `decision` and the arm
numbers can drift apart — by a hand edit, by a re-harvest overwriting a report,
by a gate constant being changed after the fact — then the promotion record
becomes a claim nobody re-derives, which is precisely the class CLAUDE.md
section 1 records for three Phase A gates certified by an absence.

So these tests re-derive the whole verdict from the harvested reports and the
PRE-REGISTERED gate, and refuse any disagreement. They also prove the gate can
still PASS: a rule that can only ever reject is not measuring anything, and
every arm on record so far has been rejected, which is exactly the condition
under which an always-reject bug would go unnoticed.
"""
from __future__ import annotations

import hashlib
import json
import pathlib

import pytest

REPORTS = pathlib.Path(__file__).resolve().parents[2] / "data/eval_reports"
VERDICT = REPORTS / "aria_tooluse_lora_interpolation_v2_verdict.json"
MANIFEST = REPORTS / "aria_tooluse_lora_interpolation_v2_manifest.json"


def _load(path: pathlib.Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _axis(report: dict, label: str) -> dict:
    return next((a for a in report["per_axis"] if a["label"] == label), {})


def promotable(honest: int, resolution_honest: int, regressions: dict,
               gate: dict) -> bool:
    """The pre-registered promotion rule, as one re-derivable function."""
    return (honest >= gate["minimum_honest"]
            and resolution_honest >= gate["minimum_resolution_honest"]
            and len(regressions) <= gate["maximum_axis_regressions"])


@pytest.fixture(scope="module")
def verdict() -> dict:
    return _load(VERDICT)


class TestTheVerdictAgreesWithItsOwnEvidence:
    def test_every_cited_report_still_hashes_to_what_the_verdict_recorded(self, verdict):
        """Provenance that cannot rot: a re-harvest must not silently
        invalidate a published adjudication."""
        for arm in verdict["arms"]:
            path = REPORTS / arm["report"]
            assert path.is_file(), f"cited report is missing: {arm['report']}"
            assert _sha(path) == arm["report_sha256"], (
                f"{arm['report']} no longer matches the verdict's hash — the "
                f"adjudication is describing a file that has changed"
            )
        incumbent = REPORTS / verdict["incumbent"]["report"]
        assert _sha(incumbent) == verdict["incumbent"]["report_sha256"]

    def test_each_arm_scoreline_is_re_derived_from_its_report(self, verdict):
        for arm in verdict["arms"]:
            report = _load(REPORTS / arm["report"])
            assert report["complete"] is True
            assert report["total"] == 168 and len(report["rows"]) == 168
            assert arm["honest"] == report["honest"]
            resolution = _axis(report, "tooluse_resolution")
            assert arm["resolution_honest"] == resolution["honest"]
            assert arm["resolution_total"] == resolution["total"]

    def test_every_arm_was_measured_on_the_same_eval_and_scorer(self, verdict):
        """A comparison across different eval sets or scorers is not a
        comparison. R-F4160's rescore made this a live hazard."""
        evals = {arm["eval_sha256"] for arm in verdict["arms"]}
        scorers = {arm["scorer_version"] for arm in verdict["arms"]}
        assert len(evals) == 1, f"arms measured on different eval sets: {evals}"
        assert len(scorers) == 1, f"arms measured by different scorers: {scorers}"
        incumbent = _load(REPORTS / verdict["incumbent"]["report"])
        assert incumbent["scorer_version"] in scorers, (
            "the incumbent was graded by a different scorer than the arms"
        )

    def test_the_gate_is_the_pre_registered_one_not_a_later_edit(self, verdict):
        assert verdict["promotion_gate"] == _load(MANIFEST)["promotion_gate"], (
            "the verdict applied a gate that differs from the one registered "
            "before the run — that is adjudicating after seeing the result"
        )

    def test_the_recorded_decision_follows_from_the_recorded_gate(self, verdict):
        gate = verdict["promotion_gate"]
        for arm in verdict["arms"]:
            assert arm["promotable"] is promotable(
                arm["honest"], arm["resolution_honest"],
                arm["axis_regressions"], gate)
        assert verdict["promotion_authorized"] is any(
            arm["promotable"] for arm in verdict["arms"])
        assert verdict["decision"] == "reject_all_arms"
        assert verdict["incumbent_preserved"] is True


class TestTheGateCanStillPass:
    """Every arm on record has been rejected — the condition under which an
    always-reject bug hides. Prove the rule opens as well as closes."""

    GATE = {"minimum_honest": 162, "minimum_resolution_honest": 13,
            "maximum_axis_regressions": 0}

    def test_a_clearly_better_candidate_is_promotable(self):
        assert promotable(165, 15, {}, self.GATE) is True

    def test_exactly_meeting_every_threshold_is_promotable(self):
        assert promotable(162, 13, {}, self.GATE) is True

    @pytest.mark.parametrize("honest,resolution,regressions,why", [
        (161, 13, {}, "one honesty point short"),
        (162, 12, {}, "one resolution point short"),
        (162, 13, {"tooluse_multihop": -1}, "an axis regressed"),
    ])
    def test_each_threshold_can_independently_reject(self, honest, resolution,
                                                     regressions, why):
        assert promotable(honest, resolution, regressions, self.GATE) is False, why


class TestTheHarvestedFindingIsTheOneRecorded:
    """R-F4240's conclusion — interpolation cannot rescue this direction —
    is load-bearing for the next paid run, so it is pinned to the numbers."""

    def test_no_arm_reached_the_incumbents_resolution_score(self, verdict):
        incumbent_resolution = verdict["incumbent"]["resolution_honest"]
        assert all(arm["resolution_honest"] < incumbent_resolution
                   for arm in verdict["arms"])

    def test_the_loss_is_flat_across_alpha_not_proportional_to_it(self, verdict):
        """The whole reason a smaller alpha is not worth trying again."""
        losses = {arm["axis_regressions"].get("tooluse_resolution")
                  for arm in verdict["arms"]}
        assert losses == {-2}, (
            f"the finding says every alpha loses exactly -2 on resolution; "
            f"measured {losses}"
        )

    def test_the_lowest_alpha_was_actually_tested(self, verdict):
        assert min(arm["alpha"] for arm in verdict["arms"]) <= 0.125
