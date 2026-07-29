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
