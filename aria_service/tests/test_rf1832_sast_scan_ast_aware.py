# -*- coding: utf-8 -*-
"""R-F1832 — capability tests for the AST-aware SAST scanner.

Proves the scanner (scripts/admin/sast_scan.py) does NOT reproduce the
substring-grep false positives that inflated the 2026-06-23 SAST report:
  * docstring placeholder keys (`sk-ant-...`) are NOT flagged as secrets
  * `run_eval()` / `model.eval()` / the token 'eval(' in a string are NOT eval
  * `py_compile.compile()` is NOT the builtin compile
  * hardcoded argv subprocess calls are NOT injection risks
…while still catching the REAL issues (a genuine hardcoded key, a bare builtin
eval(), shell=True with a dynamic command, a bare except).
"""
from __future__ import annotations

import importlib.util
import sys
import textwrap
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCANNER = REPO_ROOT / "scripts" / "admin" / "sast_scan.py"

_spec = importlib.util.spec_from_file_location("sast_scan", SCANNER)
sast = importlib.util.module_from_spec(_spec)
sys.modules["sast_scan"] = sast
_spec.loader.exec_module(sast)


def _scan(tmp_path, body: str):
    f = tmp_path / "sample.py"
    f.write_text(textwrap.dedent(body), encoding="utf-8")
    findings = sast.scan_file(f)
    by_check = {}
    for fi in findings:
        by_check.setdefault(fi.check, []).append(fi)
    return by_check


# ── False positives the OLD scanner produced — must NOT fire ──────────────────

def test_docstring_placeholder_keys_not_flagged(tmp_path):
    """The exact ocr.py shape: provider-key examples inside a docstring."""
    fp = _scan(tmp_path, '''
        """
        Set env:
            ARIA_VISION_API_KEY=AIza...
            ARIA_VISION_API_KEY=sk-or-...
            ARIA_VISION_API_KEY=sk-ant-...
        """
        import os
        def get_key():
            return os.getenv("ARIA_VISION_API_KEY", "")
    ''')
    assert fp.get("hardcoded_secret", []) == [], "docstring placeholders must not be secrets"


def test_run_eval_and_method_eval_not_flagged(tmp_path):
    fp = _scan(tmp_path, '''
        def run_eval(x):
            return x
        class M:
            def eval(self): ...
        def go(m):
            run_eval(1)
            m.eval()
            s = "eval(" + "danger"
            return s
    ''')
    assert fp.get("eval", []) == [], "run_eval/method.eval/string must not be builtin eval"


def test_py_compile_not_flagged_as_builtin(tmp_path):
    fp = _scan(tmp_path, '''
        import py_compile
        def check(p):
            py_compile.compile(p)
    ''')
    assert fp.get("compile", []) == [], "py_compile.compile is not the builtin compile()"


def test_hardcoded_argv_subprocess_not_risky(tmp_path):
    fp = _scan(tmp_path, '''
        import subprocess, sys
        def install():
            cmd = [sys.executable, "-m", "pip", "install", "easyocr"]
            return subprocess.run(cmd, capture_output=True, timeout=120)
        def restart(service):
            return subprocess.run(["flyctl", "apps", "restart", service], timeout=30)
    ''')
    assert fp.get("subprocess", []) == [], "argv-list form is not shell-injectable"


# ── Real issues — MUST fire ───────────────────────────────────────────────────

def test_real_hardcoded_anthropic_key_flagged(tmp_path):
    fp = _scan(tmp_path, '''
        API_KEY = "sk-ant-api03-aBcDeFgHiJkLmNoPqRsTuVwXyZ0123456789abcdefXYZ"
    ''')
    assert len(fp.get("hardcoded_secret", [])) == 1, "a full-length real key must be flagged"


def test_bare_builtin_eval_flagged(tmp_path):
    fp = _scan(tmp_path, '''
        def danger(user_input):
            return eval(user_input)
    ''')
    assert len(fp.get("eval", [])) == 1


def test_shell_true_dynamic_command_flagged(tmp_path):
    fp = _scan(tmp_path, '''
        import subprocess
        def run(user_cmd):
            return subprocess.run(user_cmd, shell=True)
    ''')
    risky = fp.get("subprocess", [])
    assert len(risky) == 1 and "NON-CONSTANT" in risky[0].detail


def test_bare_except_flagged(tmp_path):
    fp = _scan(tmp_path, '''
        def f():
            try:
                g()
            except:
                pass
    ''')
    assert len(fp.get("bare_except", [])) == 1


def test_keyword_arg_real_secret_flagged(tmp_path):
    fp = _scan(tmp_path, '''
        def call():
            client(api_key="sk-ant-api03-ZZZZyyyyXXXXwwwwVVVVuuuuTTTTssssRRRRqqqq1234")
    ''')
    assert len(fp.get("hardcoded_secret", [])) == 1


def test_keyword_arg_from_env_not_flagged(tmp_path):
    """api_key=config.x or os.getenv(...) is a Call/Attribute, not a Constant."""
    fp = _scan(tmp_path, '''
        import os
        def call(config):
            client(api_key=config.anthropic_api_key)
            client(api_key=os.getenv("KEY", ""))
    ''')
    assert fp.get("hardcoded_secret", []) == []
