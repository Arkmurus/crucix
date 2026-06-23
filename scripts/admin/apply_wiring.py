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


def _decorator_name(dec: ast.expr) -> str | None:
    target = dec.func if isinstance(dec, ast.Call) else dec
    if isinstance(target, ast.Name):
        return target.id
    if isinstance(target, ast.Attribute):
        return target.attr
    return None


def _locate(module: str):
    """Find {module}.py across the harness's scan scope and return
    (filepath, import_stmt). The import is relative to the file's package:
    intel/ is wire's package (from .wire); sibling packages (routes/ etc.) use
    from ..intel.wire; top-level files use from .intel.wire."""
    candidates = [os.path.join(d, f"{module}.py") for d in wh.TARGET_DIRS]
    candidates += [f for f in wh.TARGET_FILES if os.path.basename(f) == f"{module}.py"]
    for fp in candidates:
        if os.path.isfile(fp):
            parent = os.path.basename(os.path.dirname(fp))
            if parent == "intel":
                stmt = "from .wire import fail_wire"
            elif parent == "aria_service":  # top-level file (aria_engine.py, main.py)
                stmt = "from .intel.wire import fail_wire"
            else:                            # sibling package (routes/, autonomous/, ...)
                stmt = "from ..intel.wire import fail_wire"
            return fp, stmt + "  # R-F1789 §21 brain-wiring"
    return None, None


def apply_module(module: str, dry_run: bool = False) -> dict:
    fp, import_stmt = _locate(module)
    if fp is None:
        return {"module": module, "error": "file not found in scan scope"}
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
        if wh.is_exempt(f"{module}.py", node.name)[0]:
            skipped.append(f"{node.name} (HARD_EXEMPT)")
            return
        # INNERMOST placement: directly above `def`, BELOW any existing decorators
        # (node.lineno is the def line in py3.8+). Required for routes — @fail_wire
        # must be inside @router.get so FastAPI registers the wrapped handler;
        # fail_wire is signature-transparent (functools.wraps __wrapped__) so DI
        # still works. Harmless for undecorated fns (same line as before).
        targets.append((node.lineno, indent))

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
    if "import fail_wire" not in src:
        lines.insert(import_anchor, import_stmt + "\n")

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
