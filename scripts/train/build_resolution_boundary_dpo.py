"""Build a deduplicated DPO curriculum across entity-resolution branches.

R-F4135 replaces duplicate weighting with explicit decision-state coverage.  Every
chosen response must pass the real corpus validator, every rejected response must
fail it, and evaluation subjects are forbidden from entering the training asset.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.train.build_tooluse_corpus import (
    _norm_subject,
    resolve_company,
    validate_trace,
)


MINIMUM_BRANCH_COUNTS = {
    "unique_live": 8,
    "ambiguous_live": 8,
    "no_match": 8,
    "dissolved_only": 2,
}


def _load_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _trace(row: dict, completion: str) -> dict:
    prompt = row.get("prompt")
    if not isinstance(prompt, list):
        raise ValueError(f"preference for {row.get('subject')!r} has no message prompt")
    return {
        "messages": [*prompt, {"role": "assistant", "content": completion}],
        "label": "tooluse_resolution",
        "subject": row.get("subject"),
    }


def resolution_branch(row: dict) -> str:
    """Return the resolver state exercised by one preference row."""
    prompt = row.get("prompt") or []
    payloads = []
    for message in prompt:
        if (isinstance(message, dict) and message.get("role") == "tool"
                and message.get("name") == "companies_house_search"):
            try:
                payloads.append(json.loads(message.get("content") or "{}"))
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"invalid registry payload for {row.get('subject')!r}"
                ) from exc
    if len(payloads) != 1:
        raise ValueError(
            f"preference for {row.get('subject')!r} must have one registry payload"
        )
    chosen, reason, ambiguous = resolve_company(
        str(row.get("subject") or ""), payloads[0].get("results") or [],
    )
    if ambiguous:
        return "ambiguous_live"
    if chosen is not None:
        return "unique_live"
    if "the only name match is" in reason:
        return "dissolved_only"
    return "no_match"


def build_curriculum(
    rows: list[dict], *, forbidden_subjects: set[str],
    minimum_branch_counts: dict[str, int] = MINIMUM_BRANCH_COUNTS,
) -> tuple[list[dict], Counter[str]]:
    """Validate and deduplicate genuine preferences, then enforce branch coverage."""
    selected: dict[str, dict] = {}
    for row in rows:
        subject = _norm_subject(str(row.get("subject") or ""))
        if not subject:
            raise ValueError("resolution preference has an empty subject")
        if subject in forbidden_subjects:
            raise ValueError(f"held-out subject entered training preferences: {subject}")
        chosen = row.get("chosen")
        rejected = row.get("rejected")
        if not isinstance(chosen, str) or not chosen:
            raise ValueError(f"preference for {subject!r} has no chosen completion")
        if not isinstance(rejected, str) or not rejected:
            raise ValueError(f"preference for {subject!r} has no rejected completion")
        chosen_errors = validate_trace(_trace(row, chosen))
        if chosen_errors:
            raise ValueError(f"chosen completion for {subject!r} is invalid: {chosen_errors}")
        rejected_errors = validate_trace(_trace(row, rejected))
        if not rejected_errors:
            raise ValueError(f"rejected completion for {subject!r} passes the validator")
        previous = selected.get(subject)
        if previous is not None:
            if previous["chosen"] != chosen:
                raise ValueError(f"duplicate subject {subject!r} has conflicting chosen answers")
            continue
        selected[subject] = row

    curriculum = list(selected.values())
    branches = Counter(resolution_branch(row) for row in curriculum)
    short = {
        branch: minimum - branches[branch]
        for branch, minimum in minimum_branch_counts.items()
        if branches[branch] < minimum
    }
    if short:
        raise ValueError(f"resolution curriculum lacks decision-state coverage: {short}")
    return curriculum, branches


def main(argv: list[str] | None = None) -> int:
    """Write the guarded curriculum and its hash-pinned audit manifest."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", action="append", type=Path, required=True)
    parser.add_argument("--eval", type=Path, required=True)
    parser.add_argument("--golden", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args(argv)

    forbidden = {
        _norm_subject(str(row.get("subject") or ""))
        for path in (args.eval, args.golden)
        for row in _load_jsonl(path)
    } - {""}
    source_rows = [row for path in args.input for row in _load_jsonl(path)]
    curriculum, branches = build_curriculum(
        source_rows, forbidden_subjects=forbidden,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in curriculum),
        encoding="utf-8",
        newline="\n",
    )
    manifest = {
        "complete": True,
        "policy": "deduplicated_resolution_decision_state_coverage",
        "source_rows": len(source_rows),
        "curriculum_rows": len(curriculum),
        "duplicate_subject_rows_removed": len(source_rows) - len(curriculum),
        "unique_subjects": len(curriculum),
        "branch_counts": dict(sorted(branches.items())),
        "minimum_branch_counts": MINIMUM_BRANCH_COUNTS,
        "heldout_subjects_used_for_training": False,
        "input_sha256": {str(path): _sha(path) for path in args.input},
        "eval_sha256": _sha(args.eval),
        "golden_sha256": _sha(args.golden),
        "output_sha256": _sha(args.output),
    }
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
