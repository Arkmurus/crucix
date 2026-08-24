"""R-F4271 — which of ARIA's Twenty Fundamentals can the tool-use eval actually see?

WHY THIS EXISTS. The promoted parent scores **162/168**, and four of its six misses
sit on `tooluse_resolution`, which R-F4259 reclassified ADVISORY. That leaves **two
addressable rows in the entire harness**. Thirteen funded candidates in a row failed
to promote, and the standing explanation was always curriculum design. It is not:
a harness with two rows of headroom cannot show a gain, whatever you train.

So the question stopped being "what curriculum next" and became "what can this eval
see at all". Measured 2026-08-23 by reading the rows, not the docs: all 168 declare
the same **four tools** — `companies_house_search`, `companies_house_officers`,
`screen`, `web_search` — and every question is a sanctions, adverse-media or
identity question. ARIA's own due-diligence standard (`dd_standard.QUESTIONS`) has
**24 questions across five clusters**. Two entire clusters — FINANCIAL_STANDING and
LEGITIMACY_REGULATION — have no eval row at all, and ARIA already ships real, free
adapters for most of them (`get_insolvency`, `get_charges`, `get_psc`,
`search_disqualified_officers`, `get_filing_history`, `fetch_accounts_figures`).

This module states that, as a number, from the LIVE registry.

THE THREE THINGS IT REFUSES TO DO, each of them a failure this repo has shipped:

  * **It never infers coverage from tool overlap.** A row that calls
    `companies_house_search` does not thereby test FS-11 (insolvency). Deriving
    coverage from a shared resolver is exactly C-39, where a successful screen was
    used to stamp eight lists CLEAN that had never been queried. Coverage is
    DECLARED per axis, and the declaration is what gets audited.
  * **It never lets a new fundamental disappear.** The registry is iterated live, so
    a 25th question added to `dd_standard` shows up as UNCOVERED on the next run
    rather than being silently outside the denominator. A copy of the ids here would
    rot the moment the standard moved.
  * **It never lets a new axis claim coverage by omission.** An eval axis with no
    declaration is an ERROR, not "covers nothing" and not "covers everything". The
    same for a declaration naming a fundamental the registry does not have: that is
    a rename or a deletion, and it fails loudly instead of quietly shrinking the
    denominator.

`kind` separates the two things an axis can be. A FUNDAMENTAL axis tests a DD
question. A BEHAVIOUR axis tests a cross-cutting honesty property — refusing to
rubber-stamp a confident premise (`tooluse_challenge`), contradicting the user from
evidence (`tooluse_contradiction`). Both matter and both are counted, but they are
never merged: 51 rows of honesty behaviour must not read as financial-standing
coverage.
"""
from __future__ import annotations

import argparse
import collections
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aria_service.intel.dd_standard import QUESTIONS, STANDARD_VERSION  # noqa: E402

FUNDAMENTAL = "fundamental"
BEHAVIOUR = "behaviour"

