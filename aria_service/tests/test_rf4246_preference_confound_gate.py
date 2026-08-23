"""R-F4246 — the gate must block the curriculum that was actually paid for.

Not a synthetic fixture: the assertions below run against
`aria_tooluse_resolution_boundary_dpo_v1.jsonl`, the real file nine candidates
were trained from, and against the length-controlled rebuild. A gate that only
passes hand-made examples proves nothing about the corpus it exists to stop.
"""
from __future__ import annotations

import pathlib

import pytest

from scripts.train import preflight_preference_confound as gate

TRAINING = pathlib.Path(__file__).resolve().parents[2] / "data/training"
DEFECTIVE = TRAINING / "aria_tooluse_resolution_boundary_dpo_v1.jsonl"
FIXED = TRAINING / "aria_tooluse_resolution_length_control_v1.jsonl"


def _pair(chosen: int, rejected: int, label: str = "tooluse_other") -> dict:
    return {"chosen": "c" * chosen, "rejected": "r" * rejected, "label": label,
            "prompt": [{"role": "user", "content": "q"}]}


class TestAgainstTheRealCorpora:
    def test_the_shipped_curriculum_is_blocked(self):
        if not DEFECTIVE.is_file():
            pytest.skip("v1 curriculum unavailable")
        result = gate.analyse(gate._load_jsonl(DEFECTIVE))
        assert result["grouped_by"] == ["decision_branch"]
        assert set(result["predictive_groups"]) == {
            "ambiguous_live", "no_match", "unique_live"}

    def test_the_length_controlled_rebuild_passes(self):
        if not FIXED.is_file():
            pytest.skip("length-controlled curriculum unavailable")
        result = gate.analyse(gate._load_jsonl(FIXED))
        assert result["predictive_groups"] == []

    def test_the_aggregate_would_have_missed_it(self):
        """The reason grouping is the whole game: across all 32 rows the skew is
        0.69, under any sane threshold, because the branches cancel."""
        if not DEFECTIVE.is_file():
            pytest.skip("v1 curriculum unavailable")
        rows = gate._load_jsonl(DEFECTIVE)
        shorter = sum(1 for r in rows if len(r["chosen"]) < len(r["rejected"]))
        overall = max(shorter, len(rows) - shorter) / len(rows)
        assert overall < gate.LENGTH_PREDICTIVE_SHARE, (
            "an ungrouped check would have passed the defective corpus"
        )


class TestTheGroupingIsReportedNotAssumed:
    def test_non_resolution_rows_group_by_label(self):
        rows = [_pair(10, 90, "axis_a") for _ in range(9)]
        result = gate.analyse(rows)
        assert result["grouped_by"] == ["label"]
        assert "axis_a" in result["groups"]

    def test_labels_are_kept_separate(self):
        rows = [_pair(10, 90, "axis_a") for _ in range(9)]
        rows += [_pair(90, 10, "axis_b") for _ in range(9)]
        result = gate.analyse(rows)
        assert set(result["groups"]) == {"axis_a", "axis_b"}


class TestTheThresholdBehaviour:
    def test_a_perfectly_skewed_group_is_predictive(self):
        rows = [_pair(10, 90, "axis") for _ in range(10)]
        assert gate.analyse(rows)["predictive_groups"] == ["axis"]

    def test_a_nine_of_ten_skew_is_still_predictive(self):
        rows = [_pair(10, 90, "axis") for _ in range(9)] + [_pair(90, 10, "axis")]
        assert gate.analyse(rows)["predictive_groups"] == ["axis"]

    def test_a_balanced_group_passes(self):
        rows = [_pair(10, 90, "axis") for _ in range(5)]
        rows += [_pair(90, 10, "axis") for _ in range(5)]
        assert gate.analyse(rows)["predictive_groups"] == []

    def test_a_thin_group_is_reported_underpowered_and_never_blocks(self):
        """A skew over 4 pairs is noise. Say so rather than block on it —
        or quietly certify it."""
        rows = [_pair(10, 90, "axis") for _ in range(4)]
        result = gate.analyse(rows)
        assert result["groups"]["axis"]["underpowered"] is True
        assert result["groups"]["axis"]["length_skew"] == 1.0
        assert result["predictive_groups"] == []


class TestTheDriverActuallyCallsIt:
    RUNNER = (pathlib.Path(__file__).resolve().parents[2]
              / "scripts/train/run_tooluse_dpo.sh")

    def test_the_paid_dpo_driver_runs_the_gate(self):
        runner = self.RUNNER.read_text(encoding="utf-8")
        assert "scripts.train.preflight_preference_confound" in runner, (
            "a gate nothing calls is not a gate"
        )

    def test_the_gate_blocks_the_cycle_rather_than_warning(self):
        runner = self.RUNNER.read_text(encoding="utf-8")
        line = next(l for l in runner.splitlines()
                    if "preflight_preference_confound" in l and l.startswith('"$PYBIN"'))
        assert "|| exit 3" in line

    def test_it_runs_before_the_pod_is_created(self):
        """Blocking after provisioning would still cost money."""
        runner = self.RUNNER.read_text(encoding="utf-8")
        gate_at = runner.index("preflight_preference_confound")
        create_at = runner.index("_create_v04_pod")
        assert gate_at < create_at
