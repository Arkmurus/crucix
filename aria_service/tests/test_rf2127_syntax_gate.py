"""R-F2127 — the pre-commit syntax gate must FAIL on any broken .py.

2026-06-28: an autonomous annotation campaign committed 31 files with comments
inserted mid-expression (`httpx.AsyncClient(timeout  # no-breaker:…=3.0)`),
making the whole tree un-importable. Every existing pre-commit check ast.parse()s
and silently skips broken files, so they sailed through and the report called it
"safe to deploy". check_syntax() is the structural backstop: a staged .py that
doesn't compile must block the commit, regardless of which tool produced it.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "scripts"))
from pre_commit_checks import check_syntax  # noqa: E402


def test_rf2127_broken_file_is_flagged(tmp_path):
    broken = tmp_path / "broken.py"
    # the exact corruption pattern that took aria-intel down
    broken.write_text(
        "async with httpx.AsyncClient(timeout  # no-breaker: best-effort=3.0) as c:\n    pass\n",
        encoding="utf-8",
    )
    issues = check_syntax([broken])
    assert len(issues) == 1
    assert "SyntaxError" in issues[0]


def test_rf2127_stray_token_template_is_flagged(tmp_path):
    """The self_sufficient.py corruption: a stray token outside a string literal."""
    broken = tmp_path / "tmpl.py"
    broken.write_text(
        "X = (\n    'from .engine_wiring import wire_success\\n', wire_failure\n"
        "    '    wire_success(\\n'\n)\n",
        encoding="utf-8",
    )
    assert len(check_syntax([broken])) == 1


def test_rf2127_valid_file_passes(tmp_path):
    valid = tmp_path / "ok.py"
    valid.write_text(
        "import httpx\n\nasync def f():\n"
        "    async with httpx.AsyncClient(timeout=3.0) as c:  # no-breaker: fine\n"
        "        return await c.get('https://x')\n",
        encoding="utf-8",
    )
    assert check_syntax([valid]) == []


def test_rf2127_non_python_ignored(tmp_path):
    md = tmp_path / "readme.md"
    md.write_text("# not python (((", encoding="utf-8")
    assert check_syntax([md]) == []
