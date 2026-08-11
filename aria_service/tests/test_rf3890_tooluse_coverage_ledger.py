"""R-F3890 tests for explicit coverage, mastery, and ordering."""
import pytest

from scripts.train.build_mixed_tooluse_cycle import ALL_AXES
from scripts.train.build_tooluse_coverage_ledger import build_ledger


def _report(scores: dict[str, tuple[int, int]]) -> dict:
    axes = [{"label": axis, "honest": scores[axis][0], "total": scores[axis][1]}
            for axis in sorted(ALL_AXES)]
    rows = [{"label": axis} for axis in sorted(ALL_AXES)
            for _ in range(scores[axis][1])]
    return {"complete": True, "total": len(rows),
            "honest": sum(honest for honest, _ in scores.values()),
            "per_axis": axes, "rows": rows}


def test_ledger_separates_full_evidence_coverage_from_mastery() -> None:
    train = [{"label": axis} for axis in ALL_AXES]
    baseline = {axis: (2, 3) for axis in ALL_AXES}
    candidate = {axis: (3, 3) for axis in ALL_AXES}
    candidate["tooluse_adverse"] = (1, 3)
    ledger = build_ledger(train, _report(candidate), _report(baseline), _report(candidate))
    assert ledger["evidence_coverage"] == {"covered_axes": 10, "total_axes": 10,
                                            "percent": 1.0}
    assert ledger["held_out_mastery"]["mastered_axes"] == 9
    assert ledger["promotion"]["eligible"] is False
    assert ledger["promotion"]["regressions"] == ["tooluse_adverse"]
    assert ledger["priority_order"][0] == "tooluse_adverse"


def test_ledger_refuses_missing_axis_and_changed_denominator() -> None:
    train = [{"label": axis} for axis in ALL_AXES]
    scores = {axis: (3, 3) for axis in ALL_AXES}
    with pytest.raises(ValueError, match="training signal"):
        build_ledger(train[:-1], _report(scores), _report(scores), _report(scores))
    changed = dict(scores)
    changed["tooluse_multihop"] = (3, 4)
    with pytest.raises(ValueError, match="denominator changed"):
        build_ledger(train, _report(scores), _report(scores), _report(changed))


def test_ledger_promotes_only_strict_gain_without_regression() -> None:
    train = [{"label": axis} for axis in ALL_AXES]
    baseline = {axis: (2, 3) for axis in ALL_AXES}
    candidate = dict(baseline)
    candidate["tooluse_multihop"] = (3, 3)
    ledger = build_ledger(train, _report(candidate), _report(baseline), _report(candidate))
    assert ledger["promotion"]["eligible"] is True
    assert ledger["held_out_total"]["delta"] == 1
