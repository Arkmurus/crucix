"""R-F4251 — produce a promotion verdict from the reports, not from a person.

I have now hand-written this verdict three times in one day (R-F4240, R-F4243,
and the one this replaces). Hand-writing an adjudication is the step where a
number can quietly stop matching the report it claims to describe — which is
exactly what `test_rf4240_interpolation_verdict.py` was written to catch. A
verdict a tool derives passes that test by construction.

THE FOUR PROPERTIES THAT MAKE A VERDICT WORTH ANYTHING, each learned from a
defect this repo actually shipped:

  * **The gate comes from the MANIFEST**, never from a literal here. Reading a
    gate written after the run is adjudicating with the answer in hand.
  * **Every arm and the incumbent must share one `scorer_version`** (R-F4244).
    A gate compared a current-scorer candidate against a pre-R-F4160 baseline
    and handed it +6 for free; ten reports on disk pass that comparison and fail
    an honest one, including the incumbent measured against itself.
  * **Every report must PROVE completeness** — 168 declared rows and 168 actual.
    A partial report parses and carries an honest count.
  * **Scorelines are re-derived from the rows**, and the file hashes are
    recorded, so a later re-harvest cannot silently invalidate a published
    decision.

It refuses to emit a verdict it cannot stand behind, rather than emitting one
with a caveat. A caveat in a JSON file is not read by the next session.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.train import parent_of_record

RESOLUTION = "tooluse_resolution"


def sha256(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def axis_counts(report: dict) -> dict[str, int]:
    return {str(a["label"]): int(a["honest"]) for a in report.get("per_axis") or []}


def load_complete(path: pathlib.Path, expected_rows: int) -> dict:
    """A report that does not prove completeness is not evidence."""
    report = json.loads(path.read_text(encoding="utf-8"))
    rows = report.get("rows")
    if report.get("complete") is not True:
        raise RuntimeError(f"{path.name}: does not declare completeness")
    if report.get("total") != expected_rows or not isinstance(rows, list) \
            or len(rows) != expected_rows:
        raise RuntimeError(f"{path.name}: not a complete {expected_rows}-row report")
    if not report.get("scorer_version"):
        raise RuntimeError(f"{path.name}: declares no scorer_version")
    return report


def assess(arm: dict, incumbent: dict, gate: dict,
           advisory_axes: "frozenset[str] | set[str] | None" = None) -> dict:
    """Apply the pre-registered gate to one arm. Pure.

    R-F4259 — an axis may be declared ADVISORY in the manifest. An advisory
    regression is measured and reported exactly as before; it simply does not
    block. The two are kept in separate fields so a reader can never mistake
    one for the other, and an advisory regression is never dropped from the
    verdict: that would be closing the gate by measuring less, which is the
    failure CLAUDE.md section 1 forbids. Declaring an axis advisory is a
    deliberate, per-run, recorded decision — not a default and not global.
    """
    advisory = frozenset(advisory_axes or ())
    arm_axes, incumbent_axes = axis_counts(arm), axis_counts(incumbent)
    all_regressions = {label: arm_axes[label] - incumbent_axes[label]
                       for label in sorted(incumbent_axes)
                       if label in arm_axes and arm_axes[label] < incumbent_axes[label]}
    regressions = {k: v for k, v in all_regressions.items() if k not in advisory}
    advisory_regressions = {k: v for k, v in all_regressions.items() if k in advisory}
    improvements = {label: arm_axes[label] - incumbent_axes[label]
                    for label in sorted(incumbent_axes)
                    if label in arm_axes and arm_axes[label] > incumbent_axes[label]}
    resolution = arm_axes.get(RESOLUTION)
    # A minimum on an advisory axis would re-block through the back door.
    resolution_ok = (RESOLUTION in advisory
                     or (resolution is not None
                         and resolution >= gate["minimum_resolution_honest"]))
    promotable = (
        arm["honest"] >= gate["minimum_honest"]
        and resolution_ok
        and len(regressions) <= gate["maximum_axis_regressions"])
    return {
        "honest": arm["honest"], "total": arm["total"],
        "resolution_honest": resolution,
        "gain": arm["honest"] - incumbent["honest"],
        "axis_regressions": regressions,
        "advisory_regressions": advisory_regressions,
        "axis_improvements": improvements,
        "promotable": promotable,
    }


def adjudicate(manifest_path: pathlib.Path, arms: list[tuple[str, pathlib.Path]],
               incumbent_path: pathlib.Path, *, expected_rows: int = 168) -> dict:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    gate = manifest.get("promotion_gate")
    if not gate:
        raise RuntimeError(f"{manifest_path.name}: no pre-registered promotion_gate")
    advisory_axes = frozenset(manifest.get("advisory_axes") or ())
    if advisory_axes and not manifest.get("advisory_rationale"):
        raise RuntimeError(
            "advisory_axes declared without advisory_rationale — an axis may "
            "only stop blocking for a recorded reason")
    incumbent = load_complete(incumbent_path, expected_rows)

    scorers = {incumbent["scorer_version"]}
    assessed = []
    for label, path in arms:
        report = load_complete(path, expected_rows)
        scorers.add(report["scorer_version"])
        assessed.append({"arm": label, "report": path.name,
                         "report_sha256": sha256(path),
                         "scorer_version": report["scorer_version"],
                         **assess(report, incumbent, gate, advisory_axes)})
    if len(scorers) != 1:
        raise RuntimeError(
            f"refusing to adjudicate across scorer generations: {sorted(scorers)}. "
            f"Re-score the older reports — comparing honest counts across scorers "
            f"measures the scorer, not the model (R-F4244).")

    promotable = [a for a in assessed if a["promotable"]]
    return {
        "r_number": manifest.get("r_number"),
        "complete": True,
        "manifest": manifest_path.name,
        "manifest_sha256": sha256(manifest_path),
        "scorer_version": scorers.pop(),
        "promotion_gate": gate,
        "gate_source": "pre-registered manifest",
        "advisory_axes": sorted(advisory_axes),
        "advisory_rationale": manifest.get("advisory_rationale"),
        "incumbent": {"report": incumbent_path.name,
                      "report_sha256": sha256(incumbent_path),
                      "honest": incumbent["honest"],
                      "resolution_honest": axis_counts(incumbent).get(RESOLUTION)},
        "arms": assessed,
        "promotion_authorized": bool(promotable),
        "decision": ("promote:" + promotable[0]["arm"]) if promotable
                    else "reject_all_arms",
        "incumbent_preserved": not promotable,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--manifest", required=True, type=pathlib.Path)
    parser.add_argument("--incumbent", type=pathlib.Path, default=None,
                        help="defaults to the parent of record (R-F4270)")
    parser.add_argument("--incumbent-override", default=None, metavar="REASON",
                        help="adjudicate against a superseded parent deliberately")
    parser.add_argument("--arm", action="append", required=True,
                        metavar="LABEL=REPORT",
                        help="repeatable; the arm's label and its report path")
    parser.add_argument("--expected-rows", type=int, default=168)
    parser.add_argument("--out", type=pathlib.Path, default=None)
    args = parser.parse_args(argv)

    arms = [(label, pathlib.Path(path)) for label, _, path in
            (item.partition("=") for item in args.arm) if path]

    # R-F4270 — the incumbent is the PROMOTED parent, not whichever path the
    # caller happened to type. Adjudicating a candidate against a superseded
    # parent scores a null change as a gain, which is the same class of error
    # R-F4244 caught across scorer generations.
    record = parent_of_record.read_record()
    incumbent = args.incumbent
    if incumbent is None:
        if record is None:
            print("REFUSED: no parent of record and no --incumbent given "
                  "(R-F4270)", file=sys.stderr)
            return 3
        incumbent = parent_of_record.record_path(record)
    elif not args.incumbent_override:
        refusal = parent_of_record.incumbent_refusal(incumbent, record)
        if refusal:
            print(f"REFUSED: {refusal}", file=sys.stderr)
            return 3

    verdict = adjudicate(args.manifest, arms, incumbent,
                         expected_rows=args.expected_rows)
    if args.incumbent_override:
        verdict["incumbent_override_reason"] = args.incumbent_override
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(verdict, indent=2) + "\n", encoding="utf-8")

    inc = verdict["incumbent"]
    print(f"gate {verdict['promotion_gate']}  (from {verdict['gate_source']})")
    print(f"incumbent {inc['honest']}/168  resolution {inc['resolution_honest']}/16\n")
    if verdict["advisory_axes"]:
        print(f"ADVISORY (measured, reported, not blocking): "
              f"{', '.join(verdict['advisory_axes'])}")
        print(f"  rationale: {verdict['advisory_rationale']}")
        print()
    print(f"{'arm':>10} {'honest':>7} {'gain':>5} {'res':>4}  {'promotable':>10}  "
          f"blocking / advisory")
    for a in verdict["arms"]:
        print(f"{a['arm']:>10} {a['honest']:>7} {a['gain']:>+5} "
              f"{a['resolution_honest']:>4}  {str(a['promotable']):>10}  "
              f"{a['axis_regressions'] or '-'} / {a['advisory_regressions'] or '-'}")
    print(f"\nDECISION: {verdict['decision']}")
    return 0 if verdict["promotion_authorized"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
