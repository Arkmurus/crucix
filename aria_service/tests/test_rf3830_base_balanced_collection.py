"""R-F3830 capability tests for balanced raw-base failure collection."""
from collections import Counter
from pathlib import Path

import pytest

from scripts.train.build_balanced_tooluse_queue import (
    EXPECTED_LABELS,
    TARGET_LABELS,
    build_balanced_queue,
    select_novel_rows,
)


ROOT = Path(__file__).resolve().parents[2]


def _rows(per_label: int = 20) -> list[dict]:
    return [
        {"label": label, "subject": f"{label}-{index}"}
        for label in sorted(EXPECTED_LABELS)
        for index in range(per_label)
    ]


def test_balanced_queue_targets_failures_and_retains_every_axis() -> None:
    queue = build_balanced_queue(
        _rows(), eval_entities={"held-out"}, target_limit=16, retention_limit=6,
    )
    counts = Counter(row["label"] for row in queue)

    assert set(counts) == EXPECTED_LABELS
    assert all(counts[label] == 16 for label in TARGET_LABELS)
    assert all(
        counts[label] == 6 for label in EXPECTED_LABELS - TARGET_LABELS
    )
    assert counts["tooluse_person"] == 16
    assert len(queue) == 110


def test_person_match_is_targeted_before_dependent_multihop() -> None:
    assert "tooluse_person" in TARGET_LABELS
    assert "tooluse_multihop" in TARGET_LABELS


def test_delta_collection_contains_only_new_axis_subject_evidence() -> None:
    prior = [{"label": "tooluse_person", "subject": "Oleg Deripaska"}]
    current = prior + [
        {"label": "tooluse_person", "subject": "Petr Aven"},
        {"label": "tooluse_trace", "subject": "Oleg Deripaska"},
    ]
    assert select_novel_rows(current, prior) == current[1:]


def test_exclusion_precedes_cap_and_target_only_avoids_paid_retention_work() -> None:
    rows = _rows(per_label=4)
    excluded = {
        ("tooluse_contradiction", f"tooluse contradiction {index}")
        for index in range(2)
    }
    queue = build_balanced_queue(
        rows,
        eval_entities={"held-out"},
        target_limit=2,
        target_labels={"tooluse_contradiction"},
        excluded_evidence=excluded,
        only_target_axes=True,
    )
    assert [row["subject"] for row in queue] == [
        "tooluse_contradiction-2", "tooluse_contradiction-3",
    ]


def test_explicit_held_out_exclusion_advances_to_safe_rows_before_cap() -> None:
    rows = _rows(per_label=3)
    queue = build_balanced_queue(
        rows,
        eval_entities={"tooluse contradiction 0"},
        target_limit=2,
        target_labels={"tooluse_contradiction"},
        only_target_axes=True,
        exclude_held_out=True,
    )
    assert [row["subject"] for row in queue] == [
        "tooluse_contradiction-1", "tooluse_contradiction-2",
    ]


def test_measured_regression_axes_join_targets_without_dropping_defaults() -> None:
    queue = build_balanced_queue(
        _rows(), eval_entities={"held-out"}, target_limit=16, retention_limit=6,
        target_labels=TARGET_LABELS | {"tooluse_adverse", "tooluse_contradiction"},
    )
    counts = Counter(row["label"] for row in queue)
    assert counts["tooluse_adverse"] == 16
    assert counts["tooluse_contradiction"] == 16
    assert counts["tooluse_person"] == 16
    assert counts["tooluse_news_impact"] == 6


def test_queue_refuses_unknown_or_empty_target_axes() -> None:
    with pytest.raises(ValueError, match="unknown target"):
        build_balanced_queue(_rows(), eval_entities={"held-out"},
                             target_labels={"not_an_axis"})
    with pytest.raises(ValueError, match="empty"):
        build_balanced_queue(_rows(), eval_entities={"held-out"}, target_labels=set())


def test_balanced_queue_refuses_held_out_entity() -> None:
    rows = _rows()
    rows[0]["subject"] = "Held Out plc"
    with pytest.raises(ValueError, match="held-out"):
        build_balanced_queue(rows, eval_entities={"held out"})


def test_balanced_queue_refuses_a_missing_axis() -> None:
    rows = [row for row in _rows() if row["label"] != "tooluse_multihop"]
    with pytest.raises(ValueError, match="missing axes"):
        build_balanced_queue(rows, eval_entities={"held-out"})


def test_generation_driver_skips_adapter_only_in_explicit_base_mode() -> None:
    host = (ROOT / "scripts/train/run_tooluse_generation.sh").read_text(
        encoding="utf-8"
    )
    pod = (ROOT / "scripts/train/pod_tooluse_generate.sh").read_text(
        encoding="utf-8"
    )

    assert 'BASE_ONLY="${BASE_ONLY:-0}"' in host
    assert 'if [ "$BASE_ONLY" != 1 ]; then' in host
    assert 'POD_ENV="BASE_ONLY=$BASE_ONLY"' in host
    common_layout = host.index("'mkdir -p /workspace/checkpoints")
    state = host.index('echo "POD_ID=$POD_ID"')
    watchdog = host.index('arm_watchdog \\\n  "POD_ID=')
    upload = host.index('log "uploading validated serving adapter')
    adapter_branch = host.rfind('if [ "$BASE_ONLY" != 1 ]; then', 0, upload)
    assert common_layout < watchdog < state < adapter_branch < upload
    assert 'BASE_ONLY="${BASE_ONLY:-0}"' in pod
    assert '[ "$BASE_ONLY" = 1 ] || [ -f "$ADAPTER/adapter_config.json" ]' in pod
    assert 'SERVE_ADAPTER=""' in pod
