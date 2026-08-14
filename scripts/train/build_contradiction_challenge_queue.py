"""Select hard, real-evidence contradiction traces for failure collection."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from scripts.train.build_tooluse_corpus import _norm_subject, validate_trace


def _jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def _tool_payloads(row: dict, name: str) -> list[dict]:
    payloads = []
    for message in row.get("messages") or []:
        if message.get("role") != "tool" or message.get("name") != name:
            continue
        try:
            payload = json.loads(str(message.get("content") or ""))
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            payloads.append(payload)
    return payloads


def select_hard_contradictions(
    rows: list[dict], *, forbidden_subjects: set[str],
    excluded_evidence: set[tuple[str, str]], limit: int,
    minimum_relevant_results: int = 3,
) -> list[dict]:
    """Select unique no-match/adverse conflicts with strong same-subject evidence."""
    if not forbidden_subjects:
        raise ValueError("forbidden subjects are empty; contamination is unchecked")
    if limit < 1 or minimum_relevant_results < 1:
        raise ValueError("challenge thresholds must be positive")
    selected: list[dict] = []
    seen: set[str] = set()
    for row in rows:
        if row.get("label") != "tooluse_contradiction":
            continue
        subject = str(row.get("subject") or "")
        normalized = _norm_subject(subject)
        evidence_key = ("tooluse_contradiction", normalized)
        if not normalized or normalized in seen or normalized in forbidden_subjects \
                or evidence_key in excluded_evidence:
            continue
        screens = _tool_payloads(row, "screen")
        searches = _tool_payloads(row, "web_search")
        if not screens or not searches:
            continue
        sanctions = screens[-1].get("sanctions") or {}
        if sanctions.get("matched") is not False:
            continue
        subject_token = subject.split()[0].lower()
        relevant = [
            result for result in searches[-1].get("results") or []
            if isinstance(result, dict)
            and not str(result.get("url") or "").lower().startswith("memory:")
            and subject_token in (
                str(result.get("title") or "") + " "
                + str(result.get("snippet") or "")
            ).lower()
        ]
        if len(relevant) < minimum_relevant_results:
            continue
        errors = validate_trace(row)
        if errors:
            raise ValueError(f"canonical challenge trace is invalid for {subject}: {errors}")
        selected.append(row)
        seen.add(normalized)
        if len(selected) == limit:
            return selected
    raise ValueError(
        f"only {len(selected)} hard disjoint contradiction traces available; "
        f"required {limit}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--train", type=Path, required=True)
    parser.add_argument("--held-out", type=Path, action="append", required=True)
    parser.add_argument("--exclude-file", type=Path, action="append", default=[])
    parser.add_argument("--limit", type=int, default=48)
    parser.add_argument("--minimum-relevant-results", type=int, default=3)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    forbidden = {
        _norm_subject(str(row.get("subject") or ""))
        for path in args.held_out for row in _jsonl(path)
    } - {""}
    excluded = {
        (str(row.get("label") or ""), _norm_subject(str(row.get("subject") or "")))
        for path in args.exclude_file for row in _jsonl(path)
    }
    selected = select_hard_contradictions(
        _jsonl(args.train),
        forbidden_subjects=forbidden,
        excluded_evidence=excluded,
        limit=args.limit,
        minimum_relevant_results=args.minimum_relevant_results,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in selected),
        encoding="utf-8", newline="\n",
    )
    print(f"wrote {len(selected)} hard contradiction traces -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
