"""R-F3898 capability tests for positive full-replay continuation."""
import pytest

from scripts.train.build_mixed_tooluse_cycle import ALL_AXES
from scripts.train.build_positive_replay_curriculum import build_replay_curriculum


def _row(label: str, subject: str) -> dict:
    answer = f"{subject} completed the full chain [from example.com]"
    return {"label": label, "subject": subject,
            "messages": [{"role": "assistant", "content": answer}]}


def test_full_parent_replay_precedes_delta_and_retains_every_axis() -> None:
    parent = [_row(axis, f"parent-{axis}") for axis in sorted(ALL_AXES)]
    delta = [_row(axis, f"delta-{axis}") for axis in sorted(ALL_AXES)]
    rows, manifest = build_replay_curriculum(parent, delta, set())
    assert rows[:len(parent)] == parent
    assert rows[len(parent):] == delta
    assert manifest["all_axes_retained"] is True
    assert manifest["dpo_rows"] == 0


@pytest.mark.parametrize("answer", [
    "Acme [from credibility: 5]",
    "Acme [from aria_search]",
    "Acme [from memory: unsupported]",
])
def test_replay_rejects_observed_citation_drift(answer: str) -> None:
    parent = [_row(axis, f"parent-{axis}") for axis in sorted(ALL_AXES)]
    parent[0]["messages"][-1]["content"] = answer
    with pytest.raises(ValueError, match="invalid citation token"):
        build_replay_curriculum(parent, [_row("tooluse_trace", "delta")], set())


def test_replay_rejects_multihop_answer_that_drops_company() -> None:
    parent = [_row(axis, f"parent-{axis}") for axis in sorted(ALL_AXES)]
    multihop = next(row for row in parent if row["label"] == "tooluse_multihop")
    multihop["messages"][-1]["content"] = "Amanda Miller is clear [from ofac_sdn]"
    with pytest.raises(ValueError, match="multihop answer omits subject"):
        build_replay_curriculum(parent, [_row("tooluse_trace", "delta")], set())


def test_replay_refuses_heldout_entity() -> None:
    parent = [_row(axis, f"parent-{axis}") for axis in sorted(ALL_AXES)]
    with pytest.raises(ValueError, match="held-out or golden"):
        build_replay_curriculum(parent, [_row("tooluse_trace", "Held Out plc")],
                                {"held out"})
