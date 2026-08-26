"""R-F4353 (C-298) — the R-F559 gate must be able to SEE every test tier.

MEASURED 2026-08-26. A CLI fix (R-F4351) shipped with a real capability test at
``aria_cli/tests/test_rf4351_stream_toolcall_reemission.py`` and the pre-push
hook refused the push::

    [R-F559] FAIL - R-numbers missing test file:
      R-F4351

``_all_test_files()`` globbed ``aria_service/tests/`` and the Node ``test/``
directory, and nothing else. ``aria_cli/tests/`` — 47 test files — was
invisible, so **no CLI fix could ever satisfy the gate**, however well tested.

This is the SAME defect R-F3281 already fixed once for the web tier. Its
docstring states the cost as "a false accusation", and the real cost is worse:
the gate fails CLOSED and runs over the whole push range, so it blocked a second
session's four unrelated commits too, and a hook that cannot be satisfied
honestly pushes the next author toward ``--no-verify`` — which disables the gate
for EVERY R-number in the push, not just the one it misjudged.

R-F3281 widened a list from one entry to two and a third tier appeared anyway,
so the fix is discovery by SHAPE (``*/tests/test_*.py``) rather than a longer
list. These tests pin that property, not the specific directories: a test
asserting "aria_cli is in the list" would go green while the FOURTH tier stayed
invisible.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_SCRIPT = _REPO / "scripts" / "verify_commit.py"


def _import_module():
    """Load scripts/verify_commit.py with an EXPLICIT utf-8 read.

    The sibling ``test_verify_commit_rf559.py`` uses
    ``spec.loader.exec_module``, which resolves the source encoding through the
    import machinery. On this win32 box ``locale.getpreferredencoding()`` is
    cp1252 while the script is utf-8 and full of em-dashes, so that path has one
    more way to fail than the file it is testing. Reading the bytes ourselves
    with an explicit encoding means THIS test measures the verifier's tier
    coverage and nothing else.
    """
    src = _SCRIPT.read_text(encoding="utf-8")
    mod = importlib.util.module_from_spec(
        importlib.util.spec_from_loader("_vc_rf4353", loader=None))
    mod.__file__ = str(_SCRIPT)
    exec(compile(src, str(_SCRIPT), "exec"), mod.__dict__)
    return mod


def _tiers_seen(mod) -> set[str]:
    """Which `<package>/tests` directories the verifier can actually enumerate."""
    tiers = set()
    for f in mod._all_test_files():
        try:
            rel = f.resolve().relative_to(_REPO.resolve())
        except ValueError:
            continue
        if len(rel.parts) >= 2 and rel.parts[1] == "tests":
            tiers.add(rel.parts[0])
    return tiers


def test_every_package_tests_dir_is_discovered_not_just_listed() -> None:
    """THE PROPERTY. Every ``<package>/tests`` directory holding test files must
    be enumerable. Asserting a specific package name instead would go green
    while the NEXT tier stayed invisible — which is exactly how this defect
    survived R-F3281."""
    mod = _import_module()
    seen = _tiers_seen(mod)

    on_disk = {
        d.parent.name for d in _REPO.glob("*/tests")
        if d.is_dir() and any(d.glob("test_*.py"))
    }
    assert on_disk, "fixture assumes the repo has package test dirs"

    missing = on_disk - seen
    assert not missing, (
        f"test tiers invisible to the R-F559 gate: {sorted(missing)}. "
        "A fix landing there cannot satisfy the gate, blocking the push and "
        "pointing the next author at --no-verify.")


def test_the_real_cli_capability_test_satisfies_the_gate() -> None:
    """THE SYMPTOM. R-F4351's capability test exists; the gate must map the
    R-number to it rather than reporting it missing."""
    mod = _import_module()

    expected = _REPO / "aria_cli" / "tests" / "test_rf4351_stream_toolcall_reemission.py"
    assert expected.exists(), "fixture assumes R-F4351's test is still present"

    found = mod._test_files_present_for("R-F4351")
    assert found, "R-F4351 has a capability test but the gate reports it missing"
    assert expected.resolve() in {f.resolve() for f in found}


def test_widening_did_not_drop_the_existing_tiers() -> None:
    """A widened enumeration must not lose what it already had: the
    aria_service tier and the Node tier both stay visible."""
    mod = _import_module()
    assert mod._test_files_present_for("R-F540"), "aria_service tier regressed"

    files = mod._all_test_files()
    assert "aria_service" in _tiers_seen(mod)
    assert any(f.name.endswith(".test.mjs") for f in files), "node tier regressed"


def test_enumeration_stays_out_of_venv_and_node_modules() -> None:
    """Discovery by shape must stay ONE level deep. A recursive walk would drag
    in .venv/ and node_modules/, making the gate slow and matching third-party
    test files that prove nothing about our R-numbers."""
    mod = _import_module()
    bad = [f for f in mod._all_test_files()
           if any(p in {".venv", "node_modules", "site-packages"}
                  for p in f.resolve().parts)]
    assert not bad, f"enumeration escaped into vendored trees: {bad[:3]}"
