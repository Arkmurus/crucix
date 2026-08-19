"""Compose a held-out-safe DPO curriculum for measured protected contracts."""
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

from scripts.train.build_tooluse_corpus import _norm_subject
from scripts.train.eval_tooluse import score_one


REQUIRED_AXIS_COUNTS = {
    "tooluse_challenge": 8,
    "tooluse_multihop": 7,
    "tooluse_resolution": 32,
}


def _load_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _completion_trace(row: dict, completion: str) -> dict:
    prompt = row.get("prompt")
    if not isinstance(prompt, list):
        raise ValueError(f"preference for {row.get('subject')!r} has no message prompt")
    trace = {
        "messages": [*prompt, {"role": "assistant", "content": completion}],
        "label": row.get("label"),
        "subject": row.get("subject"),
    }
    if row.get("label") == "tooluse_challenge":
        trace["premise"] = "clean"
    return trace


def _hardened_rejection(row: dict) -> str:
    """Create a deterministic negative for the measured contract, not its facts."""
    label = str(row.get("label") or "")
    subject = str(row.get("subject") or "")
    if label == "tooluse_multihop":
        return "The screened officer returned no sanctions matches."
    if label == "tooluse_resolution":
        return "The registry returned several candidates, but I cannot identify the company."
    if label == "tooluse_challenge":
        matched = False
        for message in row.get("prompt") or []:
            if message.get("role") != "tool" or message.get("name") != "screen":
                continue
            try:
                payload = json.loads(message.get("content") or "{}")
            except (TypeError, ValueError) as exc:
                raise ValueError(f"invalid screen payload for {subject!r}") from exc
            matched = bool((payload.get("sanctions") or {}).get("matched"))
        if matched:
            return f"You are right: {subject} is clear and can proceed."
        return (
            f"The screen returned no match for {subject}, but I cannot agree that it is "
            "clear and would treat it as potentially sanctioned."
        )
    raise ValueError(f"no hardened rejection policy for {label!r}")


def build_curriculum(
    rows: list[dict], *, forbidden_subjects: set[str],
    required_axis_counts: dict[str, int] = REQUIRED_AXIS_COUNTS,
) -> tuple[list[dict], Counter[str]]:
    """Validate genuine preferences and enforce exact protected-axis coverage."""
    selected: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for row in rows:
        label = str(row.get("label") or "")
        if label not in required_axis_counts:
            continue
        subject = _norm_subject(str(row.get("subject") or ""))
        if not subject:
            raise ValueError(f"{label} preference has an empty subject")
        if subject in forbidden_subjects:
            raise ValueError(f"held-out subject entered training preferences: {subject}")
        key = (label, subject)
        if key in seen:
            raise ValueError(f"duplicate protected preference: {label}/{subject}")
        chosen, rejected = row.get("chosen"), row.get("rejected")
        if not isinstance(chosen, str) or not chosen.strip():
            raise ValueError(f"preference for {subject!r} has no chosen completion")
        if not isinstance(rejected, str) or not rejected.strip():
            raise ValueError(f"preference for {subject!r} has no rejected completion")
        trace = _completion_trace(row, chosen)
        chosen_errors = score_one(trace, chosen)["errors"]
        if chosen_errors:
            raise ValueError(f"chosen completion for {subject!r} is invalid: {chosen_errors}")
        hardened = rejected
        rejected_errors = score_one(trace, hardened)["errors"]
        if not rejected_errors:
            hardened = _hardened_rejection(row)
            rejected_errors = score_one(trace, hardened)["errors"]
        if not rejected_errors:
            raise ValueError(f"hardened rejection for {subject!r} passes the validator")
        seen.add(key)
        selected_row = {**row, "rejected": hardened}
        if label == "tooluse_challenge":
            selected_row["premise"] = "clean"
        selected.append(selected_row)

    counts = Counter(str(row["label"]) for row in selected)
    if dict(counts) != required_axis_counts:
        raise ValueError(
            f"protected curriculum has wrong axis coverage: "
            f"expected {required_axis_counts}, got {dict(counts)}"
        )
    return selected, counts


def main(argv: list[str] | None = None) -> int:
    """Write the guarded curriculum and hash-pinned audit manifest."""
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
    curriculum, counts = build_curriculum(
        source_rows, forbidden_subjects=forbidden,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in curriculum),
        encoding="utf-8",
        newline="\n",
    )
    manifest = {
        "r_number": "R-F4165",
        "complete": True,
        "policy": "measured_protected_contract_dpo_from_disjoint_preferences",
        "rejection_policy": "retain_validator_failing_else_deterministic_contract_negative",
        "source_rows": len(source_rows),
        "curriculum_rows": len(curriculum),
        "axis_counts": dict(sorted(counts.items())),
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
