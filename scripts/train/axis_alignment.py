"""R-F4275 — an axis may only BLOCK promotion where production does not
answer the question in CODE. (R-F4276 corrected the claim below.)

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

⚠️ **R-F4276 — the first version of this paragraph claimed those ten are "where
the model IS the mechanism". THAT WAS NOT MEASURED AND IS FALSE.** Running
`dd_standard.assess` against a synthetic report shows the honest three-way split:

    IS-13 -> NOT_RUN "no sanctions screen is recorded on this report"
    IS-15 -> NOT_RUN "no resolver is bound to this question in this build"

The first means the reader LOOKED and found nothing on that report. The second
means **nothing answers the question at all** — not code, not the model. The ten
reader-less fundamentals (EI-3, EI-4, OC-7, OC-8, FS-9, IS-14, IS-15, IS-16,
LR-19, LR-20) are UNBOUND, and an axis over one of them measures an ASPIRATION.

That is a PRODUCT gap, not a training target, and closing it is worth more than a
cycle: IS-15's pass condition is *"a dedicated media sweep ran and a backend
answered"*, which the DD pipeline already does on every run — the work is being
performed and the standard cannot credit it (C-235).

The BLOCKING rule is unaffected and never depended on the wrong half: where code
answers a question, the model's answer is not what the customer receives, so an
axis over it is defence in depth and must not block or justify GPU spend.

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

# R-F4276 CORRECTION. The first version of this module called the non-reader
# class "llm" and asserted "the model IS the mechanism". That was NOT measured and
# it is not true in general. `dd_standard.assess` was run against a synthetic
# report and the honest picture is three-way, not two:
#
#   IS-13 -> NOT_RUN "no sanctions screen is recorded on this report"   reader LOOKED
#   IS-15 -> NOT_RUN "no resolver is bound to this question in this build"  NOBODY looked
#
# A fundamental without a reader is UNBOUND — nothing answers it, the model
# included. What survives is the BLOCKING rule, which never depended on the wrong
# half: an axis may block only where production does not answer the question in
# CODE, because where code answers it the model's answer is not what ships.
# R-F4278 — READER PRESENCE IS A CANDIDATE, NOT A DEMOTION.
#
# Binding a reader to IS-15 (R-F4277) demoted `tooluse_adverse` and
# `tooluse_news_impact` on the spot, leaving three blocking axes — and the
# reductio is that binding readers to the remaining unbound questions, which is
# GOOD product work, would eventually leave nothing able to block at all. A gate
# that dissolves as the product improves is not a gate.
#
# The proxy was wrong in a specific way: a reader answers the QUESTION, it does
# not stop the model's narrative reaching the customer. What R-F4257 actually
# established for resolution was far stronger — `enforce_resolution_response`
# REPLACES the model's answer on both response surfaces, so whatever it said is
# genuinely not what shipped. That is override evidence, and nothing else in the
# eval has it.
#
# So: reader-presence makes an axis a CANDIDATE for advisory; demoting it needs
# the R-F4257-style evidence recorded in OUTPUT_OVERRIDDEN. Conservative in the
# safe direction — an unproven axis keeps blocking, which makes promotion harder,
# never easier.
NOT_DETERMINISTIC = "not_deterministic"  # code does not answer it — may block
DETERMINISTIC = "deterministic"          # code answers it in production — advisory only
UNKNOWN = "unknown"                      # could not measure; never silently "may block"

# Kept so existing callers and reports do not break; the NAME was the error.
LLM = NOT_DETERMINISTIC


#: Axes where the model's answer is DEMONSTRABLY not what the customer receives.
#: One entry per axis, each naming the override and where it was measured. This is
#: the only thing that turns a candidate into an advisory axis.
OUTPUT_OVERRIDDEN: dict[str, str] = {
    "tooluse_resolution":
        "enforce_resolution_response (R-F4144) REPLACES the model's answer when "
        "identity is gated, on BOTH the chat and streaming surfaces, and yields a "
        "{'type':'replace'} event so already-streamed text is corrected; pinned in "
        "CI by test_rf4144. Measured in R-F4257: 0 of 168 eval rows carry the "
        "production gate marker and the pod evaluator never imports companies_house.",
}


def fundamental_mechanism(fid: str) -> str:
    """Who answers this question in the shipped product.

    Read from the LIVE standard, never a copy: attaching a reader to a
    fundamental is exactly the event that should move an axis out of the
    blocking set, and a hardcoded list here would not notice it.
    """
    question = QUESTIONS_BY_ID.get(fid)
    if question is None:
        return UNKNOWN
    return (DETERMINISTIC if getattr(question, "reader", None) is not None
            else NOT_DETERMINISTIC)


def is_unbound(fid: str) -> bool:
    """Nothing answers this question in this build — not code, not the model.

    `dd_standard` renders it NOT_RUN with "no resolver is bound to this question
    in this build", which is honest and must never read as clear. An axis over an
    unbound fundamental is measuring an ASPIRATION: worth knowing, and a product
    gap to close, but not evidence the model's behaviour there reaches a customer.
    """
    question = QUESTIONS_BY_ID.get(fid)
    if question is None:
        return False
    return (getattr(question, "reader", None) is None
            and getattr(question, "established_by", "") != "SUPPLIED")


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
    if any(m == NOT_DETERMINISTIC for m in per.values()):
        loose = sorted(f for f, m in per.items() if m == NOT_DETERMINISTIC)
        unbound = sorted(f for f in loose if is_unbound(f))
        why = ("covers " + ", ".join(loose)
               + ", which production has no deterministic reader for")
        if unbound:
            why += (" — and " + ", ".join(unbound) + " is UNBOUND (nothing "
                    "answers it in this build, so this axis measures an "
                    "aspiration until a resolver is bound)")
        return {"axis": axis, "mechanism": NOT_DETERMINISTIC, "fundamentals": per,
                "unbound": unbound, "why": why}
    covered = ", ".join(sorted(per))
    override = OUTPUT_OVERRIDDEN.get(axis)
    if override:
        return {"axis": axis, "mechanism": DETERMINISTIC, "fundamentals": per,
                "advisory_candidate": True, "override_evidence": override,
                "why": f"every fundamental it covers ({covered}) has a "
                       f"deterministic reader AND the model's answer is overridden: "
                       f"{override}"}
    return {"axis": axis, "mechanism": NOT_DETERMINISTIC, "fundamentals": per,
            "advisory_candidate": True, "override_evidence": None,
            "why": f"every fundamental it covers ({covered}) has a deterministic "
                   f"reader, so this is a CANDIDATE for advisory — but no evidence "
                   f"is recorded that the model's answer fails to reach the "
                   f"customer, and a reader answers the question without silencing "
                   f"the narrative. It keeps blocking until that is measured "
                   f"(R-F4278)"}


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
