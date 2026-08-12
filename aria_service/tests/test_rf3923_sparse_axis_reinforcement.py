"""R-F3923: repaired tool-use contracts receive enough measured-axis replay."""
from __future__ import annotations

from collections import Counter

import pytest

from scripts.train import build_positive_replay_curriculum as replay


def _row(label: str, subject: str) -> dict:
    answer = subject if label == "tooluse_multihop" else "Evidence handled without a citation."
    return {
        "subject": subject,
        "label": label,
        "messages": [
            {"role": "system", "content": "contract"},
            {"role": "assistant", "content": answer},
        ],
    }


def test_real_curriculum_builder_reinforces_sparse_contract_axes() -> None:
    axes = sorted(replay.ALL_AXES)
    parent = [_row(axis, f"parent-{axis}") for axis in axes]
    delta = [_row(axis, f"delta-{axis}") for axis in axes]
    repaired = {"tooluse_adverse", "tooluse_contradiction", "tooluse_news_impact"}

    rows, manifest = replay.build_replay_curriculum(
        parent,
        delta,
        set(),
        reinforce=repaired,
        reinforcement_floor=6,
    )

    counts = Counter(row["label"] for row in rows)
    assert {axis: counts[axis] for axis in repaired} == {axis: 6 for axis in repaired}
    assert all(counts[axis] == 2 for axis in replay.ALL_AXES - repaired)
    assert manifest["reinforced_axes"] == sorted(repaired)
    assert manifest["reinforcement_floor"] == 6


def test_reinforcement_rejects_an_axis_absent_from_the_real_curriculum() -> None:
    with pytest.raises(ValueError, match="cannot reinforce absent axis"):
        replay.reinforce_axes(
            [_row("tooluse_adverse", "Acme")], {"tooluse_news_impact"}, 3)
