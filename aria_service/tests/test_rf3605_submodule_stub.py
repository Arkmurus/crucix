"""R-F3605 — `sys.modules` stubbing is defeated by the parent-package attribute.

MEASURED 2026-08-01. Three test files stubbed a submodule with
`monkeypatch.setitem(sys.modules, "aria_service.intel.X", stub)`. All three passed
ALONE and failed inside the full suite — seven failures, one cause:

    pkg has .knowledge attr BEFORE importing aria_engine : False
    pkg has .knowledge attr AFTER                        : True
    from-import gets the STUB?                            False

`from . import X` reads the attribute the import machinery set on the PARENT PACKAGE.
It never consults `sys.modules`. So the stub only works while nothing has imported the
target yet — and the failure is SILENT: `mem0.retrieve_for_query` returned "" and the
owner-scoping assertion read that as "the other user's fact was correctly withheld".

Two of the three (`test_rf463`, `test_rf468`) have been in CLAUDE.md §16's long-red
cluster list since 2026-05-20.
"""
from __future__ import annotations

import sys
import types

import pytest

from ._module_stub import stub_submodule


@pytest.fixture
def victim(monkeypatch):
    """A real package + submodule, imported so the parent attribute is set — which is
    precisely the state a long test session leaves behind."""
    pkg = types.ModuleType("rf3605_pkg")
    pkg.__path__ = []                                  # make it a package
    sub = types.ModuleType("rf3605_pkg.leaf")
    sub.VALUE = "REAL"
    monkeypatch.setitem(sys.modules, "rf3605_pkg", pkg)
    monkeypatch.setitem(sys.modules, "rf3605_pkg.leaf", sub)
    pkg.leaf = sub                                     # what `import` itself does
    return pkg


def _from_import():
    """Exactly what production code does: `from . import leaf`."""
    from rf3605_pkg import leaf
    return leaf


def test_sys_modules_alone_is_defeated_by_the_package_attribute(victim, monkeypatch):
    """PROVE THE DEFECT — this is why seven tests failed only in the full suite."""
    stub = types.ModuleType("stub"); stub.VALUE = "STUB"
    monkeypatch.setitem(sys.modules, "rf3605_pkg.leaf", stub)
    assert _from_import().VALUE == "REAL", (
        "the sys.modules patch reached the from-import; the fixture no longer "
        "reproduces the parent-attribute precedence this guard exists for"
    )


def test_stub_submodule_reaches_the_from_import(victim, monkeypatch):
    """CAPABILITY: the helper patches both, so the stub actually takes effect."""
    stub = types.ModuleType("stub"); stub.VALUE = "STUB"
    stub_submodule(monkeypatch, "rf3605_pkg.leaf", stub)
    assert _from_import().VALUE == "STUB"


def test_it_also_covers_the_plain_import_form(victim, monkeypatch):
    stub = types.ModuleType("stub"); stub.VALUE = "STUB"
    stub_submodule(monkeypatch, "rf3605_pkg.leaf", stub)
    import importlib
    assert importlib.import_module("rf3605_pkg.leaf").VALUE == "STUB"


def test_monkeypatch_restores_both_halves(victim, monkeypatch):
    """A stub that leaks is the OTHER half of this failure class (R-F3449). The
    restore must cover the package attribute too, not just sys.modules."""
    stub = types.ModuleType("stub"); stub.VALUE = "STUB"
    stub_submodule(monkeypatch, "rf3605_pkg.leaf", stub)
    assert victim.leaf.VALUE == "STUB"
    monkeypatch.undo()
    # The PACKAGE ATTRIBUTE is the half that leaked before — assert it directly.
    assert victim.leaf.VALUE == "REAL", "the package attribute was not restored"
    # NOTE: sys.modules is deliberately NOT asserted here. `undo()` reverts every
    # operation in this test INCLUDING the fixture's own setitem that created the
    # entry, so the key is absent rather than stale. Asserting on it would be
    # measuring the fixture, not the helper — the first cut of this test did exactly
    # that and failed with KeyError.
    assert "rf3605_pkg.leaf" not in sys.modules or \
        sys.modules["rf3605_pkg.leaf"].VALUE == "REAL"


def test_a_bare_name_is_rejected(monkeypatch):
    """"json" has no parent package; silently doing nothing would be worse."""
    with pytest.raises(ValueError):
        stub_submodule(monkeypatch, "json", object())


def test_an_unimportable_parent_still_gets_the_sys_modules_entry(monkeypatch):
    stub = types.ModuleType("stub")
    stub_submodule(monkeypatch, "rf3605_no_such_parent.leaf", stub)
    assert sys.modules["rf3605_no_such_parent.leaf"] is stub


# ── the real call sites ───────────────────────────────────────────────────────

def test_the_three_known_victims_use_the_helper():
    """R-F3515's lesson: assert the fix is actually WIRED, not merely available.
    These three were the measured failures — 5 + 1 + 1."""
    from pathlib import Path

    tests = Path(__file__).resolve().parent
    for name in ("test_rf3489_mem0_recall_is_owner_scoped.py",
                 "test_rf463_memory_replication_patterns.py",
                 "test_rf468_mistake_ledger_no_ttl.py"):
        src = (tests / name).read_text(encoding="utf-8")
        assert "stub_submodule(" in src, f"{name} no longer uses the helper"
        assert 'setitem(sys.modules, "aria_service.' not in src, (
            f"{name} reintroduced the defeated sys.modules-only stub"
        )
