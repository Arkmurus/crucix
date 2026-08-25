"""R-F2060 — the ARIA Coder CLI test tool must run pytest under THIS interpreter.

Root cause of ARIA's false "8-gap ecosystem report" (2026-06-27): coder_tools.test()
shelled out to a bare `python -m pytest`, which on PATH can resolve to a SYSTEM
interpreter that lacks numpy / chromadb / sentence-transformers. Every test that
imports those then reports a COLLECTION ERROR, and ARIA mis-reads the wall of
collection errors as code "regressions". Binding the test interpreter to
sys.executable (the venv/container python ARIA is already running under, which by
definition has the deps) makes that whole failure class impossible — the same root
cause + fix as R-F1928 for the autonomous TestRunner.

Capability test: drive the REAL CoderTools.test()/reserve/ship paths and assert the
emitted shell command uses sys.executable, never a bare PATH `python`.
"""
import sys
import pathlib

from aria_cli.coder_tools import CoderToolbox
from aria_cli.tools import ToolResult


class _FakeTB:
    def __init__(self, root):
        self.root = root
        self.calls = []

    def run(self, command, timeout=300, cwd=""):
        self.calls.append(command)
        return ToolResult("collected 7860 items")


def test_rf2060_test_tool_uses_running_interpreter(tmp_path):
    tb = _FakeTB(tmp_path)
    CoderToolbox(tb).test(path="aria_service/tests", pattern="rf2060")
    cmd = tb.calls[-1]
    assert sys.executable in cmd, f"test runner must use the running interpreter: {cmd!r}"
    assert not cmd.lstrip().startswith("python "), f"must not invoke bare PATH python: {cmd!r}"
    assert "-m pytest" in cmd


def test_rf2060_reserve_and_ship_use_running_interpreter():
    # reserve/ship resolve the admin script under repo root, then shell out — they
    # too must use the running interpreter (consistency + same dep guarantee).
    repo_root = pathlib.Path(__file__).resolve().parents[2]
    if not (repo_root / "scripts" / "admin" / "reserve_r_number.py").exists():
        import pytest
        pytest.skip("reserve script not present in this layout")
    tb = _FakeTB(repo_root)
    box = CoderToolbox(tb)
    box.reserve_r_number("rf2060 capability test")
    assert sys.executable in tb.calls[-1], tb.calls[-1]
    box.ship_r_number("R-F0000", "deadbeef")
    # R-F2162's _record_shipped_fix may append a `git log` side-effect call after
    # the ship command, so assert the SHIP command (the one invoking the admin
    # script) uses the running interpreter rather than assuming it is last.
    ship_cmds = [c for c in tb.calls if "reserve_r_number.py" in c and " ship " in c]
    assert ship_cmds, "ship_r_number should have issued a ship command"
    assert sys.executable in ship_cmds[-1], ship_cmds[-1]
