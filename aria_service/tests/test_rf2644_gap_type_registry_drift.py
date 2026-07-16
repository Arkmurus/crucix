"""R-F2644 — the gap-type drift guard was vacuous; make it real.

THE BUG WAS THE GUARD, not the registry. F74 (2026-04-28) added
`test_all_emitted_gap_types_are_registered` in
test_cooldown_persist_and_log_hygiene.py. Its docstring claimed it was

    "The set produced by `grep -rn 'gap_type=' aria_service/` against
     production code, deduped. If a future commit adds a new gap_type
     without registering it, this test fails until it's added."

It could not do that. The set was a **hardcoded 24-entry snapshot** of the
tree as it stood on 2026-04-28 — it never read a single source file. So it
passed forever while the codebase drifted underneath it: by 2026-07-16, **42
of 133 emitted gap types (32%) were unregistered**, each logging
"Unknown gap type ... — recording anyway" (capability_gaps.py:259) on every
emit and mirroring that warning into the error ledger as noise.

This is the same vacuous-certification class as gate #3 (R-F2622: certified a
clean week from an EMPTY ledger) and gate #6 (R-F2640: passed on the SIZE of a
mutable list). A guard that asserts a frozen literal against the registry
measures nothing about the code it claims to guard. Registering the 42 without
fixing the guard would just restart the same silent drift — so the guard is
replaced here with a real scan, and the vacuous snapshot is deleted (ONE
measure; cf. the R-F2639 "do not fork it again" rule).

Scope limit, stated honestly: the scan matches `gap_type="literal"` kwargs. A
gap_type passed as a variable would evade it. That is acceptable and complete
for the real failure mode — every live call site uses the literal kwarg
(verified 2026-07-16: no positional `record_gap("x"` / `wire_failure("x"` call
sites exist; the only such greps are R-F2538 removal comments).
"""

from __future__ import annotations

import pathlib
import re

import pytest

from aria_service.intel.capability_gaps import VALID_GAP_TYPES

_GAP_TYPE_KWARG = re.compile(r'gap_type\s*=\s*["\']([a-z0-9_]+)["\']')

_PRODUCTION_ROOT = pathlib.Path(__file__).resolve().parents[1]


def scan_emitted_gap_types(root: pathlib.Path) -> dict[str, list[str]]:
    """Return {gap_type: ["file:line", ...]} for every literal emit under ``root``.

    Reads the tree — this is the whole point of R-F2644. Test files and
    __pycache__ are excluded: a test may deliberately emit a bogus type.
    """
    found: dict[str, list[str]] = {}
    for path in sorted(root.rglob("*.py")):
        parts = path.parts
        if "tests" in parts or "__pycache__" in parts:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:  # pragma: no cover — unreadable file
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            for match in _GAP_TYPE_KWARG.finditer(line):
                found.setdefault(match.group(1), []).append(f"{path.as_posix()}:{lineno}")
    return found


def test_scanner_detects_an_unregistered_type(tmp_path: pathlib.Path) -> None:
    """Capability test: the guard must actually CATCH drift.

    This is the test the old snapshot could never pass. It plants a module
    emitting an unregistered gap_type and asserts the scanner surfaces it with
    its call site — proving the guard reads the tree rather than a frozen list.
    """
    module = tmp_path / "fake_engine.py"
    module.write_text(
        "from .wire import wire_failure\n"
        "def boom():\n"
        '    wire_failure(module="fake", detail="x", gap_type="totally_bogus_type_xyz")\n',
        encoding="utf-8",
    )

    found = scan_emitted_gap_types(tmp_path)

    assert "totally_bogus_type_xyz" in found
    assert found["totally_bogus_type_xyz"] == [f"{module.as_posix()}:3"]
    assert "totally_bogus_type_xyz" not in VALID_GAP_TYPES


def test_scanner_ignores_test_files(tmp_path: pathlib.Path) -> None:
    """A bogus type inside tests/ must not fail the production gate."""
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_thing.py").write_text(
        'wire_failure(module="t", detail="d", gap_type="only_in_a_test")\n',
        encoding="utf-8",
    )

    assert scan_emitted_gap_types(tmp_path) == {}


def test_no_unregistered_gap_types_in_production() -> None:
    """THE GATE: every gap_type production emits must be registered.

    Unlike the F74 snapshot this replaces, this fails the moment a new
    unregistered gap_type lands anywhere in aria_service/ — which is what the
    F74 docstring promised and never delivered.
    """
    found = scan_emitted_gap_types(_PRODUCTION_ROOT)
    unregistered = {g: sites for g, sites in found.items() if g not in VALID_GAP_TYPES}

    assert unregistered == {}, (
        "these gap types are emitted but not registered in VALID_GAP_TYPES — "
        "they will log 'Unknown gap type' on every emit and demote a real "
        "signal to noise. Register them in capability_gaps.py:\n"
        + "\n".join(f"  {g}  <- {sites[0]}" for g, sites in sorted(unregistered.items()))
    )


def test_the_vacuous_f74_snapshot_is_gone() -> None:
    """Anti-regression: the hardcoded snapshot must not come back.

    It is not merely redundant — it is actively misleading: it PASSES while the
    tree drifts, which is precisely how 42 types accumulated unnoticed. If a
    future commit reintroduces a literal set here, this fails.
    """
    src = (
        _PRODUCTION_ROOT / "tests" / "test_cooldown_persist_and_log_hygiene.py"
    ).read_text(encoding="utf-8", errors="ignore")

    assert "emitted_in_production = {" not in src, (
        "the F74 hardcoded gap-type snapshot is back in "
        "test_cooldown_persist_and_log_hygiene.py — it cannot detect drift; "
        "the real scan lives in test_rf2644_gap_type_registry_drift.py"
    )


@pytest.mark.parametrize("gap_type", ["search_all_engines_blocked", "module_bug"])
def test_known_previously_unregistered_types_are_now_registered(gap_type: str) -> None:
    """Spot-check two of the 42 (R-F2642's find, and a canonical GapType name)."""
    assert gap_type in VALID_GAP_TYPES
