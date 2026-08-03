"""R-F3657 — the call-arity gate must actually fire, and must stay quiet.

A gate nobody trusts gets switched off, so this pins BOTH halves:
  * each rule fires on a synthetic fixture that reproduces a real defect
  * the gate is clean on the live tree (so a regression is visible immediately)

The fixtures below are the four shapes that have actually shipped in this repo:
R-F1842 / R-F3647 / R-F3648 (keyword-only called positionally) and R-F3646 /
R-F3660 (a keyword the callee does not accept).
"""
from __future__ import annotations

import importlib.util
import os
import sys
import textwrap

import pytest

_GATE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "scripts", "admin", "call_arity_gate.py",
)


def _load_gate():
    spec = importlib.util.spec_from_file_location("call_arity_gate", _GATE_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["call_arity_gate"] = mod
    spec.loader.exec_module(mod)
    return mod


gate = _load_gate()


def _pkg(tmp_path, callee_src: str, caller_src: str):
    """Build a tiny two-module package and return the findings for it."""
    root = tmp_path / "pkg"
    root.mkdir()
    (root / "__init__.py").write_text("", encoding="utf-8")
    (root / "callee.py").write_text(textwrap.dedent(callee_src), encoding="utf-8")
    (root / "caller.py").write_text(textwrap.dedent(caller_src), encoding="utf-8")
    # the gate derives module names relative to REPO — point it at tmp_path
    old = gate.REPO
    gate.REPO = str(tmp_path)
    try:
        return gate.check(str(root))
    finally:
        gate.REPO = old


def test_rf3657_detects_keyword_only_called_positionally(tmp_path):
    """The R-F3647 / R-F3648 shape: keyword-only callee, positional call."""
    findings = _pkg(
        tmp_path,
        """
        async def analyze_transaction(*, declared_unit_value, hs_code):
            return {}
        """,
        """
        from . import callee as _c
        async def run(txn):
            return await _c.analyze_transaction(txn)
        """,
    )
    kinds = {f["kind"] for f in findings}
    assert "POSITIONAL_TO_KWONLY" in kinds, f"gate missed the defect: {findings}"
    hit = [f for f in findings if f["kind"] == "POSITIONAL_TO_KWONLY"][0]
    assert hit["callee"] == "analyze_transaction"


def test_rf3657_detects_unknown_kwarg(tmp_path):
    """The R-F3646 / R-F3660 shape: a keyword the callee does not accept."""
    findings = _pkg(
        tmp_path,
        """
        def wire_failure(module, detail, gap_type="engine_failure", source=""):
            return None
        """,
        """
        from .callee import wire_failure
        def go():
            wire_failure(module="m", summary="s", source_id="x")
        """,
    )
    hits = [f for f in findings if f["kind"] == "UNKNOWN_KWARG"]
    assert hits, f"gate missed the unknown kwarg: {findings}"
    assert "summary" in hits[0]["detail"]


def test_rf3657_detects_missing_required(tmp_path):
    """The R-F3660 dd_layer_extensions shape: required params never supplied."""
    findings = _pkg(
        tmp_path,
        """
        def classify_anomaly(declared, low, high):
            return {}
        """,
        """
        from . import callee as _c
        def go(target):
            return _c.classify_anomaly(target)
        """,
    )
    kinds = {f["kind"] for f in findings}
    assert "MISSING_REQUIRED" in kinds, f"gate missed it: {findings}"


def test_rf3657_does_not_fire_on_correct_calls(tmp_path):
    """No false positive on a call that satisfies the signature."""
    findings = _pkg(
        tmp_path,
        """
        async def analyze_transaction(*, declared_unit_value, hs_code, year=None):
            return {}
        """,
        """
        from . import callee as _c
        async def run():
            return await _c.analyze_transaction(declared_unit_value=1.0, hs_code="8802")
        """,
    )
    assert findings == [], f"false positive: {findings}"


def test_rf3657_ignores_same_named_unrelated_methods(tmp_path):
    """The bug that made the FIRST draft useless: matching on bare NAME made
    `x.strip()` resolve to an unrelated `def strip()` elsewhere in the tree —
    8,000 findings. Resolution must follow imports, never names."""
    findings = _pkg(
        tmp_path,
        """
        def strip(raw_response):
            return raw_response
        """,
        """
        def go(text):
            return text.strip()
        """,
    )
    assert findings == [], f"name-matching regression: {findings}"


def test_rf3657_live_tree_is_clean():
    """The real gate: aria_service must contain no impossible calls.

    If this fails, read the output — an impossible call is never a style issue,
    it is a code path that cannot execute and is almost certainly hidden behind
    a swallowing `except`.
    """
    findings = gate.check(gate.DEFAULT_ROOT)
    assert findings == [], (
        f"{len(findings)} impossible call(s) in aria_service:\n" +
        "\n".join(f"  {f['file']}:{f['line']} -> {f['callee']}  {f['detail']}"
                  for f in findings)
    )
