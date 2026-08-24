"""Build a fail-closed ten-axis coverage and mastery ledger (R-F3890)."""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path

from scripts.train.build_mixed_tooluse_cycle import ALL_AXES
from scripts.train.build_registry_depth_corpus import REGISTRY_AXES

# R-F4274 — the ten became thirteen. Both directions still fail closed: a report
# MISSING any of the original ten is incomplete, and one carrying an axis nobody
# declared is a harness measuring something nobody chose. What is no longer fatal
# is simply having MORE coverage than the day this check was written.
KNOWN_AXES = ALL_AXES | REGISTRY_AXES
from scripts.train.eval_tooluse import report_consistency_error


def _axis_map(report: dict, name: str) -> dict[str, dict]:
    rows = report.get("rows") or []
    if report.get("complete") is not True or len(rows) != int(report.get("total", -1)):
        raise ValueError(f"{name} report is incomplete")
    error = report_consistency_error(report)
    if error:
        raise ValueError(f"{name} report summary is inconsistent: {error}")
    axes = {str(row.get("label") or ""): row for row in report.get("per_axis") or []}
    present = set(axes)
    if not ALL_AXES <= present:
        raise ValueError(
            f"{name} report is missing core axes: {sorted(ALL_AXES - present)}")
    if present - KNOWN_AXES:
        raise ValueError(
            f"{name} report carries undeclared axes: {sorted(present - KNOWN_AXES)}")
    for label, axis in axes.items():
        total, honest = int(axis.get("total", -1)), int(axis.get("honest", -1))
        if total < 1 or honest < 0 or honest > total:
            raise ValueError(f"{name} has invalid counts for {label}")
    return axes


def build_ledger(train: list[dict], calibration: dict,
                 incumbent: dict, candidate: dict) -> dict:
    """Separate evidence coverage from measured mastery and promotion safety."""
    train_counts = Counter(str(row.get("label") or "") for row in train)
    if set(train_counts) != ALL_AXES or any(train_counts[axis] < 1 for axis in ALL_AXES):
        raise ValueError("training signal does not cover all ten axes")
    cal = _axis_map(calibration, "calibration")
    before = _axis_map(incumbent, "incumbent")
    after = _axis_map(candidate, "candidate")

    axes: list[dict] = []
    for label in sorted(ALL_AXES):
        if int(before[label]["total"]) != int(after[label]["total"]):
            raise ValueError(f"held-out denominator changed for {label}")
        total = int(after[label]["total"])
        honest = int(after[label]["honest"])
        delta = honest - int(before[label]["honest"])
        axes.append({
            "label": label,
            "train_rows": train_counts[label],
            "calibration": {"honest": int(cal[label]["honest"]),
                            "total": int(cal[label]["total"])},
            "held_out": {"incumbent_honest": int(before[label]["honest"]),
                         "candidate_honest": honest, "total": total,
                         "delta": delta},
            "evidence_covered": True,
            "mastered": honest == total,
            "regressed": delta < 0,
            "remaining_failures": total - honest,
        })

    regressions = [axis["label"] for axis in axes if axis["regressed"]]
    priorities = sorted(
        (axis for axis in axes if not axis["mastered"]),
        key=lambda axis: (not axis["regressed"], -axis["remaining_failures"], axis["label"]),
    )
    candidate_honest = int(candidate["honest"])
    incumbent_honest = int(incumbent["honest"])
    mastered = sum(axis["mastered"] for axis in axes)
    return {
        "complete": True,
        "axis_count": len(axes),
        "evidence_coverage": {"covered_axes": len(axes), "total_axes": len(axes),
                              "percent": 1.0},
        "held_out_mastery": {"mastered_axes": mastered, "total_axes": len(axes),
                             "percent": mastered / len(axes)},
        "held_out_total": {"incumbent_honest": incumbent_honest,
                           "candidate_honest": candidate_honest,
                           "total": int(candidate["total"]),
                           "delta": candidate_honest - incumbent_honest},
        "promotion": {"eligible": candidate_honest > incumbent_honest and not regressions,
                      "zero_regression_required": True, "regressions": regressions},
        "priority_order": [axis["label"] for axis in priorities],
        "axes": axes,
    }


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--train", required=True, type=Path)
    parser.add_argument("--calibration", required=True, type=Path)
    parser.add_argument("--incumbent", required=True, type=Path)
    parser.add_argument("--candidate", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)
    ledger = build_ledger(_load_jsonl(args.train), _load_json(args.calibration),
                          _load_json(args.incumbent), _load_json(args.candidate))
    ledger["input_sha256"] = {name: _sha(path) for name, path in (
        ("train", args.train), ("calibration", args.calibration),
        ("incumbent", args.incumbent), ("candidate", args.candidate))}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(ledger, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({key: ledger[key] for key in (
        "evidence_coverage", "held_out_mastery", "held_out_total", "promotion",
        "priority_order")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
