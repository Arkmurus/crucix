"""Fail-closed calibration and held-out progression gates (R-F3848).

R-F4244 — THE GATE COULD CERTIFY A NULL CHANGE, because it compared honest
counts across SCORER GENERATIONS without noticing.

Measured 2026-08-23. The pinned parent baseline
(`aria_tooluse_curve_sft_v5_heldout_rf4031_rescored.json`) carries
`scorer_version: None` and reads **155/168, resolution 11/16**. Re-grading the
SAME stored answers with the current scorer — a pure re-score, no inference —
gives **161/168, resolution 13/16**. Candidates are always evaluated with the
CURRENT scorer, so every one of them was handed a **+6 aggregate and +2
protected-axis head start** by nothing but the passage of time.

Every existing check was blind to it. Completeness, consistency, the axis-label
set and the (label, subject) row surface are all IDENTICAL across a re-grade —
that is what a re-grade means — so `calibration_rows_changed` can never fire.
The bias is also one-directional: the older scorer under-counted, so the
aggregate test, the protected-axis test AND the per-axis regression test are all
computed against under-counted numbers and all lean the same way.

Swept over every 168-row report on disk, **10 of them pass the shipped gate and
fail an honest same-scorer comparison**. Two are unanswerable:

  * `aria_tooluse_incumbent_rf4160_rescored.json` — the parent adapter measured
    against ITSELF, certified as `positive_curve`.
  * `aria_tooluse_curve_sft_v5_heldout_rf4160_rescored.json` — the baseline's
    own answers, merely re-graded. Identical model, identical eval set, zero
    behavioural difference, and the gate reports progress.

Four of the interpolation arms that R-F4164 and R-F4240 REJECTED also pass it.
No bad adapter reached ARIA — promotion was adjudicated separately against a
same-scorer incumbent, and R-F4163's downstream rescore is the workaround that
has been quietly compensating — but the gate that decides whether a paid cycle
SUCCEEDED has been systematically optimistic.

So comparability is now a precondition, not an afterthought, and it fails
CLOSED: a missing scorer version is "I cannot compare these", never "they
match". A false fail costs a free re-score; a false pass costs a wrong
conclusion about what a paid run proved.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from scripts.train.eval_tooluse import report_consistency_error


def axis_counts(report: dict) -> dict[str, int]:
    return {str(row["label"]): int(row["honest"]) for row in report.get("per_axis") or []}


def scorer_comparability(before: dict, after: dict) -> dict | None:
    """None when the two reports may be compared; a failing verdict otherwise.

    Returns a verdict rather than raising so the caller's existing fail-closed
    contract carries it, and names WHICH side is unknown — the fix for a missing
    version is a free re-score of that report, and the message should say so
    without anyone having to open both files.
    """
    versions = {name: report.get("scorer_version")
                for name, report in (("before", before), ("after", after))}
    unknown = sorted(name for name, value in versions.items() if not value)
    if unknown:
        return {"pass": False, "reason": "scorer_version_unknown",
                "unknown_side": unknown, "scorer_versions": versions,
                "remedy": "re-score the unversioned report with "
                          "scripts/train/rescore_tooluse_report.py — it is a "
                          "re-grade of stored answers, no inference and no spend"}
    if versions["before"] != versions["after"]:
        return {"pass": False, "reason": "scorer_generation_mismatch",
                "scorer_versions": versions,
                "remedy": "re-score the older report to the current scorer; "
                          "comparing honest counts across scorers measures the "
                          "scorer, not the model"}
    return None


def progression_verdict(before: dict, after: dict, protected: set[str]) -> dict:
    """Require strict aggregate learning and no per-axis calibration regression."""
    for name, report in (("before", before), ("after", after)):
        if report.get("complete") is not True or len(report.get("rows") or []) != report.get("total"):
            return {"pass": False, "reason": f"{name}_incomplete"}
        if report_consistency_error(report):
            return {"pass": False, "reason": f"{name}_report_inconsistent"}
    verdict = scorer_comparability(before, after)
    if verdict is not None:
        return verdict
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
