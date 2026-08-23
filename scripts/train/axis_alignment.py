"""R-F4275 — an axis may only BLOCK promotion where the LLM is the mechanism.

R-F4257 discovered this the expensive way, for one axis. Thirteen candidates and
arms were funded trying to move `tooluse_resolution`, and the finding that closed
it was that **production never asks the model to resolve an entity**: identity is
resolved deterministically, the model is handed the resolved company, and its
answer is REPLACED when identity is gated. The axis measured a configuration that
never ships. The operator's decision was to make it advisory.

That was reached by hand, for one axis, after ~13 funded runs. This module makes
the same judgement **mechanical, and for all of them**, from ARIA's own standard.

THE RULE. `dd_standard.QUESTIONS` gives every fundamental a `reader` — the
deterministic function that answers it in production. Fourteen of the twenty-four
have one. Where a reader exists, the shipped pipeline answers that question with
CODE, and whatever the model would have said is not what the customer receives.
An eval axis over such a question therefore measures a capability the product
does not use. It is worth keeping as defence in depth; it is not worth blocking a
promotion over, and it is certainly not worth funding a GPU cycle to move.

Where no reader exists — IS-14 (PEP), IS-15 (negative news), IS-16 (convictions
and penalties), LR-19 (source of wealth), LR-20 (economic rationale), OC-7 (group
structure), FS-9 (overdue filings), EI-3, EI-4, OC-8 — the model IS the
mechanism. Those are judgement questions over unstructured evidence, they are
where value density lives (`docs/golden_intel_north_star_2026_07_14.md`), and
they are the only place a training cycle can buy the product anything.

THE VALIDATION, and it is why this rule can be trusted: applied to
`tooluse_resolution` it returns DETERMINISTIC — **independently reproducing the
verdict R-F4259 reached by hand after thirteen funded attempts.** A rule that
agrees with a decision earned the hard way is worth more than one argued from
first principles. `test_rf4275` pins that agreement, so if the rule ever stops
reproducing it, the rule is wrong and says so.

WHAT THIS IS NOT. It does not drop an axis, hide a regression, or lower a
threshold — that is the "close the gate by measuring less" failure CLAUDE.md §1
forbids, and R-F4259 already built the honest mechanism: advisory means MEASURED
AND REPORTED, in its own column, just not blocking. This module decides which
axes belong in that column and refuses to do anything else.

BEHAVIOUR AXES ARE ALWAYS LLM-MECHANISM. `tooluse_challenge` sits over IS-13,
which has a reader — but what it measures is the model refusing to rubber-stamp
"they're fine, just confirm it", and no deterministic reader can supply conduct.
Classifying it by its underlying question would demote the honesty behaviours
that are the entire point of the corpus.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aria_service.intel.dd_standard import QUESTIONS_BY_ID, STANDARD_VERSION  # noqa: E402
from scripts.train.fundamentals_coverage import (  # noqa: E402
    AXIS_COVERAGE, BEHAVIOUR,
)

LLM = "llm"                    # the model is the mechanism — may block
DETERMINISTIC = "deterministic"  # code answers this in production — advisory only
UNKNOWN = "unknown"            # could not measure; never silently "may block"


def fundamental_mechanism(fid: str) -> str:
    """Who answers this question in the shipped product.

    Read from the LIVE standard, never a copy: attaching a reader to a
    fundamental is exactly the event that should move an axis out of the
    blocking set, and a hardcoded list here would not notice it.
    """
    question = QUESTIONS_BY_ID.get(fid)
    if question is None:
        return UNKNOWN
    return DETERMINISTIC if getattr(question, "reader", None) is not None else LLM


def axis_mechanism(axis: str) -> dict:
    """Classify one eval axis, with the evidence that produced the verdict."""
    entry = AXIS_COVERAGE.get(axis)
    if entry is None:
        return {"axis": axis, "mechanism": UNKNOWN,
                "why": "no coverage declaration — an undeclared axis cannot be "
                       "shown to measure work the product uses (R-F4271)",
                "fundamentals": {}}

    per = {fid: fundamental_mechanism(fid) for fid in entry["fundamentals"]}
    if entry["kind"] == BEHAVIOUR:
        return {"axis": axis, "mechanism": LLM, "fundamentals": per,
                "why": "behaviour axis — it measures the model's own conduct "
                       "(refusing to rubber-stamp, contradicting a premise from "
                       "evidence), which no deterministic reader can supply"}
    if UNKNOWN in per.values():
        return {"axis": axis, "mechanism": UNKNOWN, "fundamentals": per,
                "why": "names a fundamental the standard does not have"}
    # ANY judgement question makes the axis judgement work. An axis that touches
    # even one question production cannot answer in code is exercising the model.
    if any(m == LLM for m in per.values()):
        return {"axis": axis, "mechanism": LLM, "fundamentals": per,
                "why": "covers " + ", ".join(sorted(f for f, m in per.items()
                                                    if m == LLM))
                       + ", which production has no deterministic reader for"}
    return {"axis": axis, "mechanism": DETERMINISTIC, "fundamentals": per,
            "why": "every fundamental it covers (" + ", ".join(sorted(per))
                   + ") is answered in production by a deterministic reader, so "
                     "the model's answer is not what the customer receives"}


def classify_all() -> dict[str, dict]:
    return {axis: axis_mechanism(axis) for axis in sorted(AXIS_COVERAGE)}


def blocking_violations(blocking_axes: "set[str] | list[str]") -> list[str]:
    """Axes currently allowed to BLOCK that the rule says should be advisory.

    An UNKNOWN classification is a violation too. "I could not establish that
    this measures work the product uses" must not be spent against — the same
    fail-closed direction every other gate in this repo takes.
    """
    out = []
    for axis in sorted(set(blocking_axes)):
        verdict = axis_mechanism(axis)
        if verdict["mechanism"] != LLM:
            out.append(f"{axis}: {verdict['mechanism'].upper()} — {verdict['why']}")
    return out


def recommended_advisory() -> list[str]:
    return sorted(a for a, v in classify_all().items() if v["mechanism"] != LLM)


def main(argv: "list[str] | None" = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=pathlib.Path, default=None)
    args = parser.parse_args(argv)

    verdicts = classify_all()
    llm = [a for a, v in verdicts.items() if v["mechanism"] == LLM]
    det = [a for a, v in verdicts.items() if v["mechanism"] == DETERMINISTIC]

    print(f"ARIA due-diligence standard v{STANDARD_VERSION}\n")
    print(f"{'axis':<32}{'mechanism':<15}covers")
    for axis, verdict in verdicts.items():
        covers = ", ".join(f"{f}({m[0].upper()})"
                           for f, m in sorted(verdict["fundamentals"].items()))
        print(f"{axis:<32}{verdict['mechanism']:<15}{covers or '-'}")

    print(f"\nMAY BLOCK  ({len(llm)}): {', '.join(llm)}")
    print(f"ADVISORY   ({len(det)}): {', '.join(det)}")
    print("\nfundamentals with NO deterministic reader — where a cycle can buy "
          "the product something:")
    print("  " + ", ".join(sorted(fid for fid in QUESTIONS_BY_ID
                                  if fundamental_mechanism(fid) == LLM)))

    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps({
            "standard_version": STANDARD_VERSION,
            "axes": verdicts,
            "may_block": llm,
            "advisory": det,
            "llm_fundamentals": sorted(fid for fid in QUESTIONS_BY_ID
                                       if fundamental_mechanism(fid) == LLM),
        }, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
