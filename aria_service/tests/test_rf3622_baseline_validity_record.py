"""R-F3622 — a suite baseline must carry a validity record, and a corrupted run
must not be recordable.

WHY THIS EXISTS
---------------
`docs/suite_baseline.md` states that its authoritative 2026-08-01 figure was
"measured by `scratchpad/measure.py`, which snapshots a SHA-256 over every tracked
aria_service/**/*.py before and after the run and prints VALID=YES|NO".

That file does not exist. It was written into a session scratchpad and went with the
session. So the one number the repo treats as authoritative could not be reproduced
by anyone, and the check that made it trustworthy was not part of
`scripts/admin/suite_baseline.py` — the committed tool that actually records
baselines. That tool would happily `--record` a run corrupted mid-flight by a peer
commit.

The corruption is real (R-F3597): `inspect.getsource` slices the file from disk using
line numbers captured at IMPORT, so a mid-run commit makes it return a DIFFERENT
function's body — silently, because the wrong slice is still valid Python. Two
attempts at the 2026-08-01 baseline were destroyed that way, reading 147 and 110 for
a suite whose real figure was ~110 throughout.

These tests pin the three properties that make the number trustworthy: the hash is
content-addressed, VALID is reported on every run, and `--record` REFUSES when the
tree moved.
"""

from __future__ import annotations

import importlib.util
import inspect
import json
import pathlib
import subprocess
import sys

import pytest

# R-F3770/§16 — NOT inspect.getsource: it slices at line numbers captured
# AT IMPORT, so an edit mid-run silently returns a DIFFERENT function's body.
from ._source_probe import function_source

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "admin" / "suite_baseline.py"


def _load():
    spec = importlib.util.spec_from_file_location("_suite_baseline_rf3622", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_script_the_doc_points_at_actually_exists():
    # The whole defect: docs/suite_baseline.md cited a tool that had been deleted
    # with its session. Whatever the doc names must be a committed, runnable file.
    assert SCRIPT.exists(), "the baseline recorder must be committed, not session-local"
    doc = (ROOT / "docs" / "suite_baseline.md").read_text(encoding="utf-8", errors="replace")
    assert "scripts/admin/suite_baseline.py" in doc, (
        "docs/suite_baseline.md must point at the committed recorder, not a scratchpad path"
    )


def test_tree_hash_is_stable_and_content_addressed(tmp_path):
    mod = _load()
    first = mod.tree_hash()
    assert first == mod.tree_hash(), "two reads of an unchanged tree must agree"
    assert len(first) == 16 and all(c in "0123456789abcdef" for c in first)


def test_tree_hash_changes_when_a_tracked_file_changes():
    # The property that makes VALID meaningful. Mutate a tracked file, hash, restore.
    mod = _load()
    target = ROOT / "aria_service" / "intel" / "golden_intel_bridge.py"
    original = target.read_bytes()
    before = mod.tree_hash()
    try:
        target.write_bytes(original + b"\n# rf3622 transient probe\n")
        assert mod.tree_hash() != before, (
            "a mid-run edit to the code under test MUST invalidate the run — this is "
            "exactly the R-F3597 corruption the check exists to catch"
        )
    finally:
        target.write_bytes(original)
    assert mod.tree_hash() == before, "the probe must leave no trace"


def test_untracked_files_do_not_invalidate_a_run(tmp_path):
    # A scratch file or a peer's untracked WIP elsewhere must not make every run
    # read as corrupted — a check that always fires gets switched off.
    mod = _load()
    before = mod.tree_hash()
    scratch = ROOT / "aria_service" / "intel" / "_rf3622_untracked_probe.py"
    try:
        scratch.write_text("# untracked\n", encoding="utf-8")
        assert mod.tree_hash() == before
    finally:
        scratch.unlink(missing_ok=True)


@pytest.mark.parametrize("flag", ["--record"])
def test_record_refuses_when_the_tree_moved(tmp_path, flag):
    """`--record` on a moved tree must exit 2 and write nothing.

    Driven as a subprocess against a FIXTURE tests dir (the script supports
    --tests-dir/--baseline for exactly this), so the gate is exercised without
    running the real suite. The tree is moved by touching a tracked file while the
    fixture segment runs.
    """
    tests_dir = tmp_path / "t"
    tests_dir.mkdir()
    target = ROOT / "aria_service" / "intel" / "golden_intel_bridge.py"
    original = target.read_bytes()
    # A fixture test whose act of running mutates a TRACKED file — i.e. the tree
    # moves between the before- and after-snapshot, exactly as a peer commit does.
    (tests_dir / "test_moves_the_tree.py").write_text(
        "import pathlib\n"
        f"TARGET = pathlib.Path(r'{target}')\n"
        "def test_touch():\n"
        "    TARGET.write_bytes(TARGET.read_bytes() + b'\\n# rf3622 mid-run\\n')\n"
        "    assert True\n",
        encoding="utf-8",
    )
    baseline = tmp_path / "baseline.json"

    try:
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), flag, "--tests-dir", str(tests_dir),
             "--baseline", str(baseline), "--segment-size", "5", "--timeout", "30"],
            cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
    finally:
        target.write_bytes(original)

    out = proc.stdout + proc.stderr
    assert "VALID=NO" in out, out[-1500:]
    assert proc.returncode == 2, f"expected refusal exit 2, got {proc.returncode}\n{out[-1500:]}"
    assert not baseline.exists(), "a baseline must NOT be written from a corrupted run"


