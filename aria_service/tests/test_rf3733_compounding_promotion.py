"""R-F3733 capability tests for strict checkpoint compounding."""
from scripts.train.compound_tooluse_cycle import build_retention_curriculum, promotion_verdict
from pathlib import Path


def _report(total, honest, axes):
    return {"total": total, "honest": honest, "per_axis": [
        {"label": label, "total": n, "honest": score} for label, (score, n) in axes.items()]}


def test_headline_gain_cannot_hide_a_security_axis_regression():
    incumbent = _report(20, 15, {"sanctions": (8, 10), "research": (7, 10)})
    candidate = _report(20, 16, {"sanctions": (7, 10), "research": (9, 10)})
    result = promotion_verdict(incumbent, candidate)
    assert result["promote"] is False
    assert result["regressions"] == [
        {"label": "sanctions", "before": 8, "after": 7, "lost": 1, "n": 10}]


def test_true_compounding_requires_gain_and_retention_on_every_axis():
    incumbent = _report(20, 15, {"sanctions": (8, 10), "research": (7, 10)})
    candidate = _report(20, 17, {"sanctions": (8, 10), "research": (9, 10)})
    assert promotion_verdict(incumbent, candidate)["promote"] is True


def test_curriculum_replays_only_regressed_training_axes():
    train = [{"label": "sanctions", "id": 1}, {"label": "research", "id": 2}]
    verdict = {"regressions": [{"label": "sanctions"}]}
    assert build_retention_curriculum(train, verdict) == [train[0], train[1], train[0]]


def test_core_cycle_does_not_hide_its_verdict_behind_optional_generation():
    launch = (Path(__file__).resolve().parents[2] / "scripts" / "train" /
              "tooluse_launch.sh").read_text(encoding="utf-8")
    assert 'GEN_TRAIN="${GEN_TRAIN:-0}"' in launch
    assert "GEN_TRAIN=$GEN_TRAIN setsid nohup" in launch
