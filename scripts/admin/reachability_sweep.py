"""R-F2833 — control-gated reachability sweep + AST call-site verifier.

Finds code that is BUILT BUT UNREACHABLE from the application's real entry
points. Read-only: it never edits, deletes or deploys anything.

WHY THIS EXISTS AND WHY IT LOOKS PARANOID
─────────────────────────────────────────
Two earlier attempts produced confident, specific, WRONG numbers:

  v1 counted NAME OCCURRENCES and reported 433 "unwired" functions — including
     live safety gates (`check_cost_cap`) whose callers were in their own
     module. Nobody caught it until two entries looked alarming enough to check
     by hand.
  v2 fixed the graph but only walked FUNCTION BODIES, so anything referenced at
     import time (module scope, class bodies, decorators, defaults) looked dead.
     `parse_xml` — 17 references — was classified UNREACHABLE.

Both are the same failure as R-F2791 (counted templates ENTERED, not SEARCHED)
and R-F2643 (a gate certified by a key nothing writes): a PROXY reported as the
property. That is what fabrication looks like in practice — not invention from
nothing, but a plausible correlate presented as the measurement.

The four rules below exist so this tool cannot repeat that. They are not
ceremony; each one caught a real defect during development.

  1. MEASURE THE PROPERTY. Reachability is a graph question: build a call graph
     with `ast`, root it at real entry points, traverse. Never a name count.

  2. TRI-STATE. `UNDECIDABLE` is mandatory. Dynamic dispatch (getattr, tool
     registries, decorator registration, a name used as a string) is genuinely
     undeterminable statically and is reported as such — never folded into the
     dead bucket. "Could not determine" is not "determined to be dead"; this is
     the same contract R-F2639 set for the phase gates (`pass` may be None).

  3. BIAS TOWARD REACHABLE. Calls resolve by NAME across the whole project,
     which over-connects the graph ON PURPOSE. Over-connecting can only cause a
     false REACHABLE (a missed opportunity). Under-connecting causes a false
     UNREACHABLE — which sends someone to rewire or delete working code. This
     tool must never accuse working code, so UNREACHABLE is a LOWER BOUND.

  4. CONTROLS GATE THE RUN. Known-live and known-dead functions are asserted
     BEFORE any result is printed. One control miss VOIDS the run: it prints the
     failures, withholds the results, and exits non-zero. A sweep that
     misclassifies a control cannot be triaged, only discarded.

AND THE VERIFIER MATTERS AS MUCH AS THE SWEEP. `verify` counts genuine
`ast.Call` sites, because grep counts strings, imports and comments too:
`get_registry_stats` looked like it had 4 callers and all four were log-string
literals. Checking with a proxy just relocates the fabrication risk.

USAGE
─────
  python scripts/admin/reachability_sweep.py sweep            # tri-state report
  python scripts/admin/reachability_sweep.py sweep --sample 10
  python scripts/admin/reachability_sweep.py verify NAME...   # AST call sites

EXIT CODES
  0  ok
  1  RUN VOID (a control was misclassified) — results withheld deliberately
  2  bad usage

READING THE OUTPUT
  A hit is a CANDIDATE, not a finding. Verify each before acting:
      python scripts/admin/reachability_sweep.py verify <name>
  Zero call sites -> unreachable confirmed. Callers present -> check whether the
  CALLERS are themselves reachable; a function with callers is still unreachable
  if every caller is dead (that is correct, not a bug).

  Deliberately-unreachable code exists and must stay that way — e.g.
  `brave_answers.fetch_answer` is an intentional R-F320 removal stub (CLAUDE.md
  §18). Never "fix" a hit without reading why it is there.
"""
from __future__ import annotations

import argparse
import ast
import collections
import pathlib
import random
import re
import sys

# ── repo root discovery (no hardcoded absolute paths) ────────────────────────
def repo_root() -> pathlib.Path:
    p = pathlib.Path(__file__).resolve()
    for parent in p.parents:
        if (parent / "aria_service").is_dir() and (parent / "CLAUDE.md").exists():
            return parent
    # fall back to two levels up (scripts/admin/ -> repo)
    return p.parents[2]


ROOT = repo_root()
PKG = ROOT / "aria_service"

ROUTE_DECORATORS = re.compile(r"\b(router|app)\.(get|post|put|patch|delete|websocket)\b")
REGISTRY_DECORATORS = re.compile(r"\b(register|task|tool|command|handler|schedule|cron|on_)\w*\s*\(")
ENTRY_NAMES = {"main", "lifespan", "startup", "shutdown"}

# Controls — each verified BY HAND. Changing these changes what the gate means,
# so treat them as a test fixture, not as configuration.
CONTROL_LIVE = ["check_cost_cap", "verify_deploy_landed", "apply_capability_test_gate",
                "get_officers", "get_psc", "assess"]
CONTROL_DEAD = ["psc_reverse_lookup"]


def _rel(p: pathlib.Path) -> str:
    """Path relative to the repo when possible, absolute otherwise.

    `relative_to` RAISES for anything outside ROOT, which crashed `verify` when
    pointed at a directory outside the checkout (found by the R-F2833 tests).
    A reporting helper must never be the thing that aborts the run.
    """
    try:
        return p.relative_to(ROOT).as_posix()
    except ValueError:
        return p.as_posix()


