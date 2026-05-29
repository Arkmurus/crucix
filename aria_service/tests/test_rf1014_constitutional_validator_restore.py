"""R-F1014 — the ConstitutionalValidator is restored (was gutted to a pass-through
by R-F995) AND the re.compile false positive that triggered the removal is fixed.

Unit: the guard blocks the real dangerous things again.
Capability (the user-visible symptom): legitimate `re.compile(...)` no longer
trips "dynamic code execution", so the guard does not block normal improvements
— which was R-F995's stated reason for removing it.
"""
from __future__ import annotations

from aria_service.autonomous.constitutional_validator import ConstitutionalValidator

TARGET = "aria_service/intel/researcher.py"  # a normal, editable module


def _v():
    return ConstitutionalValidator()


# ── the guard is real again (not a pass-through) ─────────────────────────────
def test_bare_eval_exec_compile_still_blocked():
    cv = _v()
    for builtin in ("eval", "exec", "compile"):
        code = f"def f(x):\n    return {builtin}(x)\n"
        r = cv.validate(code, TARGET)
        assert not r.passed, f"{builtin}() must be blocked"
        assert any("Dynamic code execution" in v for v in r.violations)


def test_dangerous_import_still_blocked():
    cv = _v()
    r = cv.validate("import subprocess\n", TARGET)
    # subprocess is in DANGEROUS_IMPORTS in the restored guard
    assert not r.passed
    assert any("angerous import" in v.lower() or "dangerous" in v.lower() for v in r.violations)


def test_validator_is_not_passthrough():
    # R-F995 had made this always passed=True with empty rule sets.
    from aria_service.autonomous import constitutional_validator as m
    assert m.DANGEROUS_IMPORTS, "DANGEROUS_IMPORTS must be populated (not gutted)"
    bad = _v().validate("import os\nos.system('rm -rf /')\n", TARGET)
    assert not bad.passed


# ── the false positive that caused the removal is fixed ──────────────────────
def test_re_compile_is_allowed():
    cv = _v()
    code = (
        "import re\n"
        "PATTERN = re.compile(r'\\d+')\n"
        "def f(s):\n"
        "    return PATTERN.findall(s)\n"
    )
    r = cv.validate(code, TARGET)
    assert r.passed, f"re.compile must not be flagged; violations={r.violations}"
    assert not any("Dynamic code execution" in v for v in r.violations)


def test_attribute_eval_method_is_allowed():
    # e.g. pandas df.eval(...) — an attribute call, not the eval() builtin
    cv = _v()
    code = "def f(df):\n    return df.eval('a + b')\n"
    r = cv.validate(code, TARGET)
    assert not any("Dynamic code execution" in v for v in r.violations)
