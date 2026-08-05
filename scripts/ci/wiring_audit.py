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
import sys
from pathlib import Path

sys.path.insert(0, "scripts")
from pre_commit_checks import check_wiring_present  # noqa: E402

BASELINE = Path("docs/wiring_audit_baseline.json")
ROOT = Path("aria_service")


def _in_scope() -> list[Path]:
    return [p for p in ROOT.rglob("*.py")
            if "tests" not in p.parts and "__pycache__" not in p.parts]


def _key(issue: str) -> str:
    """Stable identity for an issue — the module path, not the issue wording."""
    for tok in issue.replace("\\", "/").split():
        if tok.endswith(".py") or "aria_service/" in tok:
            return tok.strip(":,")
    return issue.strip()


def main() -> int:
    update = "--update-baseline" in sys.argv
    scope = _in_scope()
    issues = check_wiring_present(scope, require_intel=False)

    if update:
        BASELINE.parent.mkdir(parents=True, exist_ok=True)
        BASELINE.write_text(json.dumps({
            "_comment": ("R-F3727 — KNOWN-DARK modules (§21a debt), NOT an "
                         "exemption list. A new dark module must NOT be added "
                         "here to make CI pass; wire it instead. Clearing an "
                         "entry is a PR that makes this file shorter."),
            "known_dark": sorted({_key(i) for i in issues}),
        }, indent=2) + "\n", encoding="utf-8")
        print(f"[wiring] baseline written: {len({_key(i) for i in issues})} "
              f"known-dark module(s) -> {BASELINE}")
        return 0

    known: set[str] = set()
    if BASELINE.exists():
        try:
            known = set(json.loads(BASELINE.read_text(encoding="utf-8"))
                        .get("known_dark", []))
        except Exception as e:      # an unreadable baseline is not an empty one
            print(f"[wiring] COULD NOT READ BASELINE {BASELINE}: {e}", file=sys.stderr)
            return 2

    new = [i for i in issues if _key(i) not in known]
    fixed = known - {_key(i) for i in issues}

    print(f"[wiring] scanned {len(scope)} modules across aria_service/ "
          f"({len(issues)} unwired, {len(known)} already known)")
    if fixed:
        print(f"[wiring] {len(fixed)} module(s) newly WIRED — run "
              f"--update-baseline to shrink the ledger:")
        for f in sorted(fixed)[:10]:
            print(f"    + {f}")
    if new:
        print(f"[wiring] AUDIT FAILED — {len(new)} NEW dark module(s) (§21a):")
        for i in new:
            print(f"  {i}")
        print("\nWire the success AND failure branch to a brain sink (@wired is "
              "preferred — it covers both). Do NOT add it to the baseline to go "
              "green.")
        return 1
    print("[wiring] OK — no NEW dark modules.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
