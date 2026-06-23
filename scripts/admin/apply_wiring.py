#!/usr/bin/env python3
"""R-F1789 — §21 wiring applicator (Phase-1 engine).

Mechanically adds `@fail_wire(module=..., gap_type=...)` to the MODULE-LEVEL
public functions of the given intel modules, using the Claude-approved gap_type
from wiring_harness.MODULE_GAP_TYPES.

SAFETY (only touches what the established pattern proved safe):
  - MODULE-LEVEL functions only — never class methods, never nested closures
    (those are different jobs; see the backfill plan's tier 2/3).
  - SKIPS generators (the decoration-time guard would raise) and functions that
    already carry a @fail_wire decorator (idempotent).
  - Inserts the import once, after the last top-level import.
  - Does NOT add modules to WIRED_MODULES — that is an explicit, reviewed edit
    in wiring_harness.py so GATE A coverage enforcement is intentional.

Usage:
    python scripts/admin/apply_wiring.py mod1 mod2 ...
    python scripts/admin/apply_wiring.py --dry-run mod1   # print, don't write

After running: re-run the gates, py_compile, import smoke, and a capability
test before committing. Review the diff.
"""
from __future__ import annotations

import ast
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from aria_service.intel import wiring_harness as wh  # noqa: E402

IMPORT_STMT = "from .wire import fail_wire  # R-F1789 §21 brain-wiring"


def _decorator_name(dec: ast.expr) -> str | None:
    target = dec.func if isinstance(dec, ast.Call) else dec
    if isinstance(target, ast.Name):
        return target.id
    if isinstance(target, ast.Attribute):
        return target.attr
    return None


def apply_module(module: str, dry_run: bool = False) -> dict:
    fp = os.path.join("aria_service", "intel", f"{module}.py")
    if not os.path.isfile(fp):
        return {"module": module, "error": "file not found"}
    with open(fp, encoding="utf-8") as f:
        src = f.read()
    tree = ast.parse(src)
    gap_type = wh.get_gap_type(module)

    import_end_lines: list[int] = []  # end line of every top-level import
    targets: list[tuple[int, str]] = []  # (1-based line to insert before, indent)
    skipped: list[str] = []

    # Decorators that mean "do NOT wrap" — wrapping a property/descriptor changes
    # its semantics; these are trivial accessors, not failure paths (reasoned
    # exemption per §21a). staticmethod/classmethod would also need ordering care.
    NON_WIRE_DECORATORS = {"property", "cached_property", "staticmethod", "classmethod"}

    def consider(node, indent: str) -> None:
        if node.name.startswith("_"):
            return  # private/dunder
        is_gen = any(isinstance(x, (ast.Yield, ast.YieldFrom)) for x in ast.walk(node))
        if is_gen:
            skipped.append(f"{node.name} (generator — must be HARD_EXEMPT)")
            return
        decnames = {_decorator_name(d) for d in node.decorator_list}
        if "fail_wire" in decnames:
            skipped.append(f"{node.name} (already wired)")
            return
        bad = decnames & NON_WIRE_DECORATORS
        if bad:
            skipped.append(f"{node.name} ({'/'.join(sorted(bad))} — exempt, not wired)")
            return
        first_line = node.decorator_list[0].lineno if node.decorator_list else node.lineno
        targets.append((first_line, indent))

    def walk_classes(body) -> None:
        for n in body:
            if isinstance(n, ast.ClassDef):
                for c in n.body:
                    if isinstance(c, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        consider(c, " " * c.col_offset)  # indent matches the method
                walk_classes(n.body)  # nested classes

    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            import_end_lines.append(node.end_lineno or node.lineno)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            consider(node, "")  # module-level — NOT nested closures
    walk_classes(tree.body)

    if not targets:
        return {"module": module, "wired": 0, "skipped": skipped, "gap_type": gap_type}

    # Anchor the import to the LAST import that appears BEFORE the first decorated
    # function — NOT the last import in the file. Some modules have late
    # module-level imports after functions (e.g. ocr.py: import sys/threading at
    # L368); anchoring to those would place `fail_wire` after its first use.
    first_target = min(t[0] for t in targets)
    imports_before = [el for el in import_end_lines if el < first_target]
    import_anchor = max(imports_before) if imports_before else first_target - 1

    lines = src.splitlines(keepends=True)
    # Insert decorators bottom-up so earlier indices stay valid. All targets are
    # below import_anchor, so the import insertion index is unaffected.
    for ln, indent in sorted(targets, key=lambda t: t[0], reverse=True):
        lines.insert(ln - 1, f'{indent}@fail_wire(module="{module}", gap_type="{gap_type}")\n')
    if IMPORT_STMT not in src and "from .wire import fail_wire" not in src:
        lines.insert(import_anchor, IMPORT_STMT + "\n")

    new_src = "".join(lines)
    # Validate the result parses before writing.
    ast.parse(new_src)
    if not dry_run:
        with open(fp, "w", encoding="utf-8", newline="") as f:
            f.write(new_src)
    return {"module": module, "wired": len(targets), "skipped": skipped, "gap_type": gap_type}


def main(argv: list[str]) -> int:
    dry = "--dry-run" in argv
    mods = [a for a in argv if not a.startswith("-")]
    if not mods:
        print(__doc__)
        return 2
    total = 0
    for m in mods:
        r = apply_module(m, dry_run=dry)
        if r.get("error"):
            print(f"  !! {m}: {r['error']}")
            continue
        total += r["wired"]
        print(f"  {m}: wired {r['wired']} fn(s) [{r['gap_type']}]"
              + (f"  skipped: {r['skipped']}" if r["skipped"] else ""))
    print(f"\n{'DRY-RUN — ' if dry else ''}total functions wired: {total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
