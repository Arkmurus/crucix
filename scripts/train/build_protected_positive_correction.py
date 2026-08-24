"""Build chosen-only protected-axis SFT from genuine preference evidence.

R-F4049 converts only validator-passing chosen responses from a disjoint DPO
artifact.  It never trains on rejected responses or the failed candidate.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path

from scripts.train.build_mixed_tooluse_cycle import (
    _load_jsonl,
    _subjects,
    validate_protected_axis_evidence,
)


def build_positive_rows(
    dpo_rows: list[dict], *, forbidden_subjects: set[str], required_axes: frozenset[str],
) -> list[dict]:
    """Return validated chosen responses in the corpus's SFT message schema."""
    validate_protected_axis_evidence(
        dpo_rows,
        forbidden_subjects=forbidden_subjects,
        required_axes=required_axes,
    )
    positive: list[dict] = []
    for row in dpo_rows:
        prompt = row["prompt"]
        if prompt[-1].get("role") == "assistant":
            raise ValueError("DPO prompt already ends with an assistant response")
        positive.append({
            "messages": [*prompt, {"role": "assistant", "content": row["chosen"]}],
            "subject": row["subject"],
            "label": row["label"],
        })
    return positive


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main(argv: list[str] | None = None) -> int:
    """Build the correction artifact and its reproducibility manifest."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--dpo", type=Path, required=True)
    parser.add_argument("--eval", type=Path, required=True)
    parser.add_argument("--golden", type=Path, required=True)
    parser.add_argument("--axes", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args(argv)

    required_axes = frozenset(x.strip() for x in args.axes.split(",") if x.strip())
    forbidden = _subjects(_load_jsonl(args.eval)) | _subjects(_load_jsonl(args.golden))
    rows = build_positive_rows(
        _load_jsonl(args.dpo),
        forbidden_subjects=forbidden,
        required_axes=required_axes,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
        newline="\n",
    )
    counts = Counter(str(row["label"]) for row in rows)
    manifest = {
        "complete": True,
        "policy": "genuine_chosen_only_protected_correction",
        "rows": len(rows),
        "axis_counts": dict(sorted(counts.items())),
        "required_axes": sorted(required_axes),
        "dpo_sha256": _sha(args.dpo),
        "eval_sha256": _sha(args.eval),
        "golden_sha256": _sha(args.golden),
        "output_sha256": _sha(args.output),
    }
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps(manifest, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
