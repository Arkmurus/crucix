"""R-F1928 — TestRunner must invoke pytest with sys.executable, NOT literal "python".

Root cause it fixes: subprocess.run(["python", "-m", "pytest", ...]) resolves
"python" via PATH, which on Windows/venv is often the SYSTEM interpreter — which
lacks the app's test deps (e.g. pytest-timeout). pytest then rejected
"--timeout=30" ("unrecognized arguments"), so EVERY isolated coder test run
ERRORed → the coder could never verify a fix → no gold row could ever land.
That was a real root cause behind the coder's "0 gold" history.

This is a source-level guard: the pytest command in test_runner.py must use
sys.executable and must not hardcode the "python" interpreter token.
"""
from __future__ import annotations

import ast
import pathlib

_TR = pathlib.Path(__file__).resolve().parents[1] / "autonomous" / "test_runner.py"


def test_pytest_cmd_uses_sys_executable_not_literal_python():
    src = _TR.read_text(encoding="utf-8")
    # The pytest invocation must build argv with sys.executable.
    assert "sys.executable" in src, "TestRunner must use sys.executable for the pytest subprocess"
    # And must not reintroduce the fragile literal-"python" interpreter token as
    # the first element of a pytest command list.
    tree = ast.parse(src)
    bad = []
    for node in ast.walk(tree):
        if isinstance(node, ast.List) and node.elts:
            first = node.elts[0]
            rest = {getattr(e, "value", None) for e in node.elts[1:] if isinstance(e, ast.Constant)}
            if (isinstance(first, ast.Constant) and first.value == "python"
                    and "-m" in rest and "pytest" in rest):
                bad.append(node.lineno)
    assert not bad, f"literal 'python' pytest command(s) at line(s) {bad} — use sys.executable"
