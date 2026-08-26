"""R-F4342 — C-286: `run` reported exit 0 for a command PowerShell never found.

MEASURED on the operator's box, before the fix:

    definitely-not-a-command-xyz   -> exit code: 0, is_error=False
    R1: pytest --version           -> exit code: 0, is_error=False

`$LASTEXITCODE` is set only by NATIVE EXECUTABLES, so the old
`if ($null -ne $LASTEXITCODE) { exit $LASTEXITCODE }` fell straight through to
exit 0 for every FAILING cmdlet — a CommandNotFoundException included. ARIA was
told a command SUCCEEDED when it had never run.

This is §3/§23 defeated at the tool layer: an agent required to "run the test
before claiming it passes" was structurally unable to tell a failed command from
a passing one, and the same absence-reads-as-success shape §1 records three times.

Not hypothetical — it is why a real coding turn reported `exit code: 0` on
`R1: pytest ...` and then confidently reasoned about a test run that never
happened.
"""
from __future__ import annotations

import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aria_cli.safety import WriteGuard  # noqa: E402
from aria_cli.tools import Toolbox  # noqa: E402

pytestmark = pytest.mark.skipif(
    sys.platform != "win32",
    reason="the defect is in the Windows PowerShell wrapper")


@pytest.fixture()
def box(tmp_path):
    return Toolbox(tmp_path, WriteGuard(tmp_path))


# -- THE CAPABILITY TEST -------------------------------------------------------

def test_a_command_that_does_not_exist_is_an_error(box):
    """THE MEASURED SYMPTOM. Before the fix: exit code 0, is_error False."""
    res = box.run("definitely-not-a-command-xyz")
    assert res.is_error is True, (
        "a non-existent command reported SUCCESS — ARIA cannot verify anything "
        "if 'not found' is indistinguishable from 'worked'"
    )
    assert "exit code: 0" not in str(res.output)


def test_the_operators_actual_failing_shape_is_an_error(box):
    """Her `R1:` step prefix leaks into the command string, so PowerShell never
    finds it. That must read as a failure, not a pass."""
    res = box.run("R1: pytest --version")
    assert res.is_error is True


# -- it must still be able to PASS, or it carries no information --------------

@pytest.mark.parametrize("cmd", [
    "Write-Output hello",       # pure cmdlet, succeeds, no $LASTEXITCODE
    "Get-ChildItem",            # cmdlet with output
    "python --version",         # native exe, exit 0
])
def test_a_successful_command_is_not_an_error(box, cmd):
    res = box.run(cmd)
    assert res.is_error is False, (
        f"{cmd!r} reported failure — biasing toward false failure is right, but "
        f"a check that fails on everything is not a check"
    )


# -- a native exe's REAL code must survive, not collapse to 1 -----------------

@pytest.mark.parametrize("code", [3, 7, 42])
def test_a_native_exit_code_is_preserved_exactly(box, code):
    """$LASTEXITCODE still wins. Collapsing every failure to 1 would lose the
    distinction pytest/git/docker encode in their exit codes."""
    res = box.run(f'python -c "import sys; sys.exit({code})"')
    assert res.is_error is True
    assert f"exit code: {code}" in str(res.output)


def test_a_shell_exit_code_is_preserved(box):
    res = box.run("exit 3")
    assert res.is_error is True
    assert "exit code: 3" in str(res.output)


# -- the ordering inside the script is load-bearing ---------------------------

def test_the_wrapper_reads_the_success_flag_before_anything_else():
    """`$?` reflects only the IMMEDIATELY preceding statement — the
    `$LASTEXITCODE` assignment would reset it to True and silently restore the
    defect. Asserted on the emitted script, because the ordering is invisible in
    behaviour once it is wrong (everything would simply pass again)."""
    src = (ROOT / "aria_cli/tools.py").read_text(encoding="utf-8")
    i_ok = src.index("$__aria_ok = $?")
    i_rc = src.index("$__aria_rc = $LASTEXITCODE")
    assert i_ok < i_rc, (
        "$? is read after the $LASTEXITCODE assignment, which resets it — the "
        "command-not-found case would report success again"
    )
