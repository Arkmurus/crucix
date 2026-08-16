"""R-F4026 guards for pre-request resolution capture exclusion."""
import json
from pathlib import Path

import pytest

from scripts.train.build_tooluse_corpus import _norm_subject
from scripts.train.capture_resolution import select_capture_subjects
from scripts.train.preflight_cycle import _golden_subjects, check_contamination


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


def test_generation_queue_passes_the_real_golden_contamination_gate() -> None:
    queue = [
        json.loads(line) for line in (
            ROOT / "data/training/tooluse_novel_resolution_generation_queue.jsonl"
        ).read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    result = check_contamination(
        queue, _golden_subjects(ROOT / "data/eval_frozen/aria_eval_500q.jsonl"),
    )
    assert len(queue) == 15
    assert result.status == "PASS", result.detail


def test_live_capture_provenance_and_launcher_are_pinned() -> None:
    raw_path = ROOT / "data/training/aria_tooluse_resolution_novel_v1.jsonl"
    rows = [
        json.loads(line) for line in raw_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(rows) == 16
    assert {row["label"] for row in rows} == {"tooluse_resolution"}
    assert {row["source"] for row in rows} == {"replayed_real_tool_execution"}

    import hashlib
    launcher = (
        ROOT / "scripts/train/run_tooluse_novel_resolution_generation.sh"
    ).read_text(encoding="utf-8")
    queue = ROOT / "data/training/tooluse_novel_resolution_generation_queue.jsonl"
    assert hashlib.sha256(queue.read_bytes()).hexdigest() in launcher
    assert "aria_tooluse_citation_phoenix_v3_failed_candidate.tgz" in launcher
    assert "exec bash scripts/train/run_tooluse_generation.sh" in launcher
    assert "run_tooluse_dpo.sh" not in launcher