def py_files(pkg: pathlib.Path):
    for p in pkg.rglob("*.py"):
        s = p.as_posix()
        if "__pycache__" in s or "/tests/" in s or p.name.startswith("test_"):
            continue
        yield p


def _names_in(node) -> set[str]:
    got: set[str] = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Name):
            got.add(n.id)
        elif isinstance(n, ast.Attribute):
            got.add(n.attr)
    return got


def decorator_src(node) -> str:
    out = []
    for d in getattr(node, "decorator_list", []):
        try:
            out.append(ast.unparse(d))
        except Exception:
            pass
    return " ".join(out)


def module_scope_refs(tree) -> set[str]:
    """Names referenced OUTSIDE any function body.

    Module-level statements, class bodies, decorators and default arguments all
    execute at import time, so whatever they reference is genuinely entered.
    Walking only function bodies is what made v2 misclassify `parse_xml`.

    Implemented by walking and SKIPPING function bodies rather than by listing
    the node types that count — a denylist would silently miss a construct
    nobody thought of, which is the failure mode this tool exists to avoid.
    """
    out: set[str] = set()

    def visit(node, in_func: bool):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for d in child.decorator_list:
                    out.update(_names_in(d))
                for d in child.args.defaults + [x for x in child.args.kw_defaults if x]:
                    out.update(_names_in(d))
                visit(child, True)
            else:
                if not in_func:
                    out.update(_names_in(child))
                visit(child, in_func)

    visit(tree, False)
    return out


def call_edges(node) -> set[str]:
    """Every name this function could be reaching (permissive — rule 3)."""
    out: set[str] = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Call):
            f = n.func
            if isinstance(f, ast.Name):
                out.add(f.id)
            elif isinstance(f, ast.Attribute):
                out.add(f.attr)
        elif isinstance(n, ast.Name):
            out.add(n.id)          # callback passed by name
        elif isinstance(n, ast.Attribute):
            out.add(n.attr)
    return out


def analyse(pkg: pathlib.Path) -> dict:
    """Return the tri-state classification. Pure — no printing, so it is testable."""
    all_defs: dict[str, list] = collections.defaultdict(list)
    graph: dict[str, set[str]] = collections.defaultdict(set)
    roots: set[str] = set()
    dynamic: set[str] = set()
    strings: set[str] = set()
    getattr_used = False

    for p in py_files(pkg):
        try:
            tree = ast.parse(p.read_text(encoding="utf-8", errors="ignore"))
        except SyntaxError:
            continue

        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                strings.add(node.value)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                    and node.func.id == "getattr":
                getattr_used = True
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                all_defs[node.name].append(
                    (p, node.lineno, isinstance(node, ast.AsyncFunctionDef)))
                graph[node.name] |= call_edges(node)
                dsrc = decorator_src(node)
                if ROUTE_DECORATORS.search(dsrc) or node.name in ENTRY_NAMES:
                    roots.add(node.name)
                elif dsrc and REGISTRY_DECORATORS.search(dsrc):
                    roots.add(node.name)
                    dynamic.add(node.name)

        roots |= module_scope_refs(tree)

    roots &= set(all_defs) | roots        # keep only names we know about, plus entries
    undecidable = {n for n in all_defs if n in strings} | (dynamic & set(all_defs))

    reachable: set[str] = set()
    stack = list((roots & set(all_defs)) | undecidable)
    while stack:
        cur = stack.pop()
        if cur in reachable:
            continue
        reachable.add(cur)
        stack.extend(graph.get(cur, set()) - reachable)

    return {"all_defs": all_defs, "reachable": reachable,
            "undecidable": undecidable, "getattr_used": getattr_used}


def check_controls(res: dict, live=CONTROL_LIVE, dead=CONTROL_DEAD) -> list[str]:
    """Return control failures. EMPTY list means the run may be reported."""
    all_defs, reachable, undec = res["all_defs"], res["reachable"], res["undecidable"]
    fails = []
    for c in live:
        if c not in all_defs:
            fails.append(f"control LIVE {c!r} not present in the tree at all")
        elif c not in reachable:
            fails.append(f"control LIVE {c!r} classified UNREACHABLE but it IS called")
    for c in dead:
        if c not in all_defs:
            fails.append(f"control DEAD {c!r} not present in the tree at all")
        elif c in reachable and c not in undec:
            fails.append(f"control DEAD {c!r} classified REACHABLE but it is hand-verified dead")
    return fails


def classify(res: dict) -> list[tuple]:
    rows = []
    for name, sites in res["all_defs"].items():
        if name.startswith("_"):
            continue
        p, ln, isa = sites[0]
        if name in res["undecidable"]:
            state = "UNDECIDABLE"
        elif name in res["reachable"]:
            state = "reachable"
        else:
            state = "UNREACHABLE"
        rows.append((state, _rel(p), ln, name))
    return rows


