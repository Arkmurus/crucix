"""R-F3378 — extracted from .github/workflows/ci.yml (see wiring_audit.py).

Mirrors CLAUDE.md §11c(a): compile-red is a guaranteed boot failure, so this
one FAILS the build.
"""
from __future__ import annotations

import ast
import os


def main() -> int:
    errors: list[str] = []
    for root, _dirs, files in os.walk("aria_service"):
        if "tests" in root or "__pycache__" in root:
            continue
        for name in files:
            if not name.endswith(".py"):
                continue
            path = os.path.join(root, name)
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    ast.parse(fh.read())
            except SyntaxError as exc:
                errors.append(f"{path}: {exc}")
    if errors:
        for e in errors:
            print(e)
        return 1
    print("All files pass syntax check.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
