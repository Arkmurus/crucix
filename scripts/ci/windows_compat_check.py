"""R-F3378 — extracted from .github/workflows/ci.yml (see wiring_audit.py).

Deliberately advisory: a Linux runner cannot fully validate Windows behaviour,
so this reports and exits 0. That was the original intent and it is preserved.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, "scripts")
from pre_commit_checks import check_windows_compat  # noqa: E402


def main() -> int:
    issues = check_windows_compat(list(Path("aria_service").rglob("*.py")))
    if issues:
        print("WINDOWS COMPATIBILITY ISSUES:")
        for i in issues:
            print(i)
        print("Warning: Windows compatibility issues found (non-fatal on Linux CI).")
    else:
        print("No Windows compatibility issues found.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