def call_sites(pkg: pathlib.Path, targets: list[str]) -> dict:
    """Genuine ast.Call sites per target, with the ENCLOSING function.

    The enclosing function matters: reachability semantics mean a function with
    callers is STILL unreachable if every caller is itself dead.
    """
    found = collections.defaultdict(list)
    tset = set(targets)
    for p in py_files(pkg):
        try:
            tree = ast.parse(p.read_text(encoding="utf-8", errors="ignore"))
        except SyntaxError:
            continue
        owner = {}

        def walk(node, cur):
            for ch in ast.iter_child_nodes(node):
                nxt = ch.name if isinstance(ch, (ast.FunctionDef, ast.AsyncFunctionDef)) else cur
                owner[id(ch)] = nxt
                walk(ch, nxt)

        walk(tree, "<module>")
        for n in ast.walk(tree):
            if isinstance(n, ast.Call):
                f = n.func
                nm = f.id if isinstance(f, ast.Name) else (
                    f.attr if isinstance(f, ast.Attribute) else None)
                if nm in tset:
                    found[nm].append((_rel(p), n.lineno,
                                      owner.get(id(n), "?")))
    return found


def cmd_sweep(args) -> int:
    res = analyse(PKG)
    fails = check_controls(res)

    print("=" * 74)
    print("CONTROL GATE")
    print("=" * 74)
    for c in CONTROL_LIVE:
        ok = c in res["reachable"]
        print(f"  LIVE {c:30} {'reachable OK' if ok else 'UNREACHABLE  <-- MISS'}")
    for c in CONTROL_DEAD:
        st = ("undecidable" if c in res["undecidable"]
              else "reachable  <-- MISS" if c in res["reachable"] else "unreachable OK")
        print(f"  DEAD {c:30} {st}")
    print()

    if fails:
        print("!! RUN VOID — control(s) misclassified. Results withheld:")
        for f in fails:
            print("   -", f)
        print("\nA sweep that misclassifies a control cannot be triaged, only discarded.")
        return 1

    print("all controls passed — results below are reportable\n")
    rows = classify(res)
    counts = collections.Counter(r[0] for r in rows)
    total = len(rows) or 1
    print("=" * 74)
    print(f"TRI-STATE  (public module-level functions: {len(rows)})")
    print("=" * 74)
    for k in ("reachable", "UNDECIDABLE", "UNREACHABLE"):
        print(f"  {k:12} {counts[k]:5}  ({100*counts[k]/total:.1f}%)")
    print(f"\n  getattr() present in tree: {res['getattr_used']} -> dynamic dispatch is "
          f"possible, hence UNDECIDABLE is not an empty category")
    print("  NOTE: UNREACHABLE is a LOWER BOUND (calls resolve permissively by name).")
    print("        UNDECIDABLE is NOT 'unused' — it is 'cannot be determined statically'.")

    unreach = sorted(r for r in rows if r[0] == "UNREACHABLE")
    byfile = collections.defaultdict(list)
    for _, f, ln, name in unreach:
        byfile[f].append((ln, name))
    print(f"\n=== UNREACHABLE ({len(unreach)}) — CANDIDATES, verify each before acting ===")
    for f in sorted(byfile, key=lambda k: -len(byfile[k]))[:args.files]:
        print(f"  {f}  ({len(byfile[f])})")
        for ln, name in sorted(byfile[f])[:6]:
            print(f"      :{ln:<5} {name}")

    if unreach and args.sample:
        rnd = random.Random(args.seed)
        sample = rnd.sample(unreach, min(args.sample, len(unreach)))
        print(f"\n=== VERIFY THESE {len(sample)} BEFORE QUOTING THE AGGREGATE ===")
        names = " ".join(s[3] for s in sample)
        print(f"  python scripts/admin/reachability_sweep.py verify {names}")
    return 0


def cmd_verify(args) -> int:
    found = call_sites(PKG, args.names)
    print(f"{'function':32} {'ast.Call sites':>15}   verdict")
    print("-" * 78)
    need = 0
    for t in args.names:
        hits = found.get(t, [])
        if not hits:
            print(f"{t:32} {0:>15}   UNREACHABLE confirmed")
        else:
            print(f"{t:32} {len(hits):>15}   <-- HAS CALLERS, check their reachability")
            for f, ln, own in hits[:5]:
                print(f"{'':32} {'':15}     {f}:{ln} in {own}()")
            need += 1
    print("-" * 78)
    print(f"{len(args.names)-need}/{len(args.names)} confirmed unreachable; "
          f"{need} need caller-reachability review")
    if need:
        print("\nA function with callers is STILL unreachable if every caller is dead —")
        print("re-run `verify` on the calling functions to settle it.")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("sweep", help="tri-state reachability report")
    s.add_argument("--sample", type=int, default=10, help="hits to nominate for verification")
    s.add_argument("--seed", type=int, default=0, help="sample seed (vary it between runs)")
    s.add_argument("--files", type=int, default=15, help="files to list")
    s.set_defaults(fn=cmd_sweep)

    v = sub.add_parser("verify", help="count genuine ast.Call sites for names")
    v.add_argument("names", nargs="+")
    v.set_defaults(fn=cmd_verify)

    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
