"""R-F4053 capability coverage for positive-SFT curve-gate imports."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _report(path: Path) -> None:
    rows = [
        {"label": "tooluse_adverse", "subject": "Alpha", "honest": True},
        {"label": "tooluse_contradiction", "subject": "Beta", "honest": True},
        {"label": "tooluse_news_impact", "subject": "Gamma", "honest": True},
        {"label": "tooluse_resolution", "subject": "Delta", "honest": True},
    ]
    path.write_text(json.dumps({
        "complete": True,
        "total": len(rows),
        "honest": len(rows),
        "honest_rate": 1.0,
        # R-F4244 — the gate refuses to compare across scorer generations, so
        # both sides of a valid comparison must declare the same one.
        "scorer_version": "test-scorer-v1",
        "rows": rows,
        "per_axis": [
            {"label": row["label"], "total": 1, "honest": 1, "honest_rate": 1.0}
            for row in rows
        ],
    }), encoding="utf-8")


def test_real_positive_runner_uses_import_safe_curve_gate(tmp_path: Path) -> None:
    runner = (ROOT / "scripts/train/pod_tooluse_sft_continue.sh").read_text(
        encoding="utf-8"
    )
    assert "python -m scripts.train.learning_curve_gate" in runner
    assert 'python "$SCRIPTS/learning_curve_gate.py"' not in runner

    before = tmp_path / "before.json"
    after = tmp_path / "after.json"
    verdict = tmp_path / "verdict.json"
    _report(before)
    _report(after)
    result = subprocess.run(
        [
            sys.executable, "-m", "scripts.train.learning_curve_gate",
            "--before", str(before), "--after", str(after),
            "--verdict-out", str(verdict),
            "--protected-axis", "tooluse_adverse",
            "--protected-axis", "tooluse_contradiction",
            "--protected-axis", "tooluse_news_impact",
            "--protected-axis", "tooluse_resolution",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(verdict.read_text(encoding="utf-8"))["pass"] is True


def test_evaluation_recovery_uses_same_import_safe_gate() -> None:
    runner = (
        ROOT / "scripts/train/pod_tooluse_calibration_recovery.sh"
    ).read_text(encoding="utf-8")
    assert "python -m scripts.train.learning_curve_gate" in runner
    assert 'python "$SCRIPTS/learning_curve_gate.py"' not in runner
