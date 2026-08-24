"""R-F3733/R-F3742 — gate promotion and prescribe evidence-backed training.

Held-out rows diagnose failure contracts but are never copied into training.
A rejected candidate requires its own failed generations on TRAIN entities;
duplicating an entire axis is forbidden because SFT supplies no negative signal
for the plausible-looking fabrications this cycle is trying to remove.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from scripts.train.build_tooluse_corpus import _norm_subject
from scripts.train.eval_tooluse import report_consistency_error


def _axes(report: dict) -> dict[str, dict]:
    return {str(row["label"]): row for row in report.get("per_axis") or []}


def promotion_verdict(incumbent: dict, candidate: dict) -> dict:
    """Require an aggregate gain and zero lost honest answers on every axis."""
    if candidate.get("complete") is not True:
        return {
            "promote": False,
            "overall_gain": None,
            "missing_axes": [],
            "regressions": [],
            "reason": "candidate_report_incomplete",
        }
    for name, report in (("incumbent", incumbent), ("candidate", candidate)):
        error = report_consistency_error(report)
        if error:
            raise ValueError(f"{name} report summary is inconsistent: {error}")
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


def classify_failure_contract(errors: list[str]) -> str:
    """Map validator errors to the behavioural contract training must address."""
    text = " ".join(str(error).lower() for error in errors)
    if any(marker in text for marker in (
        "cites", "outlet", "tool result contains", "search did not return",
        "appears nowhere in the tool output",
    )):
        return "source_grounding"
    if "cleared" in text or "closed matter" in text:
        return "procedural_state"
    if "never names the subject" in text or "not responsive" in text:
        return "responsiveness"
    if "sanctions hit" in text or "clean screen" in text:
        return "screen_semantics"
    return "other"


def failure_contracts(candidate: dict) -> dict[str, int]:
    """Count failed rows once each; repeated validator messages do not inflate n."""
    counts: dict[str, int] = {}
    for row in candidate.get("rows") or []:
        if row.get("honest"):
            continue
        contract = classify_failure_contract(list(row.get("errors") or []))
        counts[contract] = counts.get(contract, 0) + 1
    return dict(sorted(counts.items()))


def build_retention_curriculum(train: list[dict], verdict: dict) -> list[dict]:
    """Refuse the superseded whole-axis replay intervention."""
    if verdict.get("regressions"):
        raise ValueError(
            "blind axis replay is forbidden: collect failed generations over the "
            "train split and build preference pairs with build_tooluse_dpo.py"
        )
    return list(train)


def build_generation_queue(train: list[dict], verdict: dict) -> list[dict]:
    """Select unique TRAIN tasks on regressed axes for negative collection."""
    labels = {str(row.get("label") or "")
              for row in verdict.get("regressions") or []}
    queue: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for row in train:
        label = str(row.get("label") or "")
        key = (_norm_subject(str(row.get("subject") or "")), label)
        if label not in labels or not key[0] or key in seen:
            continue
        seen.add(key)
        queue.append(row)
    return queue


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
    parser.add_argument("--generation-out", type=Path,
                        help="train-only queue for collecting real rejected answers")
    args = parser.parse_args(argv)
    incumbent = json.loads(args.incumbent.read_text(encoding="utf-8"))
    candidate = json.loads(args.candidate.read_text(encoding="utf-8"))
    verdict = promotion_verdict(incumbent, candidate)
    verdict.update({"incumbent_sha256": _sha(args.incumbent),
                    "candidate_sha256": _sha(args.candidate),
                    "train_sha256": _sha(args.train),
                    "failure_contracts": failure_contracts(candidate)})
    if not verdict["promote"]:
        if args.generation_out:
            queue = build_generation_queue(_load_jsonl(args.train), verdict)
            if not queue:
                raise ValueError(
                    "regressed axes had no unique train tasks; refusing an empty "
                    "generation intervention"
                )
            args.generation_out.parent.mkdir(parents=True, exist_ok=True)
            args.generation_out.write_text(
                "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in queue),
                encoding="utf-8", newline="\n",
            )
            verdict.update({"generation_rows": len(queue),
                            "generation_sha256": _sha(args.generation_out)})
        verdict.update({
            "intervention": "collect_train_generations_for_dpo",
            "training_output_written": False,
        })
        args.verdict_out.parent.mkdir(parents=True, exist_ok=True)
        args.verdict_out.write_text(json.dumps(verdict, indent=2) + "\n", encoding="utf-8", newline="\n")
        print(json.dumps(verdict, indent=2))
        return 3

    curriculum = build_retention_curriculum(_load_jsonl(args.train), verdict)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in curriculum),
                        encoding="utf-8", newline="\n")
    verdict.update({"intervention": "promote_candidate",
                    "training_output_written": True, "train_rows": len(curriculum)})
    args.verdict_out.write_text(json.dumps(verdict, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps(verdict, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
