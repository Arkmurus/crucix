"""Build a chosen-only resolution correction from measured held-out failures.

The failed held-out subjects are evidence for which contracts need reinforcement,
never training material.  Correction examples must come from disjoint, genuine
preference rows and are appended to a full parent replay to retain every axis.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path

from scripts.train.build_protected_positive_correction import build_positive_rows
from scripts.train.build_mixed_tooluse_cycle import ALL_AXES
from scripts.train.build_tooluse_corpus import _norm_subject, validate_trace


_SELECTION_ERROR = "did not select the resolved company"
_CLARIFICATION_ERROR = "did not ask for clarification"


def _load_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def measured_resolution_contracts(report: dict) -> Counter[str]:
    """Count measured selection and clarification failures in a complete report."""
    rows = report.get("rows") or []
    if report.get("complete") is not True or len(rows) != int(report.get("total", -1)):
        raise ValueError("failed report is incomplete or internally inconsistent")
    counts: Counter[str] = Counter()
    for row in rows:
        if row.get("label") != "tooluse_resolution" or row.get("honest") is True:
            continue
        errors = " ".join(str(error) for error in row.get("errors") or [])
        if _SELECTION_ERROR in errors:
            counts["select_resolved_company"] += 1
        if _CLARIFICATION_ERROR in errors:
            counts["ask_for_clarification"] += 1
    missing = {
        contract
        for contract in ("select_resolved_company", "ask_for_clarification")
        if not counts[contract]
    }
    if missing:
        raise ValueError(f"measured report lacks required failure contracts: {sorted(missing)}")
    return counts


def build_correction(
    parent: list[dict], preferences: list[dict], *, forbidden_subjects: set[str],
) -> tuple[list[dict], list[dict]]:
    """Append disjoint validator-passing chosen responses to the complete parent."""
    parent_axes = {str(row.get("label") or "") for row in parent}
    if parent_axes != ALL_AXES:
        raise ValueError(f"parent replay does not retain all axes: {sorted(parent_axes)}")
    parent_errors = []
    for row in parent:
        errors = validate_trace(row)
        if errors:
            parent_errors.append((str(row.get("subject") or ""), errors))
    if parent_errors:
        raise ValueError(f"parent replay fails the corpus validator: {parent_errors[:3]}")
    positive = build_positive_rows(
        preferences,
        forbidden_subjects=forbidden_subjects,
        required_axes=frozenset({"tooluse_resolution"}),
    )
    errors = []
    for row in positive:
        row_errors = validate_trace(row)
        if row_errors:
            errors.append((str(row.get("subject") or ""), row_errors))
    if errors:
        raise ValueError(f"chosen correction rows fail the corpus validator: {errors[:3]}")
    return [*parent, *positive], positive


def main(argv: list[str] | None = None) -> int:
    """Build the replay asset and a hash-pinned audit manifest."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--parent-sft", type=Path, required=True)
    parser.add_argument("--preferences", type=Path, required=True)
    parser.add_argument("--failed-report", type=Path, required=True)
    parser.add_argument("--eval", type=Path, required=True)
    parser.add_argument("--golden", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args(argv)

    report = json.loads(args.failed_report.read_text(encoding="utf-8"))
    contracts = measured_resolution_contracts(report)
    eval_rows = _load_jsonl(args.eval)
    golden_rows = _load_jsonl(args.golden)
    forbidden = {
        _norm_subject(str(row.get("subject") or ""))
        for row in [*eval_rows, *golden_rows]
    } - {""}
    combined, positive = build_correction(
        _load_jsonl(args.parent_sft),
        _load_jsonl(args.preferences),
        forbidden_subjects=forbidden,
    )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in combined),
        encoding="utf-8",
        newline="\n",
    )
    manifest = {
        "complete": True,
        "policy": "measured_failure_disjoint_chosen_only_full_parent_replay",
        "parent_rows": len(combined) - len(positive),
        "correction_rows": len(positive),
        "total_rows": len(combined),
        "measured_failure_contracts": dict(sorted(contracts.items())),
        "heldout_subjects_used_for_training": False,
        "input_sha256": {
            "parent_sft": _sha(args.parent_sft),
            "preferences": _sha(args.preferences),
            "failed_report": _sha(args.failed_report),
            "eval": _sha(args.eval),
            "golden": _sha(args.golden),
        },
        "output_sha256": _sha(args.output),
    }
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps(manifest, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
