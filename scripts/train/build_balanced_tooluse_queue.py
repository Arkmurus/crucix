"""Build a deterministic train-only queue with target and retention coverage."""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

from scripts.train.build_tooluse_corpus import _norm_subject


TARGET_LABELS = {
    "tooluse_challenge",
    "tooluse_challenge_unavailable",
    "tooluse_multihop",
    # R-F3869 — person is an acquisition failure, not retention. Capping it at
    # six subjects while unavailable-screen axes received 11-16 taught the
    # terminal answer to call performed person matches unavailable.
    "tooluse_person",
    "tooluse_trace_unavailable",
}
EXPECTED_LABELS = TARGET_LABELS | {
    "tooluse_adverse",
    "tooluse_contradiction",
    "tooluse_news_impact",
    "tooluse_person",
    "tooluse_resolution",
    "tooluse_trace",
}


def build_balanced_queue(
    train: list[dict],
    *,
    eval_entities: set[str],
    target_limit: int = 16,
    retention_limit: int = 6,
) -> list[dict]:
    """Select every axis deterministically while refusing held-out entities."""
    if not eval_entities:
        raise ValueError("eval_entities is empty; contamination is unchecked")
    if target_limit < 1 or retention_limit < 1:
        raise ValueError("queue limits must be positive")
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in train:
        label = str(row.get("label") or "")
        if label in EXPECTED_LABELS:
            grouped[label].append(row)
    missing = EXPECTED_LABELS - grouped.keys()
    if missing:
        raise ValueError(f"training split is missing axes: {sorted(missing)}")

    selected: list[dict] = []
    for label in sorted(EXPECTED_LABELS):
        limit = target_limit if label in TARGET_LABELS else retention_limit
        selected.extend(grouped[label][:limit])
    contaminated = sorted({
        str(row.get("subject") or "")
        for row in selected
        if _norm_subject(str(row.get("subject") or "")) in eval_entities
    })
    if contaminated:
        raise ValueError(f"queue contains held-out entities: {contaminated}")
    return selected


def select_novel_rows(rows: list[dict], prior: list[dict]) -> list[dict]:
    """Return rows whose axis/subject evidence is absent from ``prior``."""
    known = {
        (str(row.get("label") or ""), _norm_subject(str(row.get("subject") or "")))
        for row in prior
    }
    return [
        row for row in rows
        if (str(row.get("label") or ""),
            _norm_subject(str(row.get("subject") or ""))) not in known
    ]


def _read_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def main(argv: list[str] | None = None) -> int:
    """Write a balanced queue after entity-level contamination checks."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--train", required=True, type=Path)
    parser.add_argument("--eval-file", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--target-limit", type=int, default=16)
    parser.add_argument("--retention-limit", type=int, default=6)
    parser.add_argument("--exclude-file", type=Path,
                        help="Write only axis/subject rows absent from this JSONL")
    args = parser.parse_args(argv)

    held = {
        _norm_subject(str(row.get("subject") or ""))
        for row in _read_jsonl(args.eval_file)
    } - {""}
    queue = build_balanced_queue(
        _read_jsonl(args.train),
        eval_entities=held,
        target_limit=args.target_limit,
        retention_limit=args.retention_limit,
    )
    if args.exclude_file:
        queue = select_novel_rows(queue, _read_jsonl(args.exclude_file))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in queue),
        encoding="utf-8",
        newline="\n",
    )
    counts = {label: 0 for label in sorted(EXPECTED_LABELS)}
    for row in queue:
        counts[str(row["label"])] += 1
    print(f"wrote {len(queue)} balanced train-only tasks -> {args.out}")
    for label, count in counts.items():
        print(f"  {label:<32}{count:>4}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
