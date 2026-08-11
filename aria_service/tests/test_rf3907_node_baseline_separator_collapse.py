r"""R-F3907 — R-F3905 normalised one character at a time; the baseline holds two.

R-F3905 collapsed the Windows/Linux separator mismatch with
`.replace(/\\/g, '/')`. That is correct for ONE backslash and wrong for what the
baseline actually contains: the recorded entries carry TWO —

    "test\\intel-value-chain-rf3536.test.mjs"

because the TAP name was captured already-escaped. Replacing each character yielded
`test//intel-...`, which still did not match the Linux run's `test/intel-...`, so
the fix reported the identical phantom NEW/FIXED pair it was written to remove,
merely spelled differently. CI caught it in one run.

WHY MY OWN TEST DID NOT. `test_rf3905` asserted the property on a string I wrote by
hand (`test\intel-...`, one backslash) instead of the string the baseline actually
holds. A test built from an assumed input can only ever confirm the assumption —
the same "asserted against a paraphrase, not the real body" failure R-F3868's
classify_429 defect was caught by, and this file exists to not repeat it: every
assertion below reads the REAL recorded entry.

`[\\/]+` collapses one backslash, two, and a stray double slash, and is idempotent,
so it cannot drift again however the name was captured.
"""
from __future__ import annotations

import json
import re
import subprocess

from aria_service.tests._source_probe import repo_path

_GATE = repo_path("scripts/admin/node_suite_baseline.mjs")
_BASELINE = repo_path("docs/node_suite_baseline.json")


def _normalise(name: str) -> str:
    """Mirror of the gate's normalisePath, for asserting against real data."""
    return re.sub(r"[\\/]+", "/", name)


def _recorded_paths() -> list[str]:
    data = json.loads(_BASELINE.read_text(encoding="utf-8"))
    return [f for f in (data.get("failures") or []) if ".test.mjs" in f]


def test_the_real_recorded_entries_normalise_to_the_linux_spelling():
    """THE ASSERTION THAT WAS MISSING. Reads the actual baseline, not a hand-written
    stand-in — the doubled backslash only exists in the real file."""
    recorded = _recorded_paths()
    assert recorded, "the baseline no longer records any file-level failures"
    for entry in recorded:
        norm = _normalise(entry)
        assert "\\" not in norm, f"a backslash survived normalisation: {norm!r}"
        assert "//" not in norm, (
            f"normalisation produced a DOUBLE slash from {entry!r} -> {norm!r}; that "
            f"is the R-F3907 defect — collapse runs of separators, not single chars")
        assert norm.startswith("test/")


def test_normalisation_is_idempotent():
    """A second pass must be a no-op, or the result depends on how many times it ran."""
    for entry in _recorded_paths() + [r"test\\x.test.mjs", "test//x.test.mjs",
                                      r"test\x.test.mjs", "test/x.test.mjs"]:
        assert _normalise(_normalise(entry)) == _normalise(entry)


def test_every_windows_spelling_collapses_to_one_linux_name():
    """One test, four spellings, one identity — the property in full."""
    spellings = [r"test\x.test.mjs", "test\\\\x.test.mjs".replace("\\\\", "\\\\"),
                 "test//x.test.mjs", "test/x.test.mjs"]
    assert len({_normalise(s) for s in spellings}) == 1, {s: _normalise(s) for s in spellings}


def test_the_gate_collapses_runs_not_single_characters():
    src = _GATE.read_text(encoding="utf-8")
    assert "function normalisePath" in src, (
        "normalisation must be ONE named function used by both sides, or the two "
        "comparisons can drift apart")
    assert re.search(r"replace\(/\[\\\\/\]\+/g, '/'\)", src), (
        r"normalisePath must collapse [\\/]+ — a per-character replace turns the "
        r"baseline's doubled backslash into a double slash (R-F3907)")
    # Both sides must go through it.
    assert "failures.add(normalisePath(" in src
    assert "map(normalisePath)" in src


def test_the_gate_still_parses():
    chk = subprocess.run(["node", "--check", str(_GATE)], capture_output=True,
                         text=True, timeout=60)
    assert chk.returncode == 0, chk.stderr[-500:]
