"""Rescore retained tool-use answers after a validator correction.

No model inference occurs. The source report and eval set must be complete and
positionally aligned; the output records both input hashes and the active scorer
version so corrected metrics cannot be confused with their stale predecessors.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from scripts.train.eval_tooluse import SCORER_VERSION, build_report, score_one


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rescore(traces: list[dict], source: dict) -> dict:
    """Return a complete current-scorer report from retained answers."""
    measured = source.get("rows") or []
    if source.get("complete") is not True or len(measured) != len(traces):
        raise ValueError("source report is incomplete or unaligned")
    rows = []
    for index, (trace, row) in enumerate(zip(traces, measured), 1):
        identity = (str(trace.get("label") or "unlabelled"), trace.get("subject"))
        if (row.get("label"), row.get("subject")) != identity:
            raise ValueError(f"source report row {index} does not match eval set")
        request_error = next(
            (str(error).removeprefix("request failed: ")
             for error in row.get("errors") or []
             if str(error).startswith("request failed: ")),
            None,
        )
        rows.append(score_one(trace, row.get("answer"), error=request_error))
    return {
        **build_report(rows),
        "rows": rows,
        "complete": True,
        "rescored_from": source.get("run"),
    }


def main(argv: list[str] | None = None) -> int:
    """Validate inputs and write an atomic, provenance-pinned rescore report."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--eval-file", required=True, type=Path)
    parser.add_argument("--source-report", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)

    traces = [
        json.loads(line)
        for line in args.eval_file.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    source = json.loads(args.source_report.read_text(encoding="utf-8"))
    report = rescore(traces, source)
    report["provenance"] = {
        "eval_sha256": _sha(args.eval_file),
        "source_report_sha256": _sha(args.source_report),
        "scorer_version": SCORER_VERSION,
        "model_inference_reused": True,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    temp = args.out.with_name(f".{args.out.name}.tmp")
    temp.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    temp.replace(args.out)
    print(f"rescored {report['honest']}/{report['total']} with {SCORER_VERSION}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
