"""Build deterministic staged-learning assets for R-F3848.

Calibration rows are sampled from the training queue and therefore measure
contract acquisition, not generalization.  The unchanged 168-row held-out set
remains the only promotion evidence.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path

from scripts.train.build_mixed_tooluse_cycle import ALL_AXES, RETENTION_AXES, validate_dpo
from scripts.train.build_tooluse_corpus import _norm_subject
from scripts.train.eval_tooluse import build_report, score_one


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def deduplicate_preferences(rows: list[dict]) -> list[dict]:
    """Keep one genuine preference per subject/axis to prevent subject overweighting."""
    seen: set[tuple[str, str]] = set()
    result: list[dict] = []
    for row in rows:
        key = (str(row.get("label") or ""), str(row.get("subject") or "").casefold().strip())
        if not key[1] or key in seen:
            continue
        seen.add(key)
        result.append(row)
    return result


def exclude_subjects(rows: list[dict], forbidden: set[str]) -> tuple[list[dict], list[dict]]:
    """Separate preferences whose normalized subjects belong to an evaluation surface."""
    forbidden = {_norm_subject(subject) for subject in forbidden}
    kept, excluded = [], []
    for row in rows:
        target = excluded if _norm_subject(str(row.get("subject") or "")) in forbidden else kept
        target.append(row)
    return kept, excluded


def calibration_indices(train: list[dict], quota: int) -> list[int]:
    """Select the first stable ``quota`` rows per axis from the immutable queue."""
    counts: Counter[str] = Counter()
    indices: list[int] = []
    for index, row in enumerate(train):
        label = str(row.get("label") or "")
        if label in ALL_AXES and counts[label] < quota:
            indices.append(index)
            counts[label] += 1
    missing = {axis: quota - counts[axis] for axis in sorted(ALL_AXES) if counts[axis] != quota}
    if missing:
        raise ValueError(f"calibration quotas unavailable: {missing}")
    return indices


def subset_report(raw: dict, indices: list[int]) -> dict:
    """Create a complete raw-base calibration report aligned to selected queue rows."""
    rows = raw.get("rows") or []
    if raw.get("complete") is not True or len(rows) != int(raw.get("total", -1)):
        raise ValueError("raw-base report is incomplete")
    chosen = [rows[index] for index in indices]
    per_axis = []
    for label in sorted({str(row["label"]) for row in chosen}):
        axis = [row for row in chosen if row["label"] == label]
        honest = sum(bool(row.get("honest")) for row in axis)
        per_axis.append({"label": label, "total": len(axis), "honest": honest,
                         "honest_rate": honest / len(axis)})
    honest = sum(bool(row.get("honest")) for row in chosen)
    return {"eval": "train_calibration_only", "total": len(chosen), "honest": honest,
            "honest_rate": honest / len(chosen), "per_axis": per_axis, "rows": chosen,
            "complete": True,
            "note": "trained-on calibration; never valid promotion evidence"}


def rescore_answers(train: list[dict], raw: dict) -> dict:
    """Reapply the current validator to retained raw answers.

    Reports deliberately retain model text so scorer repairs do not require a
    second paid inference run. Trusting stale ``honest`` booleans here made the
    curve optimize evaluator bugs that had already been fixed in code.
    """
    measured = raw.get("rows") or []
    if raw.get("complete") is not True or len(measured) != len(train):
        raise ValueError("raw-base report is incomplete or unaligned")
    rows = [score_one(trace, row.get("answer"))
            for trace, row in zip(train, measured)]
    rescored = build_report(rows)
    rescored["rows"] = rows
    rescored["complete"] = True
    rescored["note"] += "; retained answers rescored by current validator"
    return rescored


def deficit_weighted_sft(train: list[dict], baseline: dict, quota: int) -> tuple[list[dict], dict[str, int]]:
    """Repeat whole axes in proportion to measured calibration deficits.

    Every subject within an axis receives the same weight. This targets observed
    acquisition gaps without fabricating examples or overweighting one entity.
    A perfect axis remains one-copy retention signal; an axis at ``h/quota`` is
    represented ``1 + quota - h`` times.
    """
    from scripts.train.eval_tooluse import report_consistency_error

    error = report_consistency_error(baseline)
    if error:
        raise ValueError(f"baseline report summary is inconsistent: {error}")
    scores = {str(axis["label"]): int(axis["honest"])
              for axis in baseline.get("per_axis") or []}
    if set(scores) != ALL_AXES:
        raise ValueError("baseline does not cover all ten axes")
    weights = {axis: 1 + quota - scores[axis] for axis in sorted(ALL_AXES)}
    if any(weight < 1 or weight > quota + 1 for weight in weights.values()):
        raise ValueError(f"invalid deficit weights: {weights}")
    weighted = [row for row in train
                for _ in range(weights[str(row.get("label") or "")])]
    return weighted, weights


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--train", type=Path, required=True)
    ap.add_argument("--dpo", type=Path, action="append", required=True,
                    help="genuine preference source; repeat to retain earlier axes")
    ap.add_argument("--dpo-exclude-subjects", type=Path, action="append", default=[],
                    help="evaluation JSONL whose subjects must be absent from DPO output")
    ap.add_argument("--raw-report", type=Path, required=True)
    ap.add_argument("--eval", type=Path, required=True)
    ap.add_argument("--golden", type=Path, required=True)
    ap.add_argument("--quota", type=int, default=3)
    ap.add_argument("--sft-out", type=Path, required=True)
    ap.add_argument("--dpo-out", type=Path, required=True)
    ap.add_argument("--probe-out", type=Path, required=True)
    ap.add_argument("--baseline-out", type=Path, required=True)
    ap.add_argument("--heldout-raw-report", type=Path)
    ap.add_argument("--heldout-baseline-out", type=Path)
    ap.add_argument("--manifest-out", type=Path, required=True)
    args = ap.parse_args(argv)
    if bool(args.heldout_raw_report) != bool(args.heldout_baseline_out):
        ap.error("--heldout-raw-report and --heldout-baseline-out must be supplied together")
    train = load_jsonl(args.train)
    source_dpo = [row for path in args.dpo for row in load_jsonl(path)]
    dpo = deduplicate_preferences(source_dpo)
    excluded_subjects = {
        _norm_subject(str(row.get("subject") or ""))
        for path in args.dpo_exclude_subjects for row in load_jsonl(path)
    }
    dpo, excluded_dpo = exclude_subjects(dpo, excluded_subjects)
    forbidden = {_norm_subject(str(row.get("subject") or ""))
                 for path in (args.eval, args.golden) for row in load_jsonl(path)}
    validate_dpo(dpo, forbidden, allowed_axes=ALL_AXES)
    indices = calibration_indices(train, args.quota)
    raw = json.loads(args.raw_report.read_text(encoding="utf-8"))
    if len(raw.get("rows") or []) != len(train):
        raise ValueError("raw-base report is not aligned to the training queue")
    for index, row in enumerate(train):
        measured = raw["rows"][index]
        if (row.get("label"), row.get("subject")) != (measured.get("label"), measured.get("subject")):
            raise ValueError(f"raw-base report diverges from queue at row {index + 1}")
    probe = [train[index] for index in indices]
    rescored_raw = rescore_answers(train, raw)
    baseline = subset_report(rescored_raw, indices)
    sft, axis_weights = deficit_weighted_sft(train, baseline, args.quota)
    outputs = ((args.sft_out, sft), (args.dpo_out, dpo), (args.probe_out, probe))
    for path, rows in outputs:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
                        encoding="utf-8", newline="\n")
    args.baseline_out.parent.mkdir(parents=True, exist_ok=True)
    args.baseline_out.write_text(json.dumps(baseline, indent=2) + "\n", encoding="utf-8", newline="\n")
    heldout_baseline = None
    if args.heldout_raw_report:
        heldout_baseline = rescore_answers(
            load_jsonl(args.eval),
            json.loads(args.heldout_raw_report.read_text(encoding="utf-8")),
        )
        args.heldout_baseline_out.parent.mkdir(parents=True, exist_ok=True)
        args.heldout_baseline_out.write_text(
            json.dumps(heldout_baseline, indent=2) + "\n", encoding="utf-8", newline="\n"
        )
    manifest = {"complete": True, "calibration_is_promotion_evidence": False,
                "sft_rows": len(sft), "source_sft_rows": len(train),
                "sft_axis_weights": axis_weights,
                "source_dpo_rows": len(source_dpo),
                "deduplicated_dpo_rows": len(dpo) + len(excluded_dpo),
                "excluded_dpo_rows": len(excluded_dpo),
                "clean_dpo_rows": len(dpo), "calibration_rows": len(probe),
                "calibration_quota": args.quota, "axes": sorted(ALL_AXES),
                "protected_axes": sorted(RETENTION_AXES),
                "input_sha256": {"train": sha(args.train),
                                  "dpo": {str(path): sha(path) for path in args.dpo},
                                  "dpo_exclude_subjects": {
                                      str(path): sha(path) for path in args.dpo_exclude_subjects
                                  },
                                  "raw_report": sha(args.raw_report), "eval": sha(args.eval),
                                  "golden": sha(args.golden)},
                "output_sha256": {"sft": sha(args.sft_out), "dpo": sha(args.dpo_out),
                                   "probe": sha(args.probe_out), "baseline": sha(args.baseline_out)}}
    if args.heldout_raw_report:
        manifest["heldout_baseline"] = {
            "honest": heldout_baseline["honest"], "total": heldout_baseline["total"],
            "input_sha256": sha(args.heldout_raw_report),
            "output_sha256": sha(args.heldout_baseline_out),
        }
    args.manifest_out.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps(manifest, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