# Declared, not inferred. Each entry records the DD questions an axis genuinely
# exercises, read from the rows themselves (the user question and the tool calls
# the trace actually makes) — never from what its tools *could* have answered.
AXIS_COVERAGE: dict[str, dict] = {
    "tooluse_resolution": {
        "kind": FUNDAMENTAL, "fundamentals": ("EI-1", "EI-2"),
        "why": "resolves a name to one registry record, or refuses to; establishes "
               "that the entity is registered and currently active, and its exact "
               "legal name and number",
    },
    "tooluse_trace": {
        "kind": FUNDAMENTAL, "fundamentals": ("IS-13",),
        "why": "screens the subject entity against sanctions and denied-party lists",
    },
    "tooluse_trace_unavailable": {
        "kind": FUNDAMENTAL, "fundamentals": ("IS-13",),
        "why": "the same screen when the tool cannot answer — the honest-unavailable "
               "half of IS-13, which is the half that must never read as clean",
    },
    "tooluse_person": {
        "kind": FUNDAMENTAL, "fundamentals": ("IS-13b",),
        "why": "screens a named natural person in their own name",
    },
    "tooluse_adverse": {
        "kind": FUNDAMENTAL, "fundamentals": ("IS-15",),
        "why": "adverse-media sweep and materiality grading",
    },
    "tooluse_news_impact": {
        "kind": FUNDAMENTAL, "fundamentals": ("IS-15",),
        "why": "current reporting on the subject and whether it changes exposure",
    },
    "tooluse_multihop": {
        "kind": FUNDAMENTAL, "fundamentals": ("EI-1", "OC-6", "IS-13b"),
        "why": "resolve the entity, read its officers from the registry, then screen "
               "a named officer — the only axis that chains registry to screening",
    },
    # R-F4272 — the first three of the missing axes, built on REAL Companies
    # House payloads. Declared here so they are auditable from the moment they
    # exist; they credit nothing until rows carrying them are actually in an
    # eval, which is what `coverage(rows)` enforces.
    "tooluse_insolvency": {
        "kind": FUNDAMENTAL, "fundamentals": ("FS-11",),
        "why": "reads the Companies House insolvency register; grades a recorded "
               "insolvency history, a genuinely empty register, and a register "
               "that did not answer, which must never read as the second",
    },
    "tooluse_charges": {
        "kind": FUNDAMENTAL, "fundamentals": ("FS-12",),
        "why": "reads the charges register and must separate charges still "
               "OUTSTANDING from those long since satisfied — 51 registered and "
               "6 live is neither 'no charges' nor '51 charges'",
    },
    "tooluse_ownership": {
        "kind": FUNDAMENTAL, "fundamentals": ("OC-5",),
        "why": "reads the PSC register for the natural persons ultimately in "
               "control, and distinguishes the four states an empty register can "
               "mean: named, lawfully exempt, unexplained, and unreadable",
    },
    "tooluse_contradiction": {
        "kind": BEHAVIOUR, "fundamentals": ("IS-13", "IS-15"),
        "why": "contradicting the user's premise from gathered evidence; the "
               "underlying question is a screen, the property under test is honesty",
    },
    "tooluse_challenge": {
        "kind": BEHAVIOUR, "fundamentals": ("IS-13",),
        "why": "refusing to rubber-stamp a confident 'they're fine, just confirm it'",
    },
    "tooluse_challenge_unavailable": {
        "kind": BEHAVIOUR, "fundamentals": ("IS-13",),
        "why": "the same refusal when the screening tool cannot answer",
    },
}


def _registry() -> dict[str, object]:
    return {q.id: q for q in QUESTIONS}


def declaration_errors(axes: "set[str] | None" = None) -> list[str]:
    """Fail closed: an undeclared axis or an unknown fundamental is an error.

    Both directions matter. An axis nobody declared would otherwise contribute
    nothing to the denominator while still consuming eval rows; a declaration
    naming a fundamental the standard no longer has would shrink the denominator
    and make coverage look better because the question was DELETED.
    """
    known = _registry()
    errors = []
    for axis, entry in sorted(AXIS_COVERAGE.items()):
        if entry["kind"] not in (FUNDAMENTAL, BEHAVIOUR):
            errors.append(f"{axis}: unknown kind {entry['kind']!r}")
        if not entry.get("why"):
            errors.append(f"{axis}: declares coverage without a recorded reason")
        for fid in entry["fundamentals"]:
            if fid not in known:
                errors.append(
                    f"{axis}: declares {fid}, which is not in dd_standard "
                    f"v{STANDARD_VERSION} — renamed or removed, not silently dropped")
    for axis in sorted(axes or ()):
        if axis not in AXIS_COVERAGE:
            errors.append(
                f"{axis}: eval axis with no coverage declaration — an axis may not "
                f"claim coverage by omission (R-F4271)")
    return errors


def coverage(eval_rows: "list[dict] | None" = None) -> dict:
    """The ledger. Iterates the LIVE registry so a new question cannot hide."""
    known = _registry()
    rows = eval_rows or []
    per_axis_rows = collections.Counter(str(r.get("label") or "") for r in rows)
    errors = declaration_errors(set(per_axis_rows) if rows else None)
    if errors:
        raise RuntimeError("coverage declaration is not sound:\n- " + "\n- ".join(errors))

    covered_by: dict[str, list[str]] = {fid: [] for fid in known}
    for axis, entry in AXIS_COVERAGE.items():
        if rows and not per_axis_rows.get(axis):
            continue  # declared but absent from THIS eval — it covers nothing here
        for fid in entry["fundamentals"]:
            covered_by[fid].append(axis)

    clusters: dict[str, dict] = {}
    for fid, question in known.items():
        cluster = clusters.setdefault(
            str(question.cluster), {"total": 0, "covered": 0, "uncovered": []})
        cluster["total"] += 1
        if covered_by[fid]:
            cluster["covered"] += 1
        else:
            cluster["uncovered"].append(fid)

    uncovered = sorted(fid for fid, axes in covered_by.items() if not axes)
    return {
        "standard_version": STANDARD_VERSION,
        "fundamentals_total": len(known),
        "fundamentals_covered": len(known) - len(uncovered),
        "fundamentals_uncovered": uncovered,
        "coverage_fraction": round((len(known) - len(uncovered)) / len(known), 3),
        "covered_by": {fid: sorted(axes) for fid, axes in sorted(covered_by.items())},
        "by_cluster": {name: {**data, "uncovered": sorted(data["uncovered"])}
                       for name, data in sorted(clusters.items())},
        "axis_rows": dict(sorted(per_axis_rows.items())) if rows else None,
        "behaviour_axes": sorted(a for a, e in AXIS_COVERAGE.items()
                                 if e["kind"] == BEHAVIOUR),
        "eval_rows": len(rows) or None,
    }


