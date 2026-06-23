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
    targets: list[int] = []  # 1-based def line numbers to decorate
    skipped: list[str] = []
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            import_end_lines.append(node.end_lineno or node.lineno)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not node.name.startswith("_"):
            is_gen = any(isinstance(x, (ast.Yield, ast.YieldFrom)) for x in ast.walk(node))
            already = any(_decorator_name(d) == "fail_wire" for d in node.decorator_list)
            if is_gen:
                skipped.append(f"{node.name} (generator — must be HARD_EXEMPT)")
                continue
            if already:
                skipped.append(f"{node.name} (already wired)")
                continue
            # The node's own first line; decorators (none here) would precede it.
            first_line = node.decorator_list[0].lineno if node.decorator_list else node.lineno
            targets.append(first_line)

    if not targets:
        return {"module": module, "wired": 0, "skipped": skipped, "gap_type": gap_type}

    # Anchor the import to the LAST import that appears BEFORE the first decorated
    # function — NOT the last import in the file. Some modules have late
    # module-level imports after functions (e.g. ocr.py: import sys/threading at
    # L368); anchoring to those would place `fail_wire` after its first use.
    first_target = min(targets)
    imports_before = [el for el in import_end_lines if el < first_target]
    import_anchor = max(imports_before) if imports_before else first_target - 1

    lines = src.splitlines(keepends=True)
    dec_line = f'@fail_wire(module="{module}", gap_type="{gap_type}")\n'
    # Insert decorators bottom-up so earlier indices stay valid. All targets are
    # below import_anchor, so the import insertion index is unaffected.
    for ln in sorted(targets, reverse=True):
        lines.insert(ln - 1, dec_line)
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
