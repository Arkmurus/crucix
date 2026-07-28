"""R-F3378 — extracted from .github/workflows/ci.yml.

Lived as a `python -c "..."` block whose body sat at COLUMN 0 inside a YAML
block scalar. Column-0 content terminates the scalar, so the whole workflow
became unparseable (R-F1073, 2026-05-29) and CI failed in 0s on every push for
two months. Indenting it back would fix the YAML and break the Python; a real
file is valid as both, and can be run locally.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, "scripts")
from pre_commit_checks import check_wiring_present  # noqa: E402


def main() -> int:
    issues = check_wiring_present(list(Path("aria_service/intel").rglob("*.py")))
    if issues:
        print("WIRING AUDIT FAILED:")
        for i in issues:
            print(i)
        return 1
    print("All intel modules have brain wiring.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
