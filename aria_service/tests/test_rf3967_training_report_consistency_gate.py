"""R-F3967 / C-56 — stale report summaries must never steer training.

The Phoenix v2 artifact measured 87 honest rows out of 88, but its embedded
per-axis block summed to 81 honest rows.  Promotion code trusted both views at
once: the headline for aggregate gain and the stale axes for regression gates.
These capability tests drive the real consumers that make those decisions.
"""
from __future__ import annotations

import pytest

from scripts.train.compound_tooluse_cycle import promotion_verdict
from scripts.train.build_positive_curve_assets import deficit_weighted_sft
from scripts.train.learning_curve_gate import progression_verdict


def _report(*, honest: int, axis_honest: int) -> dict:
    rows = [
        {"label": "tooluse_adverse", "subject": f"entity-{index}",
         "honest": index < honest, "errors": [] if index < honest else ["failure"]}
        for index in range(10)
    ]
    return {
        "complete": True,
        "total": 10,
        "honest": honest,
        "honest_rate": honest / 10,
        "per_axis": [{
            "label": "tooluse_adverse", "total": 10,
            "honest": axis_honest, "honest_rate": axis_honest / 10,
        }],
        "failure_classes": [["failure", 10 - axis_honest]],
        "rows": rows,
    }


def test_promotion_refuses_headline_and_axis_disagreement() -> None:
    incumbent = _report(honest=8, axis_honest=8)
    phoenix_shape = _report(honest=9, axis_honest=3)

    with pytest.raises(ValueError, match="summary is inconsistent"):
        promotion_verdict(incumbent, phoenix_shape)


def test_learning_curve_refuses_headline_and_axis_disagreement() -> None:
    before = _report(honest=8, axis_honest=8)
    phoenix_shape = _report(honest=9, axis_honest=3)

    verdict = progression_verdict(before, phoenix_shape, {"tooluse_adverse"})

    assert verdict == {"pass": False, "reason": "after_report_inconsistent"}


def test_weighting_refuses_to_train_from_stale_axis_deficits() -> None:
    phoenix_shape = _report(honest=9, axis_honest=3)

    with pytest.raises(ValueError, match="summary is inconsistent"):
        deficit_weighted_sft([], phoenix_shape, quota=10)
