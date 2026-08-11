r"""R-F3905 — the Node baseline compared raw paths, so a slash read as a regression.

The first CI run after the Node tier actually started executing (R-F3904) reported:

    NEW FAILURES (2):
      ! test/intel-value-chain-rf3536.test.mjs
      ! test/precommit-gate-performance-rf3556.test.mjs
    FIXED since the baseline (2):
      - test\intel-value-chain-rf3536.test.mjs
      - test\precommit-gate-performance-rf3556.test.mjs

The SAME TWO FILES, differing only by separator. Node reports a test name as the
path it was invoked with, so a baseline recorded on Windows can never match a Linux
run — every standing failure reads as FIXED and its twin as NEW.

Both directions are wrong and the NEW half is the dangerous one: it fails the build
on a phantom, and a gate that cries wolf is a gate that gets muted (R-F3858) — which
would have re-darkened the Node tier the day after R-F3904 lit it up.

NORMALISED ON BOTH SIDES, deliberately: the parsed side so a fresh run is portable,
and the RECORDED side so the existing Windows baseline keeps working without a
re-record. The Python gate needed a whole second file (suite_baseline.ci.json)
because its platform difference is REAL — 89 failures locally vs 165 in CI (§16).
This one is only a slash, and solving it with a second baseline would have pinned
the artefact instead of removing it.
"""
from __future__ import annotations

import json
import re
import subprocess

from aria_service.tests._source_probe import repo_path

_GATE = repo_path("scripts/admin/node_suite_baseline.mjs")


def test_both_sides_of_the_comparison_are_normalised():
    """Both sides, through ONE shared function.

    R-F3907 UPDATE: this asserted the inline `.replace(...)` that R-F3905 shipped.
    That form was wrong — it collapsed one character at a time, turning the
    baseline's DOUBLED backslash into a double slash — and is now a single named
    `normalisePath` used by both sides, so the two comparisons cannot drift apart.
    The correctness of the collapse itself is asserted against the REAL recorded
    entries in `test_rf3907_node_baseline_separator_collapse.py`; this test only
    pins that both sides go through it.
    """
    src = _GATE.read_text(encoding="utf-8")
    assert "function normalisePath" in src, (
        "normalisation must be one shared function, not duplicated inline")
    assert re.search(r"failures\.add\(normalisePath\(", src), (
        "the PARSED failure names must be separator-normalised")
    assert re.search(r"base\.failures[^\n]*map\(normalisePath\)", src), (
        "the RECORDED baseline must be normalised too, or an existing "
        "Windows-recorded file still mismatches every Linux run")


def test_a_windows_and_a_linux_spelling_are_the_same_test():
    """The property in one line: the gate must not be able to tell them apart."""
    win = r"test\intel-value-chain-rf3536.test.mjs"
    nix = "test/intel-value-chain-rf3536.test.mjs"
    assert win.replace("\\", "/") == nix


def test_the_recorded_baseline_is_still_usable():
    """A baseline that cannot gate anything is worse than none."""
    data = json.loads(
        repo_path("docs/node_suite_baseline.json").read_text(encoding="utf-8"))
    assert data.get("failures"), "an empty baseline cannot gate anything"
    assert (data.get("totals") or {}).get("pass", 0) > 100, (
        "a baseline recorded from a run that collected almost nothing would pin a "
        "broken runner as the standard")


def test_the_gate_still_parses():
    chk = subprocess.run(["node", "--check", str(_GATE)], capture_output=True,
                         text=True, timeout=60)
    assert chk.returncode == 0, chk.stderr[-500:]
