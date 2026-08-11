"""R-F3900 — search_engine_health shipped DARK on its success branch.

The module carried `wire_failure` on every error path and NOTHING on any success
path. So the brain could see the health tracker BREAK but never see it WORK — and
"no failure signal" is not evidence of health (§1, §22). A module that only ever
reports its own errors is indistinguishable from one that never ran.

CAUGHT BY CI, NOT BY THE HOOK, and the asymmetry is the point: the pre-commit hook
scans only STAGED files, and this module was committed before the hook was activated
(R-F3885/R-F3896). The identical defect in `brave_usage.py` was caught by the hook.
Two enforcement points covering for each other is the design working.
"""
from __future__ import annotations

import subprocess
import sys

from aria_service.tests._source_probe import module_source, repo_path
from aria_service.intel import search_engine_health as seh


def test_the_module_wires_its_success_branch():
    src = module_source(seh)
    assert "@wired(" in src, (
        "search_engine_health must wire SUCCESS as well as failure (§21a). "
        "`@wired` is preferred over a bare wire_success call because it covers "
        "both branches by construction.")


def test_the_repo_wide_wiring_audit_sees_no_new_dark_module():
    """CAPABILITY TEST — drives the actual CI gate that failed (§3c/§23), not a
    proxy. Bounded well under the per-test budget (R-F3459)."""
    proc = subprocess.run(
        [sys.executable, str(repo_path("scripts/ci/wiring_audit.py"))],
        capture_output=True, text=True, timeout=90,
        cwd=str(repo_path(".")), encoding="utf-8", errors="replace",
    )
    blob = (proc.stdout or "") + (proc.stderr or "")
    assert "NEW dark module" not in blob or "no NEW dark modules" in blob, blob[-800:]
    assert proc.returncode == 0, f"wiring audit failed:\n{blob[-800:]}"
