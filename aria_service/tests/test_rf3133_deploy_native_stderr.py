"""R-F3133 — deploy.ps1 aborted on flyctl's first stderr line under Windows PowerShell 5.1.

LIVE 2026-07-26. The operator ran `.\\scripts\\deploy.ps1 -Intel`. Every gate passed
(push guard, tree integrity, r-tags R-F3129+R-F3130+R-F3131+R-F3132), then:

    Running: flyctl deploy --config fly.toml --app aria-intel --wait-timeout 900s
    ...
    C:\\code\\crucix\\scripts\\deploy.ps1:187 char:5
    + flyctl deploy --remote-only --config $Config --app $App --wait-ti ...
    CategoryInfo: NotSpecified: (==> Verifying app config:String) [], RemoteException

NO Fly release was created — `flyctl apps releases` stayed at v2661 and live build_rev
stayed at the PREVIOUS commit. The deploy did not merely fail late; it never started.

MECHANISM: under Windows PowerShell 5.1, `2>&1` on a NATIVE command wraps each stderr
line in an ErrorRecord. With deploy.ps1's script-level `$ErrorActionPreference='Stop'`,
the FIRST record is a TERMINATING error. flyctl writes all progress to stderr, so the
redirection that existed to CAPTURE flyctl's output was what killed the script.
PowerShell 7 yields plain strings instead — which is why the same script deployed fine
from pwsh for weeks. The bug lived in the OPERATOR'S shell, not in a code path any of
my runs touched (§23: reproduce the operator's actual path, not a proxy).

BLAST RADIUS: all six native invocations, not just flyctl. The worst is the `finally`
restore — `git stash pop` throwing there would leave the operator's CleanHead-shielded
WIP stashed while the console showed an unrelated error, the same shape as the R-F3122
false ship-mark.

These tests run the REAL helper extracted from the REAL script under REAL 5.1.
"""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

DEPLOY_PS1 = Path(__file__).resolve().parents[2] / "scripts" / "deploy.ps1"
POWERSHELL_5 = shutil.which("powershell.exe")


def _source() -> str:
    return DEPLOY_PS1.read_text(encoding="utf-8", errors="replace")


def _code_only(text: str) -> str:
    """Strip comment lines.

    A guard that matches its own explanatory comment proves nothing — that has bitten
    this repo twice (R-F3092's slice check, R-F3129's clearance phrase). Assertions
    about what the script DOES must be made against code, not prose.
    """
    return "\n".join(
        line for line in text.splitlines() if not line.strip().startswith("#")
    )


def _extract_invoke_native() -> str:
    """Pull the Invoke-Native function out of the shipped deploy.ps1.

    Extracting (rather than pasting a copy into the test) is the whole point: if
    someone edits the helper in deploy.ps1, THESE tests exercise the edited version.
    """
    src = _source()
    start = src.index("function Invoke-Native {")
    depth, i = 0, src.index("{", start)
    for j in range(i, len(src)):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                return src[start : j + 1]
    raise AssertionError("Invoke-Native braces never balanced")


def test_rf3133_no_raw_native_stderr_pipelines_remain():
    """THE DEFECT ITSELF: a bare `<native> ... 2>&1 |` under $ErrorActionPreference='Stop'."""
    code = _code_only(_source())
    offenders = [
        line.strip()
        for line in code.splitlines()
        if re.search(r"^\s*(flyctl|git|python|npm|node|docker)\b.*2>&1\s*\|", line)
    ]
    assert not offenders, (
        "R-F3133 REGRESSION: native command piping stderr directly again — this "
        "TERMINATES the script under Windows PowerShell 5.1:\n  " + "\n  ".join(offenders)
    )


def test_rf3133_the_flyctl_deploy_call_is_wrapped():
    """The exact line the operator's run died on."""
    code = _code_only(_source())
    m = re.search(r"^.*flyctl deploy --remote-only.*$", code, re.M)
    assert m, "the flyctl deploy invocation vanished"
    assert "Invoke-Native" in m.group(0), (
        "deploy.ps1's flyctl call must go through Invoke-Native")


