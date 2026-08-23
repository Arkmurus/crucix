"""R-F3378 — extracted from .github/workflows/ci.yml.

Lived as a `python -c "..."` block whose body sat at COLUMN 0 inside a YAML
block scalar. Column-0 content terminates the scalar, so the whole workflow
became unparseable (R-F1073, 2026-05-29) and CI failed in 0s on every push for
two months. Indenting it back would fix the YAML and break the Python; a real
file is valid as both, and can be run locally.

R-F3727 — THE AUDIT NOW COVERS ALL OF aria_service, NOT JUST intel/.

§21b is explicit: "no new module, engine, route, guard, or feature ships dark."
Enforcement only ever scanned `aria_service/intel`, so outside that directory the
rule was unenforced — while CI printed "All intel modules have brain wiring",
which is true and reads as "everything is wired".

Measured 2026-08-05: 33 modules outside intel/ reach NO brain sink at all and
swallow at least one failure — 7 in metacognitive/, 6 in learning/, 4 each in
guardian/ and cli/, 2 each in search_index/ utils/ vetting/ static/, one each in
crawler/ intent/ integrations/ env_bootstrap. metacognitive and learning are core
cognition, not peripheral tooling.

WHY A BASELINE AND NOT A BIG-BANG FIX. Failing CI on 33 pre-existing modules
breaks every build on arrival, and a gate that fails on day one gets switched
off — after which it protects nothing (same reasoning as the R-F3720 secret
gate). The baseline records the KNOWN dark set so that:

  - no NEW dark module can ship — that is the class this closes;
  - the debt is explicit and reviewable in git rather than invisible;
  - clearing an entry is a normal PR that makes the file shorter.

The baseline is a LEDGER OF DEBT, not an exemption list. Do not add to it to make
a build pass — wire the module instead. `docs/wiring_backlog_2026_07_28.md`
remains the per-module plan. There is still exactly ONE definition of "wired"
(check_wiring_present); this widens SCOPE, never the vocabulary.
"""
from __future__ import annotations

import json
import time
import sys
from pathlib import Path

sys.path.insert(0, "scripts")
from pre_commit_checks import check_wiring_present  # noqa: E402

BASELINE = Path("docs/wiring_audit_baseline.json")
ROOT = Path("aria_service")


def _in_scope() -> list[Path]:
    return [p for p in ROOT.rglob("*.py")
            if "tests" not in p.parts and "__pycache__" not in p.parts]


def _verdict(issue: str) -> str:
    """The CATEGORY of the finding, from its first line.

    R-F3728 — keying on the whole message meant any reword invalidated every
    entry at once, turning the gate into 66 spurious "NEW dark module" failures
    and training people to re-baseline instead of read it. Keying on the
    category keeps that stable while still noticing a module that changes
    KIND — e.g. from fully dark to half-wired, which is progress that should be
    re-recorded rather than silently accepted.
    """
    head = issue.strip().splitlines()[0] if issue.strip() else ""
    low = head.lower()
    if "no brain wiring" in low:
        return "no-wiring"
    if "no wire_failure" in low:
        return "missing-failure"
    if "no wire_success" in low:
        return "missing-success"
    return "other"


def _scan() -> dict[str, str]:
    """{posix path: verdict} — one entry per unwired module.

    R-F3728 — scans PER FILE. check_wiring_present() emits the module's BASENAME
    only ("git_utils.py: NO brain wiring found"), so a key parsed out of the
    message is (a) ambiguous — this tree has same-named modules in different
    packages, and two `db.py` would share one baseline entry, hiding whichever
    was added second — and (b) tied to the message wording. Calling the checker
    with a single file makes the path unambiguous because we already know it.
    """
    found: dict[str, str] = {}
    for p in _in_scope():
        for issue in check_wiring_present([p], require_intel=False):
            found[p.as_posix()] = _verdict(issue)
    return found


def main() -> int:
    update = "--update-baseline" in sys.argv
    found = _scan()

    if update:
        BASELINE.parent.mkdir(parents=True, exist_ok=True)
        # R-F4263 (dossier E9) — STAMP THE DATE. The ledger carried no
        # `recorded_at` at all, so 63 dark modules were indistinguishable from a
        # decision nobody remembers making: nothing recorded how old the debt
        # was, and a ledger of debt that cannot be aged is a ledger nobody pays
        # down. `module_count` is written alongside so a drifted file (66
        # entries against 63 actual, which is what E9 measured) is visible
        # without re-running the scan.
        BASELINE.write_text(json.dumps({
            "_comment": ("R-F3727/R-F3728 — KNOWN-DARK modules (§21a debt), NOT "
                         "an exemption list. A new dark module must NOT be added "
                         "here to make CI pass; wire it instead. Clearing an "
                         "entry is a PR that makes this file shorter. Keyed by "
                         "module PATH -> verdict category. R-F4263: "
                         "`recorded_at` dates the debt — if it is far in the "
                         "past, that is the finding, not a formatting detail."),
            "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "module_count": len(found),
            "known_dark": dict(sorted(found.items())),
        }, indent=2) + "\n", encoding="utf-8")
        print(f"[wiring] baseline written: {len(found)} known-dark module(s) "
              f"-> {BASELINE}")
        return 0

    known: dict[str, str] = {}
    if BASELINE.exists():
        try:
            raw = json.loads(BASELINE.read_text(encoding="utf-8")).get("known_dark", {})
            # tolerate the R-F3727 list form so a stale baseline is not silently empty
            known = raw if isinstance(raw, dict) else {k: "other" for k in raw}
        except Exception as e:      # an unreadable baseline is not an empty one
            print(f"[wiring] COULD NOT READ BASELINE {BASELINE}: {e}", file=sys.stderr)
            return 2

    new = [(p, v) for p, v in sorted(found.items()) if p not in known]
    changed = [(p, known[p], v) for p, v in sorted(found.items())
               if p in known and known[p] != v]
    fixed = set(known) - set(found)

    print(f"[wiring] scanned {len(_in_scope())} modules across aria_service/ "
          f"({len(found)} unwired, {len(known)} already known)")
    if fixed:
        print(f"[wiring] {len(fixed)} module(s) newly WIRED — run "
              f"--update-baseline to shrink the ledger:")
        for f in sorted(fixed)[:10]:
            print(f"    + {f}")
    if changed:
        print(f"[wiring] {len(changed)} module(s) changed CATEGORY "
              f"(re-baseline to record):")
        for p, was, now in changed[:10]:
            print(f"    ~ {p}: {was} -> {now}")
    if new:
        print(f"[wiring] AUDIT FAILED — {len(new)} NEW dark module(s) (§21a):")
        for p, v in new:
            print(f"  {p}: {v}")
        print("\nWire the success AND failure branch to a brain sink (@wired is "
              "preferred — it covers both). Do NOT add it to the baseline to go "
              "green.")
        return 1
    print("[wiring] OK — no NEW dark modules.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
