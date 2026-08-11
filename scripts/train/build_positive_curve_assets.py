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


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--train", type=Path, required=True)
    ap.add_argument("--dpo", type=Path, required=True)
    ap.add_argument("--raw-report", type=Path, required=True)
    ap.add_argument("--eval", type=Path, required=True)
    ap.add_argument("--golden", type=Path, required=True)
    ap.add_argument("--quota", type=int, default=3)
    ap.add_argument("--sft-out", type=Path, required=True)
    ap.add_argument("--dpo-out", type=Path, required=True)
    ap.add_argument("--probe-out", type=Path, required=True)
    ap.add_argument("--baseline-out", type=Path, required=True)
    ap.add_argument("--manifest-out", type=Path, required=True)
    args = ap.parse_args(argv)
    train, source_dpo = load_jsonl(args.train), load_jsonl(args.dpo)
    dpo = deduplicate_preferences(source_dpo)
    forbidden = {_norm_subject(str(row.get("subject") or ""))
                 for path in (args.eval, args.golden) for row in load_jsonl(path)}
    validate_dpo(dpo, forbidden)
    indices = calibration_indices(train, args.quota)
    raw = json.loads(args.raw_report.read_text(encoding="utf-8"))
    if len(raw.get("rows") or []) != len(train):
        raise ValueError("raw-base report is not aligned to the training queue")
    for index, row in enumerate(train):
        measured = raw["rows"][index]
        if (row.get("label"), row.get("subject")) != (measured.get("label"), measured.get("subject")):
            raise ValueError(f"raw-base report diverges from queue at row {index + 1}")
    probe = [train[index] for index in indices]
    baseline = subset_report(raw, indices)
    outputs = ((args.sft_out, train), (args.dpo_out, dpo), (args.probe_out, probe))
    for path, rows in outputs:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
                        encoding="utf-8", newline="\n")
    args.baseline_out.parent.mkdir(parents=True, exist_ok=True)
    args.baseline_out.write_text(json.dumps(baseline, indent=2) + "\n", encoding="utf-8")
    manifest = {"complete": True, "calibration_is_promotion_evidence": False,
                "sft_rows": len(train), "source_dpo_rows": len(source_dpo),
                "deduplicated_dpo_rows": len(dpo), "calibration_rows": len(probe),
                "calibration_quota": args.quota, "axes": sorted(ALL_AXES),
                "protected_axes": sorted(RETENTION_AXES),
                "input_sha256": {"train": sha(args.train), "dpo": sha(args.dpo),
                                  "raw_report": sha(args.raw_report), "eval": sha(args.eval),
                                  "golden": sha(args.golden)},
                "output_sha256": {"sft": sha(args.sft_out), "dpo": sha(args.dpo_out),
                                   "probe": sha(args.probe_out), "baseline": sha(args.baseline_out)}}
    args.manifest_out.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