def test_rf3133_stash_restore_is_wrapped():
    """The `finally` restore is the highest-consequence site: throwing there strands
    the operator's shielded WIP in a stash they were never told about."""
    code = _code_only(_source())
    for cmd in ("git stash pop", "git stash push", "git stash drop"):
        m = re.search(r"^.*" + re.escape(cmd) + r".*$", code, re.M)
        assert m, f"{cmd!r} disappeared from deploy.ps1"
        assert "Invoke-Native" in m.group(0), f"{cmd!r} must go through Invoke-Native"


def test_rf3133_success_is_judged_by_exit_code_not_dollar_question():
    """`$?` reflects the last pipeline's success flag, not the native exit code."""
    code = _code_only(_source())
    assert "if (-not $?)" not in code, (
        "R-F3133: `$?` is not a native exit status — judge by $script:NativeExitCode")
    assert "$script:NativeExitCode" in code


def _ps5(body: str, tmp_path: Path) -> str:
    """Run `body` under real Windows PowerShell 5.1 with the REAL helper in scope."""
    script = tmp_path / "rf3133_case.ps1"
    script.write_text(
        '$ErrorActionPreference = "Stop"\n'
        "$script:NativeExitCode = 0\n"
        + _extract_invoke_native()
        + "\n"
        + body,
        encoding="utf-8",
    )
    proc = subprocess.run(
        [POWERSHELL_5, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    return (proc.stdout or "") + (proc.stderr or "")


@pytest.mark.skipif(not POWERSHELL_5, reason="Windows PowerShell 5.1 not present")
def test_rf3133_capability_old_pattern_really_does_abort(tmp_path):
    """Prove the DEFECT is real under 5.1 — otherwise the fix is cargo-cult.

    `cmd /c "... 1>&2 & exit 0"` is flyctl's exact shape: progress on stderr,
    then a SUCCESSFUL exit.
    """
    out = _ps5(
        'try {\n'
        '  cmd /c "echo progress-line 1>&2 & exit 0" 2>&1 | ForEach-Object { $null = $_ }\n'
        '  Write-Host "RESULT=NO-THROW"\n'
        '} catch { Write-Host "RESULT=THREW" }\n',
        tmp_path,
    )
    assert "RESULT=THREW" in out, (
        "Windows PowerShell 5.1 no longer aborts on native stderr — if this is a real "
        "platform change, R-F3133's wrapper is harmless, but re-read the rationale "
        "before simplifying it away.\n" + out)


@pytest.mark.skipif(not POWERSHELL_5, reason="Windows PowerShell 5.1 not present")
def test_rf3133_capability_invoke_native_survives_stderr_and_keeps_exit_code(tmp_path):
    """THE FIX, on the real path: stderr no longer terminates, output still reaches the
    caller, a genuine non-zero exit is still reported, and 'Stop' is restored after."""
    out = _ps5(
        'try {\n'
        '  $lines = Invoke-Native { cmd /c "echo progress-line 1>&2 & exit 0" }\n'
        '  Write-Host "OK-RC=$script:NativeExitCode"\n'
        '  Write-Host "OK-SAW-OUTPUT=$([bool]($lines -match \'progress-line\'))"\n'
        '} catch { Write-Host "OK-THREW" }\n'
        'try {\n'
        '  Invoke-Native { cmd /c "echo boom 1>&2 & exit 7" } | Out-Null\n'
        '  Write-Host "FAIL-RC=$script:NativeExitCode"\n'
        '} catch { Write-Host "FAIL-THREW" }\n'
        'Write-Host "EAP=$ErrorActionPreference"\n',
        tmp_path,
    )
    assert "OK-RC=0" in out, f"stderr must not abort, and a success must read 0:\n{out}"
    assert "OK-THREW" not in out, f"Invoke-Native still aborted on stderr:\n{out}"
    assert "OK-SAW-OUTPUT=True" in out, (
        f"output must still reach the caller — a silent deploy is its own defect:\n{out}")
    assert "FAIL-RC=7" in out, (
        f"a REAL failure must still surface; swallowing it would recreate the R-F1369 "
        f"'ALL DEPLOYS VERIFIED LIVE over a [FAIL]' banner:\n{out}")
    assert "EAP=Stop" in out, f"$ErrorActionPreference must be restored:\n{out}"
