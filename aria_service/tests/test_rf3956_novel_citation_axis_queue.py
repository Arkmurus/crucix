"""R-F3956: CLI builds a novel queue restricted to measured citation axes."""
from __future__ import annotations

import json
from pathlib import Path

from scripts.train.build_balanced_tooluse_queue import EXPECTED_LABELS, main


def _write(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def test_rf3956_cli_emits_only_novel_requested_axes(tmp_path: Path) -> None:
    train = tmp_path / "train.jsonl"
    held = tmp_path / "eval.jsonl"
    prior = tmp_path / "prior.jsonl"
    out = tmp_path / "out.jsonl"
    rows = [
        {"label": label, "subject": f"{label} old"}
        for label in sorted(EXPECTED_LABELS)
    ] + [
        {"label": label, "subject": f"{label} novel"}
        for label in ("tooluse_adverse", "tooluse_news_impact", "tooluse_person")
    ]
    _write(train, rows)
    _write(held, [{"label": "tooluse_adverse", "subject": "Held Entity"}])
    _write(prior, rows[:len(EXPECTED_LABELS)])

    assert main([
        "--train", str(train), "--eval-file", str(held), "--out", str(out),
        "--target-limit", "99", "--retention-limit", "1",
        "--target-axis", "tooluse_adverse",
        "--target-axis", "tooluse_news_impact",
        "--target-axis", "tooluse_person",
        "--exclude-file", str(prior),
    ]) == 0

    emitted = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    assert {row["label"] for row in emitted} == {
        "tooluse_adverse", "tooluse_news_impact", "tooluse_person",
    }
    assert all(row["subject"].endswith("novel") for row in emitted)


def test_rf3956_launcher_pins_novel_queue_and_accepted_parent() -> None:
    root = Path(__file__).resolve().parents[2]
    queue = root / "data/training/tooluse_citation_phoenix_v2_generation_queue.jsonl"
    launcher = (root / "scripts/train/run_tooluse_citation_phoenix_v2_generation.sh"
                ).read_text(encoding="utf-8")

    import hashlib
    assert hashlib.sha256(queue.read_bytes()).hexdigest() in launcher
    assert "aria_tooluse_curve_sft_v5.tgz" in launcher
    assert "citation_contract_v10_calibration_child" not in launcher
    assert "exec bash scripts/train/run_tooluse_generation.sh" in launcher
