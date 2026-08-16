"""R-F4026 guards for pre-request resolution capture exclusion."""
import json
from pathlib import Path

import pytest

from scripts.train.build_tooluse_corpus import _norm_subject
from scripts.train.capture_resolution import select_capture_subjects


ROOT = Path(__file__).resolve().parents[2]


def test_subject_selection_excludes_before_limit_and_deduplicates_aliases() -> None:
    selected = select_capture_subjects(
        ["Held plc", "Novel Engineering plc", "Novel Engineering", "Second Group"],
        forbidden_subjects={_norm_subject("Held plc")},
        limit=2,
    )
    assert selected == ["Novel Engineering plc", "Second Group"]


def test_subject_selection_refuses_unchecked_or_empty_capture() -> None:
    with pytest.raises(ValueError, match="contamination is unchecked"):
        select_capture_subjects(["Novel Engineering"], forbidden_subjects=set())
    with pytest.raises(ValueError, match="no novel capture subjects"):
        select_capture_subjects(
            ["Held plc"], forbidden_subjects={_norm_subject("Held plc")},
        )


def test_real_subject_roster_is_unique_and_absent_from_protected_surfaces() -> None:
    subjects = [
        line.strip() for line in (
            ROOT / "data/training/tooluse_novel_resolution_subjects.txt"
        ).read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]
    protected_paths = [
        ROOT / "data/training/split_v1/eval.jsonl",
        ROOT / "data/training/split_v2/eval.jsonl",
        ROOT / "data/eval_frozen/aria_eval_500q.jsonl",
        ROOT / "data/training/aria_tooluse_curve_v5_probe.jsonl",
        ROOT / "data/training/tooluse_protected_axis_recovery_queue.jsonl",
    ]
    protected = {
        _norm_subject(str(json.loads(line).get("subject") or ""))
        for path in protected_paths
        for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    } - {""}
    normalized = [_norm_subject(subject) for subject in subjects]
    assert len(normalized) == len(set(normalized)) == 16
    assert not set(normalized) & protected


def test_cli_filters_subjects_before_live_capture() -> None:
    source = (ROOT / "scripts/train/capture_resolution.py").read_text(
        encoding="utf-8",
    )
    assert source.index("select_capture_subjects(") < source.index(
        "asyncio.run(capture(",
    )
