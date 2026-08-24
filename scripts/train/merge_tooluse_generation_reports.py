"""Merge complete generation reports into a target queue without losing alignment."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from scripts.train.eval_tooluse import build_report, score_one


def load_jsonl(path: Path) -> list[dict]:
    """Load non-empty JSONL rows."""
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def trace_key(trace: dict) -> str:
    """Return a stable identity for an exact queued trace."""
    encoded = json.dumps(trace, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def merge_reports(target: list[dict], sources: list[tuple[list[dict], dict]]) -> dict:
    """Align source answers to ``target`` and rescore them with the current validator."""
    answers: dict[str, str] = {}
    for queue, report in sources:
        rows = report.get("rows") or []
        if report.get("complete") is not True or len(rows) != len(queue):
            raise ValueError("source report is incomplete or unaligned")
        for index, (trace, measured) in enumerate(zip(queue, rows), 1):
            if (trace.get("label"), trace.get("subject")) != (
                    measured.get("label"), measured.get("subject")):
                raise ValueError(f"source report diverges from queue at row {index}")
            key = trace_key(trace)
            answer = str(measured.get("answer") or "")
            if key in answers and answers[key] != answer:
                raise ValueError(f"conflicting answers for trace {key}")
            answers[key] = answer

    missing = [trace_key(trace) for trace in target if trace_key(trace) not in answers]
    if missing:
        raise ValueError(f"target contains {len(missing)} traces without generations")
    rows = [score_one(trace, answers[trace_key(trace)]) for trace in target]
    report = build_report(rows)
    report.update({"rows": rows, "complete": True,
                   "note": report["note"] + "; merged retained answers rescored by current validator"})
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", required=True, type=Path)
    ap.add_argument("--source", action="append", nargs=2, metavar=("QUEUE", "REPORT"),
                    required=True)
    ap.add_argument("--out", required=True, type=Path)
    args = ap.parse_args(argv)
    target = load_jsonl(args.target)
    sources = [(load_jsonl(Path(queue)),
                json.loads(Path(report).read_text(encoding="utf-8")))
               for queue, report in args.source]
    merged = merge_reports(target, sources)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(merged, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"wrote {merged['honest']}/{merged['total']} honest -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
