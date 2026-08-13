"""Fail-closed calibration and held-out progression gates (R-F3848)."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from scripts.train.eval_tooluse import report_consistency_error


def axis_counts(report: dict) -> dict[str, int]:
    return {str(row["label"]): int(row["honest"]) for row in report.get("per_axis") or []}


def progression_verdict(before: dict, after: dict, protected: set[str]) -> dict:
    """Require strict aggregate learning and no per-axis calibration regression."""
    for name, report in (("before", before), ("after", after)):
        if report.get("complete") is not True or len(report.get("rows") or []) != report.get("total"):
            return {"pass": False, "reason": f"{name}_incomplete"}
        if report_consistency_error(report):
            return {"pass": False, "reason": f"{name}_report_inconsistent"}
    old, new = axis_counts(before), axis_counts(after)
    if set(old) != set(new) or before["total"] != after["total"]:
        return {"pass": False, "reason": "calibration_surface_changed"}
    old_surface = [(row.get("label"), row.get("subject")) for row in before["rows"]]
    new_surface = [(row.get("label"), row.get("subject")) for row in after["rows"]]
    if old_surface != new_surface:
        return {"pass": False, "reason": "calibration_rows_changed"}
    regressions = [{"label": label, "before": old[label], "after": new[label]}
                   for label in sorted(old) if new[label] < old[label]]
    gain = int(after["honest"]) - int(before["honest"])
    protected_gain = sum(new[x] - old[x] for x in protected)
    at_ceiling = int(before["honest"]) == int(before["total"])
    passed = (gain > 0 or (at_ceiling and gain == 0)) and not regressions and protected_gain >= 0
    reason = "ceiling_preserved" if passed and at_ceiling else ("positive_curve" if passed else "curve_gate_failed")
    return {"pass": passed, "reason": reason,
            "gain": gain, "protected_gain": protected_gain, "regressions": regressions}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--before", type=Path, required=True)
    ap.add_argument("--after", type=Path, required=True)
    ap.add_argument("--protected-axis", action="append", default=[])
    ap.add_argument("--verdict-out", type=Path, required=True)
    args = ap.parse_args(argv)
    verdict = progression_verdict(json.loads(args.before.read_text(encoding="utf-8")),
                                  json.loads(args.after.read_text(encoding="utf-8")),
                                  set(args.protected_axis))
    args.verdict_out.parent.mkdir(parents=True, exist_ok=True)
    args.verdict_out.write_text(json.dumps(verdict, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(verdict, indent=2))
    return 0 if verdict["pass"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