def test_valid_is_reported_on_a_clean_run(tmp_path):
    """A quiet tree reports VALID=YES, and --record stamps it into the file.

    Without this the refusal above could pass vacuously by always reporting NO.
    """
    tests_dir = tmp_path / "t"
    tests_dir.mkdir()
    (tests_dir / "test_quiet.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    baseline = tmp_path / "baseline.json"

    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--record", "--tests-dir", str(tests_dir),
         "--baseline", str(baseline), "--segment-size", "5", "--timeout", "30"],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    out = proc.stdout + proc.stderr
    assert "VALID=YES" in out, out[-1500:]
    assert proc.returncode == 0, out[-1500:]

    # R-F3622 — --record must write to --baseline. It used to write the module
    # constant regardless, so exercising the record path against a fixture would
    # overwrite docs/suite_baseline.json. Assert the fixture got it and the real
    # baseline was left alone.
    assert baseline.exists(), "--record must honour --baseline"
    recorded = json.loads(baseline.read_text(encoding="utf-8"))
    assert recorded.get("valid") is True, "the validity record must travel with the number"
    assert recorded.get("tree_hash"), "the tree hash must be recorded so it can be re-checked"


def test_record_does_not_clobber_the_real_baseline(tmp_path):
    real = ROOT / "docs" / "suite_baseline.json"
    before = real.read_bytes() if real.exists() else None
    tests_dir = tmp_path / "t"
    tests_dir.mkdir()
    (tests_dir / "test_quiet.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    subprocess.run(
        [sys.executable, str(SCRIPT), "--record", "--tests-dir", str(tests_dir),
         "--baseline", str(tmp_path / "fixture.json"), "--segment-size", "5", "--timeout", "30"],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    after = real.read_bytes() if real.exists() else None
    assert after == before, "recording against a fixture must not touch the real baseline"


def test_an_invalid_run_emits_no_section_16_verdict(tmp_path):
    """R-F3624 — VALID=NO must suppress the DIFF, not just the record.

    Observed on the harness's first real use (2026-08-01): it printed "DISCARD this
    result — do not publish it and do not diff it" and then diffed it anyway, emitting
    "NEW FAILURES (20) — CLAUDE.md section 16" and exiting 1. A gate verdict computed
    from data the tool has just declared invalid is the same plausible-looking wrong
    answer R-F3597 is about, one layer up — someone would go hunting twenty regressions
    that may not exist.

    Exit 3 keeps "the measurement failed" distinct from "the code failed" (exit 1).
    Collapsing those two is how a broken measurement gets actioned as a regression.
    """
    tests_dir = tmp_path / "t"
    tests_dir.mkdir()
    target = ROOT / "aria_service" / "intel" / "golden_intel_bridge.py"
    original = target.read_bytes()
    # This fixture BOTH moves the tree and produces a genuine new failure, so the test
    # proves the verdict is suppressed even when there is a real one to report.
    (tests_dir / "test_moves_and_fails.py").write_text(
        "import pathlib\n"
        f"TARGET = pathlib.Path(r'{target}')\n"
        "def test_touch():\n"
        "    TARGET.write_bytes(TARGET.read_bytes() + b'# rf3624 mid-run\\n')\n"
        "def test_fails():\n"
        "    assert False\n",
        encoding="utf-8",
    )
    baseline = tmp_path / "baseline.json"
    baseline.write_text('{"failures": []}', encoding="utf-8")

    try:
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), "--tests-dir", str(tests_dir),
             "--baseline", str(baseline), "--segment-size", "5", "--timeout", "30"],
            cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
    finally:
        target.write_bytes(original)

    out = proc.stdout + proc.stderr
    assert "VALID=NO" in out, out[-1200:]
    assert proc.returncode == 3, (
        f"expected 3 (measurement invalid), got {proc.returncode} — 1 would mean the "
        f"corrupted run was reported as a real regression\n{out[-1200:]}"
    )
    assert "NEW FAILURES" not in out, "an invalid run must emit no section-16 verdict"
    assert "FIXED since" not in out


