"""R-F3378 — a CI step that runs the tests and cannot fail.

`.github/workflows/ci.yml` ran:

    python -m pytest aria_service/tests/ -v --tb=short -q 2>&1 | tail -50

A bash pipeline exits with the status of its LAST command. `tail` always
succeeds, so pytest's exit code was discarded: no test failure could ever fail
that step. It read as "CI runs the suite" while being structurally incapable of
reporting a result — and it truncated the evidence to the last 50 lines on the
way past.

That is the same shape as everything else found on 2026-07-28: a guard that
cannot fire. It is why CLAUDE.md §16's baseline could drift ~3x stale for two
months with a green pipeline the whole time.

Note the step ALSO cannot pass honestly: CI installs pytest/pytest-asyncio/httpx
plus requirements-dev.txt, which carries only pytest, pytest-asyncio,
pytest-timeout and pyflakes. No torch, no chromadb, not even fastapi — most of
the suite errors on import there. So R-F3378 does not make it blocking (that
would turn CI red on missing dependencies rather than on regressions, which is a
cry-wolf gate); it removes the swallow so the true result is visible, and marks
the step advisory EXPLICITLY rather than by accident.

This test stops the pattern returning anywhere in the workflows.
"""
from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"

# `<something> pytest ... | <anything>` — the pipe is what discards the status.
_PIPED_PYTEST = re.compile(r"pytest[^\n|]*\|")


def _workflow_files() -> list[pathlib.Path]:
    return sorted(list(WORKFLOWS.glob("*.yml")) + list(WORKFLOWS.glob("*.yaml")))


def test_rf3378_no_workflow_pipes_pytest_into_another_command():
    """A piped pytest reports the PIPE's exit status, never pytest's."""
    assert _workflow_files(), "no workflows found — this guard would pass vacuously"
    offenders: dict[str, list[str]] = {}
    for wf in _workflow_files():
        hits = [
            line.strip()
            for line in wf.read_text(encoding="utf-8", errors="replace").splitlines()
            if _PIPED_PYTEST.search(line) and not line.lstrip().startswith("#")
        ]
        if hits:
            offenders[wf.name] = hits
    assert offenders == {}, (
        "these CI steps pipe pytest into another command, so the pipeline's exit "
        "status is the LAST command's and test failures are silently discarded: "
        f"{offenders}. Drop the pipe (the log keeps the full output anyway), or "
        "set `set -o pipefail` first if a pipe is genuinely needed."
    )


def test_rf3378_the_guard_can_actually_fire():
    """Prove the instrument on the exact line that was removed, rather than
    trusting a green result from a pattern that might match nothing."""
    assert _PIPED_PYTEST.search(
        "          python -m pytest aria_service/tests/ -v --tb=short -q 2>&1 | tail -50"
    ), "the guard must match the line R-F3378 removed"
    assert not _PIPED_PYTEST.search(
        "          python -m pytest aria_service/tests/ -v --tb=short -q"
    ), "the guard must not flag an unpiped invocation"


def test_rf3378_a_non_blocking_test_step_says_so_out_loud():
    """If a test step cannot gate, that must be DECLARED. An accidentally
    non-blocking step is indistinguishable from a passing one."""
    ci = (WORKFLOWS / "ci.yml").read_text(encoding="utf-8", errors="replace")
    assert "pytest" in ci, "precondition: ci.yml still runs pytest"
    # The pytest step must either be able to fail, or be explicitly advisory.
    assert "continue-on-error: true" in ci and "ADVISORY" in ci, (
        "ci.yml's pytest step is neither blocking nor declared advisory — a "
        "reader cannot tell whether a green CI means the suite passed"
    )
