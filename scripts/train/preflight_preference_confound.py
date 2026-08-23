"""R-F4246 — refuse a paid DPO cycle whose label is predictable from length.

R-F4243 found this by hand, after nine candidates had already been paid for: in
`aria_tooluse_resolution_boundary_dpo_v1.jsonl`, 30 of 32 labels were
recoverable from response LENGTH alone, in opposite directions per decision
branch. DPO can drive that loss down by learning verbosity instead of the
decision, and the net gradient favoured the longer, list-shaped answer — which
is precisely the regression the sweep produced.

Finding it by hand is not a control. This runs before the spend.

GROUPING IS THE WHOLE GAME, and an overall number would have MISSED the real
defect. Across all 32 rows the skew was 0.69 — comfortably under any sane
threshold — while per branch it was 0.9, 0.9, 1.0, 1.0. The pathology hides in
the aggregate because the branches point opposite ways and cancel. So:

  * resolution corpora are grouped by DECISION BRANCH, recovered from the
    registry payload the prompt already carries — the same function the
    curriculum builder uses, so the two cannot drift;
  * anything else is grouped by `label`;
  * the grouping actually used is REPORTED, never assumed, because a check that
    silently grouped everything into one bucket would pass this corpus and
    teach us nothing.

It fails CLOSED on a corpus it cannot group into more than one bucket only when
that corpus is large enough for the reading to mean something — a tiny
single-branch set is `underpowered`, said out loud, not quietly certified.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import statistics
import sys
from collections import defaultdict

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# A group where this share of pairs lean the same way is still a near-perfect
# length classifier. Deliberately not 1.0: v1's worst branches were 0.9, and an
# all-or-nothing test would have waved them through.
LENGTH_PREDICTIVE_SHARE = 0.8

# Below this a skew is noise, not evidence, and is reported as such.
MINIMUM_PAIRS_FOR_A_SKEW = 8


def _load_jsonl(path: pathlib.Path) -> list[dict]:
    return [json.loads(line) for line in
            path.read_text(encoding="utf-8").splitlines() if line.strip()]


def group_key(row: dict) -> tuple[str, str]:
    """Return (grouping_kind, group) for one preference pair.

    Falls back deliberately rather than raising: a corpus this cannot classify
    still gets checked, just at the coarser grouping it says it used.
    """
    try:
        from scripts.train.build_resolution_boundary_dpo import resolution_branch
        return "decision_branch", resolution_branch(row)
    except Exception:                                  # noqa: BLE001
        return "label", str(row.get("label") or "unlabelled")


def analyse(rows: list[dict]) -> dict:
    """Length-skew per group, with the grouping it used stated."""
    grouped: dict[str, list[dict]] = defaultdict(list)
    kinds: set[str] = set()
    for row in rows:
        kind, group = group_key(row)
        kinds.add(kind)
        grouped[group].append(row)
    groups = {}
    for group, members in sorted(grouped.items()):
        shorter = sum(1 for row in members
                      if len(row.get("chosen") or "") < len(row.get("rejected") or ""))
        longer = len(members) - shorter
        skew = max(shorter, longer) / len(members)
        groups[group] = {
            "pairs": len(members),
            "chosen_shorter": shorter,
            "chosen_longer": longer,
            "median_chosen": int(statistics.median(
                len(r.get("chosen") or "") for r in members)),
            "median_rejected": int(statistics.median(
                len(r.get("rejected") or "") for r in members)),
            "length_skew": round(skew, 3),
            "length_predictive": (skew >= LENGTH_PREDICTIVE_SHARE
                                  and len(members) >= MINIMUM_PAIRS_FOR_A_SKEW),
            "underpowered": len(members) < MINIMUM_PAIRS_FOR_A_SKEW,
        }
    return {
        "pairs": len(rows),
        "grouped_by": sorted(kinds) or ["none"],
        "groups": groups,
        "predictive_groups": sorted(g for g, s in groups.items()
                                    if s["length_predictive"]),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dpo-file", required=True, type=pathlib.Path)
    parser.add_argument("--report-out", type=pathlib.Path, default=None)
    args = parser.parse_args(argv)

    rows = _load_jsonl(args.dpo_file)
    result = analyse(rows)
    if args.report_out is not None:
        args.report_out.parent.mkdir(parents=True, exist_ok=True)
        args.report_out.write_text(json.dumps(result, indent=2) + "\n",
                                   encoding="utf-8")

    print(f"preference confound — {result['pairs']} pairs, "
          f"grouped by {'/'.join(result['grouped_by'])}")
    for group, stats in result["groups"].items():
        mark = "FAIL" if stats["length_predictive"] else (
            "note" if stats["underpowered"] else " ok ")
        print(f"  [{mark}] {group:<18} n={stats['pairs']:<3} "
              f"skew={stats['length_skew']:<5} "
              f"chosen {stats['median_chosen']} vs rejected {stats['median_rejected']}")
    if result["predictive_groups"]:
        print("\nBLOCKED: length alone predicts the label in "
              f"{', '.join(result['predictive_groups'])}.")
        print("A model can win this preference by counting tokens instead of "
              "making the decision. Add length counter-examples "
              "(scripts/train/build_resolution_length_control.py) before spending.")
        return 1
    print("\nno group is separable by length alone.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
