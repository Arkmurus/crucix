"""R-F462 — self_improve human-gate for bug_fix + optimisation.

DD-audit P0 #4 (next_session_pickup_2026_05_15): pre-R-F462,
CHANGE_TYPES["bug_fix"] and CHANGE_TYPES["optimisation"] carried
`auto_deploy: True`, so the self-improvement loop wrote LLM-diagnosed
patches straight to live source files without operator review. This
contradicts [[cost_and_autonomy_gate]] and put the constitution one
buggy LLM diagnosis away from a regression.

R-F462 makes the default `auto_deploy=False` for both types. Operators
who want the legacy behaviour back set ARIA_SELF_IMPROVE_AUTO_DEPLOY=1.
This test pins the default-off contract.
"""
from __future__ import annotations

import importlib
import os

import pytest


def _reload_self_improve(env: dict):
    """Reload the module fresh under specific env vars so the
    module-import-time flag reflects the test scenario."""
    for k in ("ARIA_SELF_IMPROVE_AUTO_DEPLOY",):
        os.environ.pop(k, None)
    for k, v in env.items():
        os.environ[k] = v
    import aria_service.intel.self_improve as _si
    importlib.reload(_si)
    return _si


def test_rf462_default_is_human_gate(monkeypatch):
    """No env var set → bug_fix and optimisation default to auto_deploy=False."""
    monkeypatch.delenv("ARIA_SELF_IMPROVE_AUTO_DEPLOY", raising=False)
    _si = _reload_self_improve({})
    assert _si.CHANGE_TYPES["bug_fix"]["auto_deploy"] is False, (
        "R-F462 default-off: bug_fix should NOT auto-deploy without explicit opt-in"
    )
    assert _si.CHANGE_TYPES["optimisation"]["auto_deploy"] is False, (
        "R-F462 default-off: optimisation should NOT auto-deploy without explicit opt-in"
    )


def test_rf462_explicit_opt_in_restores_auto_deploy(monkeypatch):
    """Operator sets ARIA_SELF_IMPROVE_AUTO_DEPLOY=1 → legacy behaviour back."""
    monkeypatch.setenv("ARIA_SELF_IMPROVE_AUTO_DEPLOY", "1")
    _si = _reload_self_improve({"ARIA_SELF_IMPROVE_AUTO_DEPLOY": "1"})
    assert _si.CHANGE_TYPES["bug_fix"]["auto_deploy"] is True
    assert _si.CHANGE_TYPES["optimisation"]["auto_deploy"] is True


def test_rf462_human_only_change_types_still_human(monkeypatch):
    """Regression guard: prompt_evolution, new_intel_layer, enhancement
    must remain human-gated regardless of env var (these are constitution-
    touching or new-surface)."""
    monkeypatch.setenv("ARIA_SELF_IMPROVE_AUTO_DEPLOY", "1")
    _si = _reload_self_improve({"ARIA_SELF_IMPROVE_AUTO_DEPLOY": "1"})
    for ct in ("prompt_evolution", "new_intel_layer", "enhancement"):
        assert _si.CHANGE_TYPES[ct]["auto_deploy"] is False, (
            f"R-F462 regression: {ct} must stay human-gated even with opt-in flag"
        )


def test_rf462_truthy_variants_accepted(monkeypatch):
    """Env-var parser accepts the usual truthy spellings."""
    for v in ("1", "true", "TRUE", "yes", "On"):
        monkeypatch.setenv("ARIA_SELF_IMPROVE_AUTO_DEPLOY", v)
        _si = _reload_self_improve({"ARIA_SELF_IMPROVE_AUTO_DEPLOY": v})
        assert _si.CHANGE_TYPES["bug_fix"]["auto_deploy"] is True, (
            f"R-F462: spelling {v!r} should enable auto-deploy"
        )


def test_rf462_falsy_variants_stay_human(monkeypatch):
    """Empty or falsy env var keeps the safe default."""
    for v in ("", "0", "false", "no", "off"):
        monkeypatch.setenv("ARIA_SELF_IMPROVE_AUTO_DEPLOY", v)
        _si = _reload_self_improve({"ARIA_SELF_IMPROVE_AUTO_DEPLOY": v})
        assert _si.CHANGE_TYPES["bug_fix"]["auto_deploy"] is False, (
            f"R-F462: spelling {v!r} should NOT enable auto-deploy"
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
