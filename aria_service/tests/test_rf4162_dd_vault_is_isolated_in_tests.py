"""R-F4162 (C-181) — the DD vault must never be the machine's, only the run's.

`dd_vault` resolves its database to `${ARIA_DATA_DIR:-/data}/dd_vault.db`. On a
dev box that is `C:\\data\\dd_vault.db` — OUTSIDE the repo, shared by every test
run on the machine, and never cleaned.

`dd_orchestrator.list_reports` reconciles against that vault on EVERY read
(R-F1973 / R-F2485 / R-F2652), so any DD list test silently merges whatever
earlier runs left behind. Measured 2026-08-18: six residual fixture rows
(`Risky Business SARL`, `dd_test_red`, `dd_test_green`, ...) turned
`test_rf2407_dd_rerun_unnamed` red on this box while it stayed green in CI — and
the §16 baseline could not see it, because the baseline machine's copy happened
to be empty.

**A suite whose verdict depends on the machine's history is not a suite.** C-179
fixed that one test by stubbing `get_vault`; the session fixture in `conftest.py`
is the class fix, in the spirit of the R-F3449 note there ("fixed at the class
rather than per test").

These tests assert the fixture's PROPERTIES rather than trusting a green suite
diff — a diff shows nothing broke, not that anything is actually isolated.
"""
from __future__ import annotations

import pathlib

from aria_service.intel import dd_vault


def _default_machine_path() -> pathlib.Path:
    """Where dd_vault WOULD resolve without the fixture."""
    import os
    return pathlib.Path(os.getenv("ARIA_DATA_DIR") or "/data") / "dd_vault.db"


def test_the_vault_path_is_not_the_machine_global_one():
    """The whole point. If this resolves back to the machine path, the suite is
    reading and writing the operator's real vault again.

    Asserted as INEQUALITY WITH THE DEFAULT rather than by looking for "tmp" in
    the string: a path-substring check passes for any directory that happens to
    be named tmp-something, and fails for a perfectly good isolated path that is
    not. The property that matters is "not the shared one"."""
    resolved = pathlib.Path(dd_vault._VAULT_DB).resolve()
    assert resolved.name == "dd_vault.db"
    assert resolved != _default_machine_path().resolve(), (
        f"the DD vault resolves to the machine-global {resolved} — this run is "
        "reading and writing a database shared with every other run on the box")


def test_the_singleton_was_rebuilt_against_the_isolated_path():
    """`get_vault()` caches `_vault_instance`, so repointing `_VAULT_DB` alone
    would hand back a handle to the OLD database — isolation in name only."""
    v = dd_vault.get_vault()
    used = pathlib.Path(getattr(v, "_db_path")).resolve()
    assert used == pathlib.Path(dd_vault._VAULT_DB).resolve(), (
        f"the cached vault still points at {used}")


def test_the_machine_global_vault_is_never_written_by_the_suite():
    """The goal, stated directly and provably.

    An earlier version of this test asserted the isolated vault "starts empty of
    foreign rows" by looking for names like `Risky Business SARL`. **That test
    was wrong and the full-suite diff caught it**: the fixture is SESSION-scoped,
    so other DD tests legitimately write those very fixture names into the
    isolated database during the run. It passed alone and failed in-suite —
    asserting something isolation never promised.

    What isolation actually promises is that the MACHINE-GLOBAL file is not the
    one in play. That is what this checks, and it cannot be satisfied by
    accident."""
    in_use = pathlib.Path(dd_vault._VAULT_DB).resolve()
    machine = _default_machine_path().resolve()
    assert in_use != machine, f"the suite is using the machine-global vault at {machine}"

    v = dd_vault.get_vault()
    assert pathlib.Path(getattr(v, "_db_path")).resolve() != machine, (
        "the cached vault handle still points at the machine-global database")


def test_writes_land_in_the_isolated_file_and_are_readable():
    """Isolation must not mean 'broken'. A test that writes must still read its
    own data back, or the fixture would silently disable every vault test."""
    v = dd_vault.get_vault()
    v.record_case(
        canonical_entity_id="company:XX:rf4162",
        entity_name="RF4162 Probe Ltd",
        entity_type="company",
        jurisdiction="XX",
        registration_number="rf4162",
        latest_report_id="dd_rf4162",   # §3b: the parameter is latest_report_id
    )
    names = {(r.get("entity_name") or "") for r in (v.list_all(limit=50) or [])}
    assert "RF4162 Probe Ltd" in names, "a write to the isolated vault was not readable"
    assert pathlib.Path(dd_vault._VAULT_DB).exists(), (
        "the isolated database file was never created")
