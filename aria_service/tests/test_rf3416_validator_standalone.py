"""R-F3416 — the corpus validator must import without the service package.

R-F3398 added credential preconditions to the capture scripts, and put

    from aria_service.env_bootstrap import load_project_env, require_env

at MODULE level in build_tooluse_corpus. That file is not only a capture CLI —
it is the VALIDATOR, imported by the eval harness, and the pod only receives
`scripts/train/*`. So the first real cycle died at the baseline eval with
`ModuleNotFoundError: No module named 'aria_service'` after paying for a pod,
a GPU and a 60-second model load.

The dependency is real but it belongs to ONE function. `check_preconditions()`
is called from the CLI entry point; nothing in the grading, building or
validating path needs it. A module-level import for a CLI-only concern makes
the whole module unimportable wherever the service package is absent — which
is every remote execution context this pipeline has.
"""
from __future__ import annotations

import builtins
import importlib
import sys
from pathlib import Path

import pytest


def _reimport_without_aria_service(modname: str):
    """Import `modname` in an environment where `aria_service` cannot be found.

    Reproduces the pod: only scripts/train/* is present.
    """
    real_import = builtins.__import__

    def blocked(name, *a, **kw):
        if name == "aria_service" or name.startswith("aria_service."):
            raise ModuleNotFoundError("No module named 'aria_service'")
        return real_import(name, *a, **kw)

    saved = {k: v for k, v in sys.modules.items()
             if k == modname or k.startswith("aria_service")}
    for k in list(saved):
        if k == modname:
            del sys.modules[k]
    builtins.__import__ = blocked
    try:
        return importlib.import_module(modname)
    finally:
        builtins.__import__ = real_import
        sys.modules.update(saved)


def test_validator_imports_without_aria_service():
    m = _reimport_without_aria_service("scripts.train.build_tooluse_corpus")
    assert callable(m.validate_trace)


def test_validation_still_works_in_that_environment():
    """Importable is not enough — it must still grade."""
    m = _reimport_without_aria_service("scripts.train.build_tooluse_corpus")
    payload = {"status": "OK", "entity": "Acme",
               "sanctions": {"screened": True, "sources": ["ofac_sdn"], "matches": []}}
    t = m.build_trace("Acme", payload)
    assert m.validate_trace(t) == []


def test_eval_harness_imports_without_aria_service():
    """The harness the pod actually runs."""
    m = _reimport_without_aria_service("scripts.train.eval_tooluse")
    assert callable(m.score_one) and callable(m.build_report)


def test_preconditions_still_enforced_when_the_package_IS_present(monkeypatch):
    """Making the import lazy must not disarm the credential guard.

    Patched at the SOURCE module: after the fix `load_project_env` is a local
    inside check_preconditions, so patching it as an attribute of the capture
    module is a no-op that would make this test pass without testing anything.
    """
    from aria_service import env_bootstrap as E
    from scripts.train import build_tooluse_corpus as B

    monkeypatch.setattr(E, "load_project_env", lambda *a, **k: 0)
    monkeypatch.delenv("ARIA_INTERNAL_TOKEN", raising=False)
    with pytest.raises(E.MissingCredentials):
        B.check_preconditions()


def test_the_guard_passes_when_the_credential_is_present(monkeypatch):
    from aria_service import env_bootstrap as E
    from scripts.train import build_tooluse_corpus as B

    monkeypatch.setattr(E, "load_project_env", lambda *a, **k: 0)
    monkeypatch.setenv("ARIA_INTERNAL_TOKEN", "present")
    B.check_preconditions()


# --------------------------------------------------------------------------
# R-F3418 — simulate the POD'S ACTUAL FILE SET, not one missing module
# --------------------------------------------------------------------------

