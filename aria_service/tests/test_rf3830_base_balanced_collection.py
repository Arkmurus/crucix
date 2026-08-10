"""R-F3830 capability tests for balanced raw-base failure collection."""
from collections import Counter
from pathlib import Path

import pytest

from scripts.train.build_balanced_tooluse_queue import (
    EXPECTED_LABELS,
    TARGET_LABELS,
    build_balanced_queue,
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
    assert len(queue) == 100


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
    watchdog = host.index('grep -q ARMED')
    upload = host.index('log "uploading validated serving adapter')
    adapter_branch = host.rfind('if [ "$BASE_ONLY" != 1 ]; then', 0, upload)
    assert common_layout < watchdog < state < adapter_branch < upload
    assert 'BASE_ONLY="${BASE_ONLY:-0}"' in pod
    assert '[ "$BASE_ONLY" = 1 ] || [ -f "$ADAPTER/adapter_config.json" ]' in pod
    assert 'SERVE_ADAPTER=""' in pod