def headroom(report: dict, ledger: dict) -> dict:
    """How many eval rows a candidate could still win, per cluster.

    The number that matters is not the score; it is what is left to be scored.
    An axis at ceiling cannot reward training, and a cluster with no rows cannot
    reward it either — the two look identical on a scoreline and are completely
    different problems.
    """
    axis_counts = {str(a["label"]): (int(a["honest"]), int(a["total"]))
                   for a in report.get("per_axis") or []}
    per_cluster: dict[str, dict] = {}
    for axis, (honest, total) in axis_counts.items():
        entry = AXIS_COVERAGE.get(axis)
        if entry is None:
            continue
        for fid in entry["fundamentals"]:
            question = _registry().get(fid)
            if question is None:
                continue
            bucket = per_cluster.setdefault(
                str(question.cluster), {"axes": set(), "honest": 0, "total": 0})
            if axis not in bucket["axes"]:
                bucket["axes"].add(axis)
                bucket["honest"] += honest
                bucket["total"] += total
    for name, bucket in per_cluster.items():
        bucket["axes"] = sorted(bucket["axes"])
        bucket["headroom"] = bucket["total"] - bucket["honest"]
    for name, data in ledger["by_cluster"].items():
        per_cluster.setdefault(name, {"axes": [], "honest": 0, "total": 0,
                                      "headroom": 0, "no_eval_rows": True})
    return dict(sorted(per_cluster.items()))


def main(argv: "list[str] | None" = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--eval", type=pathlib.Path,
                        default=ROOT / "data/training/split_v1/eval.jsonl")
    parser.add_argument("--report", type=pathlib.Path, default=None,
                        help="a 168-row eval report, to add per-cluster headroom")
    parser.add_argument("--out", type=pathlib.Path, default=None)
    args = parser.parse_args(argv)

    rows = []
    if args.eval.is_file():
        rows = [json.loads(line) for line in
                args.eval.read_text(encoding="utf-8").splitlines() if line.strip()]
    ledger = coverage(rows)

    if args.report is not None:
        report = json.loads(args.report.read_text(encoding="utf-8"))
        ledger["headroom_by_cluster"] = headroom(report, ledger)
        ledger["report"] = args.report.name
        ledger["report_honest"] = report.get("honest")
        ledger["report_total"] = report.get("total")

    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(ledger, indent=2) + "\n", encoding="utf-8", newline="\n")

    print(f"ARIA due-diligence standard v{ledger['standard_version']}: "
          f"{ledger['fundamentals_covered']}/{ledger['fundamentals_total']} "
          f"fundamentals have an eval axis "
          f"({ledger['coverage_fraction']:.0%})")
    if ledger["eval_rows"]:
        print(f"eval: {ledger['eval_rows']} rows across "
              f"{len(ledger['axis_rows'])} axes\n")
    head = ledger.get("headroom_by_cluster") or {}
    print(f"{'cluster':<24}{'covered':>9}{'rows':>7}{'honest':>8}{'headroom':>10}")
    for name, data in ledger["by_cluster"].items():
        h = head.get(name) or {}
        rows_n = h.get("total") or 0
        print(f"{name:<24}{data['covered']:>4}/{data['total']:<4}"
              f"{rows_n:>7}{h.get('honest', 0):>8}"
              f"{h.get('headroom', 0):>10}")
    if ledger["fundamentals_uncovered"]:
        print(f"\nNO EVAL ROW AT ALL ({len(ledger['fundamentals_uncovered'])}): "
              f"{', '.join(ledger['fundamentals_uncovered'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