def _pod_pushed_modules() -> set[str]:
    """The scripts.train modules the driver actually copies to the pod.

    DERIVED FROM THE DRIVER, never hardcoded: a hand-written list is exactly
    what let this class recur. R-F3416 blocked only `aria_service`, so the test
    modelled ONE missing module while the pod is missing everything that is not
    on this list — and the next cycle died on `scripts.train._subjects`, which
    the validator imports at module scope to build DEFAULT_SUBJECTS.
    """
    drv = Path(__file__).resolve().parents[2] / "scripts" / "train" / "tooluse_cycle.sh"
    mods = set()
    for line in drv.read_text(encoding="utf-8").splitlines():
        if not line.startswith("RSCP scripts/train/"):
            continue
        name = line.split("RSCP scripts/train/")[1].split()[0]
        if name.endswith(".py"):
            mods.add("scripts.train." + name[:-3])
    return mods


def _import_as_pod(modname: str):
    """Import `modname` with every non-pushed scripts.train module unavailable."""
    allowed = _pod_pushed_modules() | {"scripts.train", "scripts"}
    real_import = builtins.__import__

    def blocked(name, *a, **kw):
        if name == "aria_service" or name.startswith("aria_service."):
            raise ModuleNotFoundError("No module named 'aria_service'")
        if name.startswith("scripts.train.") and name not in allowed:
            raise ModuleNotFoundError(f"No module named {name!r}")
        return real_import(name, *a, **kw)

    saved = {k: v for k, v in sys.modules.items() if k.startswith("scripts.train")}
    for k in list(sys.modules):
        if k.startswith("scripts.train.") and k not in allowed:
            del sys.modules[k]
    sys.modules.pop(modname, None)
    builtins.__import__ = blocked
    try:
        return importlib.import_module(modname)
    finally:
        builtins.__import__ = real_import
        sys.modules.update(saved)


def test_the_pushed_set_is_actually_derived():
    mods = _pod_pushed_modules()
    assert "scripts.train.build_tooluse_corpus" in mods
    assert "scripts.train.eval_tooluse" in mods
    assert "scripts.train._subjects" not in mods, (
        "the roster is NOT pushed — that is the condition under test")


@pytest.mark.parametrize("mod", [
    "scripts.train.build_tooluse_corpus",
    "scripts.train.eval_tooluse",
])
def test_pod_side_modules_import_with_only_what_the_pod_has(mod):
    """The condition that killed two cycles, asserted for free."""
    m = _import_as_pod(mod)
    assert m is not None


def test_the_validator_still_grades_under_pod_conditions():
    m = _import_as_pod("scripts.train.build_tooluse_corpus")
    payload = {"status": "OK", "entity": "Acme",
               "sanctions": {"screened": True, "sources": ["ofac_sdn"], "matches": []}}
    assert m.validate_trace(m.build_trace("Acme", payload)) == []


def test_the_capture_roster_still_resolves_when_it_IS_available():
    """Making it lazy must not break the capture path that needs it."""
    from scripts.train import build_tooluse_corpus as B

    subs = B.default_subjects()
    assert isinstance(subs, list) and len(subs) > 50


def test_no_bare_DEFAULT_SUBJECTS_lookup_remains_inside_the_module():
    """PEP 562 __getattr__ does NOT cover the module's own global lookups.

    `DEFAULT_SUBJECTS` resolves for importers but NameErrors inside the module
    itself, so the one CLI path that needed the roster would have died at
    runtime — a lazy-import fix that moved the failure rather than removing it.

    Checked with `ast`, not text: a docstring mentioning the name is fine, a
    load of it as a variable is not.
    """
    import ast

    src = (Path(__file__).resolve().parents[2] / "scripts" / "train"
           / "build_tooluse_corpus.py").read_text(encoding="utf-8")
    loads = [n.lineno for n in ast.walk(ast.parse(src))
             if isinstance(n, ast.Name) and n.id == "DEFAULT_SUBJECTS"
             and isinstance(n.ctx, ast.Load)]
    assert not loads, f"bare internal load of DEFAULT_SUBJECTS at lines {loads}"


def test_the_capture_cli_path_can_build_its_subject_list():
    from scripts.train import build_tooluse_corpus as B

    subs = B.default_subjects()
    assert subs[:3] and len(subs) == len(set(subs))
