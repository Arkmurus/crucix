"""R-F3903 — the Node suite gate refused correctly, and silently, for 8+ commits.

`node scripts/admin/node_suite_baseline.mjs` failed in CI on EIGHT consecutive
commits — both agents' work — emitting exactly one line:

    [node-baseline] could not parse TAP totals — refusing to record or gate

and nothing else. Meanwhile the same command passes locally: 1833 passed / 8 failed,
exit 0. So the suite behaves differently under CI's pinned Node 20 than under a dev
Node 22, and the gate printed nothing that could distinguish "the runner crashed
before emitting a TAP summary" from "the TAP format changed".

REFUSING IS RIGHT. Refusing in silence is not. A guard that cannot say WHY it could
not measure is one nobody can act on, so it stays red until somebody mutes it — the
failure mode every allowlist and gate in this repo is written against, and the same
absence-that-explains-nothing shape as C-23/C-25.

This does NOT claim to fix the CI failure: the cause is not yet known, and inventing
one would be worse than the silence. It makes the next CI run SAY what it saw.
"""
from __future__ import annotations

import subprocess

from aria_service.tests._source_probe import repo_path

_GATE = repo_path("scripts/admin/node_suite_baseline.mjs")


def test_the_refusal_branch_reports_what_it_saw():
    src = _GATE.read_text(encoding="utf-8")
    idx = src.index("could not parse TAP totals")
    branch = src[idx: idx + 900]
    assert "lastRawOutput.length" in branch, (
        "the refusal must report how many bytes it captured — zero bytes and "
        "unparseable bytes are different failures with different fixes")
    assert "tail" in branch, "it must print the tail of what it actually received"
    assert "did not start" in branch, (
        "a completely empty capture must say so explicitly, rather than leaving the "
        "reader to infer it from a byte count")


def test_the_captured_output_is_actually_populated():
    """The diagnostic is worthless if nothing ever fills the buffer."""
    src = _GATE.read_text(encoding="utf-8")
    assert "lastRawOutput = stdout;" in src, (
        "runSuite must record its raw output, or the refusal branch prints nothing")


def test_the_tail_is_bounded():
    """`maxBuffer` is 64MB. An unbounded dump would flood the CI log and get the
    gate muted for a different reason."""
    src = _GATE.read_text(encoding="utf-8")
    assert "slice(-40)" in src and "slice(0, 300)" in src, (
        "the diagnostic must cap both line count and line length")


def test_the_gate_still_parses_and_runs():
    """CAPABILITY TEST — the change must not break the gate it instruments.
    Bounded under the per-test budget (R-F3459)."""
    chk = subprocess.run(["node", "--check", str(_GATE)], capture_output=True,
                         text=True, timeout=60)
    assert chk.returncode == 0, f"the gate no longer parses:\n{chk.stderr[-600:]}"
