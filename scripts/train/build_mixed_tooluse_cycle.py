"""Build the retention SFT phase for a mixed tool-use DPO cycle (R-F3843).

The DPO input is immutable evidence collected from real model failures.  This
builder never creates rejected answers.  It selects deterministic, chosen-only
rehearsal rows for axes on which raw base produced no usable negative, while
failing closed on held-out contamination or incomplete axis coverage.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path

from scripts.train.build_tooluse_corpus import _norm_subject


TARGET_AXES = frozenset({
    "tooluse_challenge", "tooluse_challenge_unavailable", "tooluse_multihop",
    "tooluse_person", "tooluse_trace", "tooluse_trace_unavailable",
})
RETENTION_AXES = frozenset({
    "tooluse_adverse", "tooluse_contradiction", "tooluse_resolution",
    "tooluse_news_impact",
})
ALL_AXES = TARGET_AXES | RETENTION_AXES


def _load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def _subjects(rows: list[dict]) -> set[str]:
    return {_norm_subject(str(row.get("subject") or "")) for row in rows
            if _norm_subject(str(row.get("subject") or ""))}


def select_retention_rows(
    train: list[dict], *, quota: int, forbidden_subjects: set[str],
) -> list[dict]:
    """Select exactly ``quota`` stable train rows for every protected axis."""
    if quota < 1:
        raise ValueError("retention quota must be positive")
    selected: list[dict] = []
    counts: Counter[str] = Counter()
    seen: set[tuple[str, str]] = set()
    for row in train:
        label = str(row.get("label") or "")
        subject = _norm_subject(str(row.get("subject") or ""))
        key = (label, subject)
        if label not in RETENTION_AXES or not subject or key in seen:
            continue
        if subject in forbidden_subjects:
            raise ValueError(f"train subject overlaps held-out data: {subject}")
        messages = row.get("messages")
        if not isinstance(messages, list) or not messages:
            raise ValueError(f"retention row has no messages: {subject}")
        if not isinstance(messages[-1], dict) or messages[-1].get("role") != "assistant" \
                or not str(messages[-1].get("content") or "").strip():
            raise ValueError(f"retention row has no chosen assistant answer: {subject}")
        seen.add(key)
        if counts[label] < quota:
            selected.append(row)
            counts[label] += 1
    missing = {axis: quota - counts[axis] for axis in sorted(RETENTION_AXES)
               if counts[axis] != quota}
    if missing:
        raise ValueError(f"retention quotas unavailable: {missing}")
    return selected


def validate_dpo(
    rows: list[dict],
    forbidden_subjects: set[str],
    allowed_axes: frozenset[str] = TARGET_AXES,
    required_axes: frozenset[str] = TARGET_AXES,
) -> Counter[str]:
    """Validate genuine collected preferences without synthesizing negatives."""
    counts: Counter[str] = Counter()
    if not rows:
        raise ValueError("DPO artifact is empty")
    for index, row in enumerate(rows, 1):
        label = str(row.get("label") or "")
        subject = _norm_subject(str(row.get("subject") or ""))
        if label not in allowed_axes:
            raise ValueError(f"DPO row {index} is not a targeted failure axis: {label}")
        if not subject or subject in forbidden_subjects:
            raise ValueError(f"DPO row {index} overlaps held-out data: {subject}")
        if not isinstance(row.get("prompt"), list) or not row["prompt"]:
            raise ValueError(f"DPO row {index} has no collected prompt")
        chosen, rejected = row.get("chosen"), row.get("rejected")
        if not all(isinstance(value, str) and value.strip()
                   for value in (chosen, rejected)) or chosen == rejected:
            raise ValueError(f"DPO row {index} has no genuine preference")
        if not str(row.get("why") or "").strip():
            raise ValueError(f"DPO row {index} lacks validator failure evidence")
        counts[label] += 1
    missing = sorted(required_axes - set(counts))
    if missing:
        raise ValueError(f"targeted axes lack genuine DPO signal: {missing}")
    return counts


def validate_protected_axis_evidence(
    rows: list[dict], *, forbidden_subjects: set[str], required_axes: frozenset[str],
) -> Counter[str]:
    """Require current-scoring, held-out-disjoint protected preferences."""
    unknown = sorted(required_axes - ALL_AXES)
    if unknown:
        raise ValueError(f"unknown protected DPO axes: {unknown}")
    counts = validate_dpo(
        rows,
        forbidden_subjects,
        allowed_axes=ALL_AXES,
        required_axes=required_axes,
    )
    from scripts.train.eval_tooluse import score_one

    for index, row in enumerate(rows, 1):
        base = {
            "label": row.get("label"),
            "subject": row.get("subject"),
            "messages": list(row["prompt"]),
        }
        if row.get("premise") is not None:
            base["premise"] = row["premise"]
        chosen = score_one(
            {**base, "messages": base["messages"] + [
                {"role": "assistant", "content": row["chosen"]},
            ]},
            row["chosen"],
        )
        rejected = score_one(
            {**base, "messages": base["messages"] + [
                {"role": "assistant", "content": row["rejected"]},
            ]},
            row["rejected"],
        )
        if not chosen.get("honest"):
            raise ValueError(
                f"DPO row {index} chosen answer fails current validator: "
                f"{(chosen.get('errors') or ['unknown'])[0]}"
            )
        if rejected.get("honest"):
            raise ValueError(
                f"DPO row {index} rejected answer passes current validator; "
                "preference evidence is stale"
            )
    return counts


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--train", type=Path, required=True)
    parser.add_argument("--dpo", type=Path, required=True)
    parser.add_argument("--eval", type=Path, required=True)
    parser.add_argument("--golden", type=Path, required=True)
    parser.add_argument("--quota", type=int, default=9)
    parser.add_argument("--sft-out", type=Path, required=True)
    parser.add_argument("--manifest-out", type=Path, required=True)
    args = parser.parse_args(argv)

    train, dpo = _load_jsonl(args.train), _load_jsonl(args.dpo)
    forbidden = _subjects(_load_jsonl(args.eval)) | _subjects(_load_jsonl(args.golden))
    dpo_counts = validate_dpo(dpo, forbidden)
    retention = select_retention_rows(train, quota=args.quota,
                                      forbidden_subjects=forbidden)
    args.sft_out.parent.mkdir(parents=True, exist_ok=True)
    args.sft_out.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in retention),
        encoding="utf-8", newline="\n",
    )
    manifest = {
        "complete": True,
        "policy": "chosen_only_retention_then_genuine_dpo",
        "retention_counts": dict(sorted(Counter(
            str(row["label"]) for row in retention).items())),
        "dpo_counts": dict(sorted(dpo_counts.items())),
        "signal_axes": sorted(set(dpo_counts) | {str(row["label"]) for row in retention}),
        "sft_rows": len(retention),
        "dpo_rows": len(dpo),
        "train_sha256": _sha(args.train),
        "dpo_sha256": _sha(args.dpo),
        "eval_sha256": _sha(args.eval),
        "golden_sha256": _sha(args.golden),
        "sft_sha256": _sha(args.sft_out),
    }
    if set(manifest["signal_axes"]) != ALL_AXES:
        raise ValueError("mixed cycle does not cover all ten axes")
    args.manifest_out.parent.mkdir(parents=True, exist_ok=True)
    args.manifest_out.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
