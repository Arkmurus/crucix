"""R-F3573 — the dead-code gate reported 0 on a tree with 61 orphan modules.

`scripts/ecosystem_audit.py::check_dead_code` printed "Dead modules: 0" while the
import graph showed 17 modules imported by NOTHING and 44 imported only by the
test suite. Two compounding causes, and the second was introduced as a fix:

  1. SUBSTRING, NOT IMPORT. `if mod_name not in all_source` counts a name in a
     comment, a docstring, a log line or a registry key. behavioural_anomaly,
     quarantine_network and credential_self_destruct — three SECURITY subsystems
     with no production caller — read as live because `wiring_harness.py` lists
     them in a gap_type registry and `capability_gaps.py` names one in a comment.

  2. TESTS COUNTED AS PRODUCTION. R-F3381 added the test corpus to the haystack
     because a scoring harness used only by tests was being reported dead. That
     was right as far as it went and collapsed two different states into one: a
     test-only HARNESS is fine, a test-only ENGINE is dead code that happens to
     own a test, and afterwards the gate could not tell them apart.

Found by checking whether the success branches wired in R-F3567 were REACHABLE
rather than assuming it. `audit_trail.py` was wired by R-F3563 and its only
apparent referrer is `case.audit_trail.append(...)` — a dict FIELD, not the
module.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest


_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "scripts"))

from ecosystem_audit import (  # noqa: E402
    ORPHAN_BASELINE_NEVER,
    ORPHAN_BASELINE_TEST_ONLY,
    _imported_stems,
    check_orphan_modules,
    scan_modules,
)


def test_an_import_is_detected_but_a_mention_is_not(tmp_path):
    """The core correction: a name in a comment is not a reference."""
    real = tmp_path / "real.py"
    real.write_text(
        "from .engine_wiring import wire_success\n"
        "import json\n"
        "from ..intel import country_taxonomy as ct\n",
        encoding="utf-8",
    )
    assert {"engine_wiring", "wire_success", "json", "country_taxonomy"} <= _imported_stems(real)

    mention = tmp_path / "mention.py"
    mention.write_text(
        '# quarantine_network handles this case\n'
        'GAP_TYPES = {"behavioural_anomaly": "security_threat"}\n'
        'log.info("credential_self_destruct fired")\n',
        encoding="utf-8",
    )
    stems = _imported_stems(mention)
    for name in ("quarantine_network", "behavioural_anomaly", "credential_self_destruct"):
        assert name not in stems, (
            f"{name} counted as imported from a comment/string/registry key — "
            f"this is exactly how three security subsystems read as live"
        )


def test_a_dynamic_import_by_literal_still_counts(tmp_path):
    """importlib.import_module('pkg.mod') is a real, resolvable import; missing it
    would manufacture orphans that are genuinely reached."""
    f = tmp_path / "dyn.py"
    f.write_text(
        "import importlib\n"
        "mod = importlib.import_module('aria_service.intel.researcher')\n",
        encoding="utf-8",
    )
    assert "researcher" in _imported_stems(f)


def test_the_baseline_matches_the_live_tree_exactly():
    """Anti-rot in both directions.

    A NEW orphan must fail the build, and an entry that is no longer orphaned
    must be DELETED — otherwise the list decays into a permanent excuse, which is
    the failure mode every allowlist in this repo is written against.
    """
    result = check_orphan_modules(scan_modules())
    live_never = set(result["never_imported"])
    live_test_only = set(result["test_only"])

    newly_orphaned = sorted(
        (live_never - ORPHAN_BASELINE_NEVER) | (live_test_only - ORPHAN_BASELINE_TEST_ONLY)
    )
    assert not newly_orphaned, (
        f"{len(newly_orphaned)} module(s) are newly orphaned: {newly_orphaned}. "
        f"Nothing imports them. Wire each into a caller, or — if it is an entry "
        f"point or a deliberate harness — add it to ORPHAN_BASELINE_* with its reason."
    )

    no_longer_orphaned = sorted(
        (ORPHAN_BASELINE_NEVER | ORPHAN_BASELINE_TEST_ONLY) - live_never - live_test_only
    )
    assert not no_longer_orphaned, (
        f"{no_longer_orphaned} are no longer orphaned — REMOVE them from the "
        f"baseline. A pinned list that outlives the debt it records is a lie."
    )


def test_the_baselines_may_only_shrink():
    """Hard caps at the measured 2026-07-31 figures. Raising either is the clamp
    pattern; the only legitimate direction is down."""
    assert len(ORPHAN_BASELINE_NEVER) <= 17, (
        f"ORPHAN_BASELINE_NEVER grew to {len(ORPHAN_BASELINE_NEVER)} (cap 17)"
    )
    assert len(ORPHAN_BASELINE_TEST_ONLY) <= 44, (
        f"ORPHAN_BASELINE_TEST_ONLY grew to {len(ORPHAN_BASELINE_TEST_ONLY)} (cap 44)"
    )


def test_the_baseline_is_stored_platform_independently():
    """`str(Path)` yields backslashes on Windows and forward slashes on the Linux
    CI runner. A baseline pinned from a dev machine would match NOTHING in CI and
    report all 61 entries as newly orphaned — a gate that fails 100% of the time
    on the only platform that runs it."""
    for entry in ORPHAN_BASELINE_NEVER | ORPHAN_BASELINE_TEST_ONLY:
        assert "\\" not in entry, f"{entry!r} is a Windows path; the baseline must be posix"
    result = check_orphan_modules(scan_modules())
    for entry in result["never_imported"] + result["test_only"]:
        assert "\\" not in entry, f"{entry!r} — check_orphan_modules must emit posix paths"


def test_the_scan_is_not_vacuous():
    """A detector that finds nothing passes everything. It must still see the
    known orphans, and must still see the tree."""
    modules = scan_modules()
    assert len(modules) > 400, f"only {len(modules)} modules scanned"

    result = check_orphan_modules(modules)
    total = len(result["never_imported"]) + len(result["test_only"])
    assert total > 0, (
        "the orphan scan reports zero — that is the state check_dead_code was in, "
        "and it was wrong. Verify the detector before believing a clean result."
    )
    assert "intel/audit_trail.py" in result["never_imported"], (
        "audit_trail is no longer detected as an orphan; if it was genuinely "
        "wired into a caller, remove it from the baseline instead"
    )


def test_entry_points_are_not_reported_as_orphans():
    """cli/ and static/ modules are EXECUTED, never imported. Absence of an
    importer is their normal state, so reporting them would be noise that buries
    the real findings."""
    result = check_orphan_modules(scan_modules())
    for entry in result["never_imported"] + result["test_only"]:
        assert not entry.startswith(("cli/", "static/", "scripts/")), (
            f"{entry} is an entry point and should not be reported as an orphan"
        )
