"""R-F3902 — c_number_registry read as newly ORPHANED; it is a CLI-backed tool.

Its only in-tree importer is its own test, because the real entry point is
`scripts/admin/reserve_c_number.py` (plus the CI defect-register gate) and `scripts/`
is outside the audit's scan boundary. That is exactly the "entry point or deliberate
harness" case R-F3573's own failure message names as the reason to record it.

RECORDED, NOT SILENCED. It stays subject to every other gate: it wires success AND
failure to the brain (§21a) and is registered in MODULE_GAP_TYPES (R-F3901). Per
R-F3573's anti-rot-in-both-directions rule, if a production caller ever imports it
the baseline entry must come back out.
"""
from __future__ import annotations

import sys

from aria_service.tests._source_probe import repo_path

sys.path.insert(0, str(repo_path("scripts")))
from ecosystem_audit import (  # noqa: E402
    ORPHAN_BASELINE_TEST_ONLY,
    check_orphan_modules,
    scan_modules,
)


def test_it_is_recorded_in_the_test_only_bucket_with_a_reason():
    assert "intel/c_number_registry.py" in ORPHAN_BASELINE_TEST_ONLY
    src = repo_path("scripts/ecosystem_audit.py").read_text(encoding="utf-8")
    assert "R-F3902" in src, "a baseline entry without its reason is an excuse"


def test_the_orphan_gate_is_green():
    """CAPABILITY TEST — the gate that failed, run for real."""
    result = check_orphan_modules(scan_modules())
    newly = set(result["test_only"]) - ORPHAN_BASELINE_TEST_ONLY
    assert not newly, f"newly orphaned: {sorted(newly)}"


def test_the_module_is_not_exempt_from_wiring():
    """Baselining an ORPHAN must not become a general exemption."""
    from aria_service.intel import c_number_registry as reg
    from aria_service.tests._source_probe import module_source

    src = module_source(reg)
    assert "wire_success" in src and "wire_failure" in src, (
        "an orphan-baselined module is still bound by §21a")
