"""R-F3733 — build retention curriculum and gate checkpoint promotion.

Only aggregate axis scores from the held-out report influence curriculum
weights; held-out prompts and answers are never copied into training data.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def _axes(report: dict) -> dict[str, dict]:
    return {str(row["label"]): row for row in report.get("per_axis") or []}


def promotion_verdict(incumbent: dict, candidate: dict) -> dict:
    """Require an aggregate gain and zero lost honest answers on every axis."""
    old, new = _axes(incumbent), _axes(candidate)
    missing = sorted(set(old) - set(new))
    regressions = []
    for label in sorted(set(old) & set(new)):
        before, after = int(old[label]["honest"]), int(new[label]["honest"])
        if after < before:
            regressions.append({"label": label, "before": before, "after": after,
                                "lost": before - after, "n": int(old[label]["total"])})
    overall_gain = int(candidate.get("honest", 0)) - int(incumbent.get("honest", 0))
    promote = overall_gain > 0 and not missing and not regressions
    return {"promote": promote, "overall_gain": overall_gain,
            "missing_axes": missing, "regressions": regressions,
            "reason": "strict_compounding" if promote else "retention_gate_failed"}


def build_retention_curriculum(train: list[dict], verdict: dict) -> list[dict]:
    """Replay regressed training axes once, preserving every original row."""
    labels = {row["label"] for row in verdict.get("regressions") or []}
    replay = [row for row in train if str(row.get("label")) in labels]
    return list(train) + replay


def _load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--incumbent", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--train", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--verdict-out", type=Path, required=True)
    args = parser.parse_args(argv)
    incumbent = json.loads(args.incumbent.read_text(encoding="utf-8"))
    candidate = json.loads(args.candidate.read_text(encoding="utf-8"))
    verdict = promotion_verdict(incumbent, candidate)
    curriculum = build_retention_curriculum(_load_jsonl(args.train), verdict)
    if len(curriculum) <= len(_load_jsonl(args.train)) and verdict["regressions"]:
        raise ValueError("regressed axes had no training rows; refusing empty intervention")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in curriculum),
                        encoding="utf-8", newline="\n")
    verdict.update({"incumbent_sha256": _sha(args.incumbent),
                    "candidate_sha256": _sha(args.candidate),
                    "train_sha256": _sha(args.train), "train_rows": len(curriculum)})
    args.verdict_out.write_text(json.dumps(verdict, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(verdict, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