def test_a_valid_run_still_reports_real_new_failures(tmp_path):
    """The suppression above must not disable the gate itself.

    Without this, returning 3 unconditionally would pass the test above while silently
    switching §16 off — the failure mode this repo has hit before (a guard that cries
    wolf gets disabled; a guard that never fires is worse).
    """
    tests_dir = tmp_path / "t"
    tests_dir.mkdir()
    (tests_dir / "test_real_regression.py").write_text(
        "def test_ok():\n    assert True\n\ndef test_regressed():\n    assert False\n",
        encoding="utf-8",
    )
    baseline = tmp_path / "baseline.json"
    baseline.write_text('{"failures": []}', encoding="utf-8")

    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--tests-dir", str(tests_dir),
         "--baseline", str(baseline), "--segment-size", "5", "--timeout", "30"],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    out = proc.stdout + proc.stderr
    assert "VALID=YES" in out, out[-1200:]
    assert proc.returncode == 1, f"a real new failure on a VALID run must still fail the gate\n{out[-1200:]}"
    assert "NEW FAILURES" in out


# ── R-F3625: the tool must be able to produce the number the doc publishes ───

def test_single_process_mode_exists_and_is_the_section_16_measurement(tmp_path):
    """The committed tool could only produce a segmented FLOOR, while CLAUDE.md §16 and
    docs/suite_baseline.md quote the SINGLE-PROCESS figure. So the authoritative number
    was not reproducible by the authoritative tool — the same shape as R-F3622, in a
    different place: the tool measured a different thing than the doc published.
    """
    tests_dir = tmp_path / "t"
    tests_dir.mkdir()
    (tests_dir / "test_a.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    (tests_dir / "test_b.py").write_text("def test_bad():\n    assert False\n", encoding="utf-8")
    baseline = tmp_path / "baseline.json"

    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--single-process", "--record",
         "--tests-dir", str(tests_dir), "--baseline", str(baseline), "--timeout", "30"],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    out = proc.stdout + proc.stderr
    assert "ONE pytest process" in out, out[-1200:]
    assert "VALID=YES" in out, out[-1200:]
    assert proc.returncode == 0, out[-1200:]

    rec = json.loads(baseline.read_text(encoding="utf-8"))
    assert rec["totals"]["failed"] == 1 and rec["totals"]["passed"] == 1, rec["totals"]
    assert "test_b.py::test_bad" in rec["failures"], rec["failures"]
    # The recorded metadata must describe the run that actually happened. Recording a
    # single-process measurement under the segmented FLOOR caveat would understate its
    # authority and invite someone to re-measure a number that was already correct.
    assert "single pytest process" in rec["method"], rec["method"]
    assert "FLOOR" not in rec["caveat"], rec["caveat"]


def test_segmented_mode_still_labels_itself_a_floor(tmp_path):
    """The counterpart: a segmented record must KEEP the caveat, or its number gets
    quoted as the §16 figure it cannot be."""
    tests_dir = tmp_path / "t"
    tests_dir.mkdir()
    (tests_dir / "test_a.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    baseline = tmp_path / "baseline.json"

    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--record", "--tests-dir", str(tests_dir),
         "--baseline", str(baseline), "--segment-size", "5", "--timeout", "30"],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    assert proc.returncode == 0, (proc.stdout + proc.stderr)[-1200:]
    rec = json.loads(baseline.read_text(encoding="utf-8"))
    assert "FLOOR" in rec["caveat"]
    assert "segments" in rec["method"]


def test_a_dead_single_process_run_is_not_reported_as_zero_failures():
    """An external kill produces no pytest summary. Parsing that as 0 failed / 0 passed
    and publishing it would be a fabricated clean run — the exact never-false-clean rule
    this repo applies to its intel sources, applied to its own measurement.
    """
    mod = _load()
    src = function_source(mod, "_run_single_process")
    assert "hung" in src
    # The guard: no 'passed' AND no 'failed' in the output => hung, not clean.
    assert '"passed" not in out' in src and '"failed" not in out' in src, src[-400:]
