"""R-F4243 — stop length from being sufficient to win the resolution preference.

MEASURED, on `aria_tooluse_resolution_boundary_dpo_v1.jsonl` (32 rows): the
label is recoverable from LENGTH ALONE in 30 of them, and the direction flips
per branch.

    branch            n   median chosen   median rejected   chosen shorter
    unique_live      10             155               354           9 / 10
    ambiguous_live   10             480               320           0 / 10
    no_match         10             446               355           1 / 10
    dissolved_only    2             508               394           0 / 2

DPO can drive that loss down by learning *"be terse when one company matches, be
expansive otherwise"* and never learn to perform a selection at all. Worse, 22
of the 32 rows push toward LONGER, so the net gradient favours the expansive,
list-shaped answer — which is exactly the regression the sweep produced:
interpolation v2 newly broke `Meggitt`, `Cobham` and `Lockheed Martin UK
Limited`, every one of them *"did not select the resolved company"*.

THE FIX IS NOT TO MAKE ANSWERS UNNATURAL. A selection genuinely is shorter than
a clarification, and flattening that would teach a different lie. Length only
has to stop being SUFFICIENT: for every existing pair this adds a second pair,
same prompt and same chosen, whose rejected sits on the OTHER side of the length
divide. After that a model cannot separate chosen from rejected by counting
tokens, because both lengths appear on both sides of the same branch.

The two synthetic rejections are the two failure shapes measured in live evals,
not invented ones:

  * the FALSE DENIAL — the `Compass` error, where the model listed
    `COMPASS LTD (11466170)` and then said it had found nothing. Short, so it is
    the counter-example for `unique_live`, whose chosen answers are short.
  * the FIRST-ROW DEFAULT — the `Prudential` error, where the model listed five
    candidates and then declared *"The first result is PRUDENTIAL PUBLIC LIMITED
    COMPANY"*. Long, so it is the counter-example for the three branches whose
    chosen answers are long. It was covered in only 2 of 10 `ambiguous_live`
    rows, which is the branch where it actually costs a report.

Every synthetic rejection must FAIL the real corpus validator. One that passes
is a rejection that would teach the model to avoid a correct answer, so it
raises rather than being dropped — a silently skipped row would quietly restore
the confound this module exists to remove.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.train.build_resolution_boundary_dpo import (  # noqa: E402
    _trace,
    resolution_branch,
)
from scripts.train.build_tooluse_corpus import validate_trace  # noqa: E402

# Branches whose CHOSEN answer is the short one. Everything else is long-chosen.
SHORT_CHOSEN_BRANCHES = frozenset({"unique_live"})


def _load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in
            path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def registry_results(row: dict) -> list[dict]:
    """The candidate rows the model was shown for this subject."""
    for message in row.get("prompt") or []:
        if (isinstance(message, dict) and message.get("role") == "tool"
                and message.get("name") == "companies_house_search"):
            payload = json.loads(message.get("content") or "{}")
            return list(payload.get("results") or [])
    raise ValueError(f"no registry payload for {row.get('subject')!r}")


def false_denial(row: dict) -> str:
    """SHORT and wrong — the `Compass` shape, stripped to its essence."""
    return (f"I could not identify any company matching "
            f"{str(row.get('subject') or '').strip()}.")


def first_row_default(row: dict) -> str:
    """LONG and wrong — the `Prudential` shape: list, then commit anyway."""
    results = registry_results(row)
    if not results:
        raise ValueError(f"no candidates to list for {row.get('subject')!r}")
    subject = str(row.get("subject") or "").strip()
    lines = [f"I found {len(results)} companies matching '{subject}':", ""]
    for entry in results:
        lines.append(
            f"* {entry.get('title')} ({entry.get('company_number')})"
            f" - {entry.get('company_status')},"
            f" created {entry.get('date_of_creation')}")
    head = results[0]
    lines += ["", f"The first result is {head.get('title')} "
                  f"({head.get('company_number')}), created "
                  f"{head.get('date_of_creation')}. I will proceed on company "
                  f"number {head.get('company_number')}."]
    return "\n".join(lines)


def counter_example(row: dict, branch: str) -> dict:
    """Same prompt, same chosen, a rejection on the other side of the divide."""
    rejected = (false_denial(row) if branch in SHORT_CHOSEN_BRANCHES
                else first_row_default(row))
    errors = validate_trace(_trace(row, rejected))
    if not errors:
        raise ValueError(
            f"length counter-example for {row.get('subject')!r} PASSES the "
            f"validator — it would train the model away from a correct answer")
    return {**row, "rejected": rejected,
            "why": f"R-F4243 length counter-example ({branch})"}


# A branch where this share of pairs lean the same way is still a near-perfect
# length classifier. An all-or-nothing test would have called v1's unique_live
# (9 of 10 one way) "not separable", which is the absence-reads-as-health shape:
# the guard reports clean because it can only see the extreme case.
LENGTH_PREDICTIVE_SHARE = 0.8

# Below this, the skew is not evidence. `dissolved_only` has only 2 source rows,
# so it reads 0.75 after the fix — under the threshold, but on 4 pairs. The
# manifest marks it rather than reporting a clean number nobody can rely on.
MINIMUM_PAIRS_FOR_A_SKEW = 8


def length_signal(rows: list[dict]) -> dict[str, dict]:
    """Per branch: how well does length alone separate chosen from rejected?

    `length_predictive` is the load-bearing field. It is a SHARE, not an
    all-or-nothing test, because 9 of 10 leaning one way is still a model that
    can win by counting tokens.
    """
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        grouped[resolution_branch(row)].append(row)
    report = {}
    for branch, members in sorted(grouped.items()):
        shorter = sum(1 for row in members
                      if len(row["chosen"]) < len(row["rejected"]))
        longer = len(members) - shorter
        skew = max(shorter, longer) / len(members)
        report[branch] = {
            "pairs": len(members),
            "chosen_shorter": shorter,
            "chosen_longer": longer,
            "median_chosen": int(statistics.median(len(r["chosen"]) for r in members)),
            "median_rejected": int(statistics.median(len(r["rejected"]) for r in members)),
            "length_skew": round(skew, 3),
            "length_predictive": skew >= LENGTH_PREDICTIVE_SHARE,
            "fully_separable": shorter in (0, len(members)),
            # A skew over 4 pairs carries almost no information either way.
            # Say so in the artefact rather than letting a thin branch certify.
            "underpowered": len(members) < MINIMUM_PAIRS_FOR_A_SKEW,
        }
    return report


def build(rows: list[dict]) -> tuple[list[dict], dict]:
    """Return the length-controlled curriculum and its before/after evidence."""
    before = length_signal(rows)
    out = list(rows)
    added: Counter[str] = Counter()
    for row in rows:
        branch = resolution_branch(row)
        out.append(counter_example(row, branch))
        added[branch] += 1
    after = length_signal(out)
    still = [b for b, stats in after.items() if stats["length_predictive"]]
    if still:
        raise ValueError(
            f"length still predicts the label in {still} — the counter-examples "
            f"did not break the confound")
    return out, {"before": before, "after": after,
                 "counter_examples_added": dict(sorted(added.items()))}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args(argv)

    rows = _load_jsonl(args.input)
    curriculum, evidence = build(rows)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in curriculum),
        encoding="utf-8", newline="\n")
    manifest = {
        "r_number": "R-F4243",
        "complete": True,
        "policy": "length_controlled_resolution_preference_pairs",
        "source_rows": len(rows),
        "curriculum_rows": len(curriculum),
        "length_evidence": evidence,
        "input_sha256": _sha(args.input),
        "output_sha256": _sha(args.output),
    }
    args.manifest.write_text(json.dumps(manifest, indent=2) + "\n",
                             encoding="utf-8", newline="\n")
    print(json.dumps({k: manifest[k] for k in
                      ("source_rows", "curriculum_rows", "length_evidence")},
                     indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
