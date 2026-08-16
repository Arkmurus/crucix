"""R-F4055 guards for a genuinely fresh contradiction-capture pool."""
from __future__ import annotations

import json
import hashlib
from pathlib import Path

from scripts.train.build_tooluse_corpus import (
    _independent_sources,
    _matches,
    _norm_subject,
    validate_trace,
)
from scripts.train.capture_contradiction import select_capture_subjects


ROOT = Path(__file__).resolve().parents[2]
SUBJECTS = ROOT / "data/training/tooluse_fresh_contradiction_subjects_v3.txt"
PROTECTED = (
    ROOT / "data/training/split_v1/eval.jsonl",
    ROOT / "data/training/split_v2/eval.jsonl",
    ROOT / "data/training/split_v3/eval.jsonl",
    ROOT / "data/training/aria_tooluse_curve_v5_probe.jsonl",
    ROOT / "data/training/tooluse_protected_axis_recovery_queue.jsonl",
    ROOT / "data/training/tooluse_hard_contradiction_queue.jsonl",
    ROOT / "data/training/aria_tooluse_contradiction_novel_v1.jsonl",
    ROOT / "data/training/aria_tooluse_contradiction_novel_v2.jsonl",
)


def _subjects(path: Path) -> list[str]:
    if path.suffix == ".txt":
        return [
            line.strip() for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.startswith("#")
        ]
    return [
        str(json.loads(line).get("subject") or "")
        for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def test_fresh_pool_is_unique_and_disjoint_before_live_requests() -> None:
    subjects = _subjects(SUBJECTS)
    protected = {
        _norm_subject(subject)
        for path in PROTECTED if path.exists() for subject in _subjects(path)
    } - {""}

    selected = select_capture_subjects(
        subjects, forbidden_subjects=protected, limit=len(subjects),
    )

    assert len(subjects) == len(selected) == 20
    assert len({_norm_subject(subject) for subject in selected}) == 20
    assert not {_norm_subject(subject) for subject in selected} & protected


def test_capture_cli_supports_all_required_pre_request_exclusions() -> None:
    source = (ROOT / "scripts/train/capture_contradiction.py").read_text(
        encoding="utf-8",
    )
    assert 'ap.add_argument("--subjects-file"' in source
    assert 'ap.add_argument("--exclude-file"' in source
    assert source.index("select_capture_subjects(") < source.index(
        "asyncio.run(capture(",
    )


def test_live_capture_is_valid_disjoint_and_grounded() -> None:
    queue = ROOT / "data/training/aria_tooluse_contradiction_fresh_v3.jsonl"
    rows = [json.loads(line) for line in queue.read_text(encoding="utf-8").splitlines()]
    protected = {
        _norm_subject(subject)
        for path in PROTECTED if path.exists() for subject in _subjects(path)
    } - {""}

    assert len(rows) == 14
    assert {row["label"] for row in rows} == {"tooluse_contradiction"}
    assert not {_norm_subject(row["subject"]) for row in rows} & protected
    for row in rows:
        assert validate_trace(row) == []
        tools = {
            message["name"]: json.loads(message["content"])
            for message in row["messages"] if message.get("role") == "tool"
        }
        assert tools["screen"]["sanctions"]["matched"] is False
        assert _matches(tools["screen"]) == []
        assert _independent_sources(tools["web_search"])


def test_generation_launcher_pins_inputs_and_cannot_train() -> None:
    queue = ROOT / "data/training/aria_tooluse_contradiction_fresh_v3.jsonl"
    launcher = (
        ROOT / "scripts/train/run_tooluse_fresh_contradiction_v3_generation.sh"
    ).read_text(encoding="utf-8")

    assert hashlib.sha256(queue.read_bytes()).hexdigest() in launcher
    assert "223ba1dc99d0e65aafdfa3f5190d57e0e8dfdd4013f9fab5be3994af63384998" in launcher
    assert "exec bash scripts/train/run_tooluse_generation.sh" in launcher
    assert "run_tooluse_dpo.sh" not in launcher
    assert "run_tooluse_sft" not in launcher


def test_generation_launcher_rotates_secure_capacity_under_price_cap() -> None:
    launcher = (
        ROOT / "scripts/train/run_tooluse_fresh_contradiction_v3_generation.sh"
    ).read_text(encoding="utf-8")

    assert "REPO=\"$ROOT\"" in launcher
    assert "ARIA_POD_CREATE_API=graphql" in launcher
    assert "ARIA_MAX_GPU_HOURLY_USD=1.60" in launcher
