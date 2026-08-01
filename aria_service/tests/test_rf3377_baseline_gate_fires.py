"""R-F3377 — R-F3373 shipped a §16 gate that had never been seen to fire.

R-F3373 added scripts/admin/suite_baseline.py to make CLAUDE.md §16 ("new
R-numbers must not add to the failing-test count") a machine check instead of a
habit. It was compiled, its --help was run, and real segments were executed — but
the one behaviour that matters, *a new failure produces a non-zero exit*, was
never exercised. The comparison lived inside main(), so reaching it required
running the entire suite.

That is precisely the defect class this session spent the day removing: a guard
whose green tells you nothing because it has never been made to go red. R-F3356's
first draft could not fire; R-F3365's guard matched its own docstrings; R-F3370
found an allowlist entry built on an inverted reading. This closes the same hole
in the tool built to enforce the rule.

The comparison is now a pure function, and these tests drive it directly AND
drive the script end-to-end as a subprocess against a synthetic baseline, so the
exit code — the thing CI or an operator would key off — is proven, not assumed.
"""
from __future__ import annotations

import importlib.util
import json
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "admin" / "suite_baseline.py"


def _load():
    spec = importlib.util.spec_from_file_location("suite_baseline", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def test_rf3377_a_new_failure_is_detected():
    """The gate's whole purpose."""
    sb = _load()
    new, fixed = sb.compare(
        observed=["test_a.py::t1", "test_b.py::t2"],
        known={"test_a.py::t1"},
        complete=True,
    )
    assert new == ["test_b.py::t2"], "a newly failing test must be reported"
    assert fixed == [], "nothing was fixed here"


def test_rf3377_a_flat_count_cannot_hide_a_swap():
    """One test starts failing while another starts passing — the count is
    unchanged, which is exactly why this compares SETS."""
    sb = _load()
    new, fixed = sb.compare(
        observed=["test_b.py::t2"],
        known={"test_a.py::t1"},
        complete=True,
    )
    assert new == ["test_b.py::t2"], "the newly broken test must surface"
    assert fixed == ["test_a.py::t1"], "the newly fixed test must surface"
    assert len(new) == len(fixed), "precondition: the counts are identical, the sets are not"


def test_rf3377_a_partial_run_never_claims_things_were_fixed():
    """Every test a partial run did not execute would otherwise read as fixed.
    New failures must still be reported, or the gate would be silently disabled
    exactly when someone resumes after an interruption."""
    sb = _load()
    new, fixed = sb.compare(
        observed=["test_new.py::t"],
        known={"test_a.py::t1", "test_b.py::t2"},
        complete=False,
    )
    assert fixed is None, "a partial run must not claim anything was fixed"
    assert new == ["test_new.py::t"], "a partial run must STILL report new failures"


def test_rf3377_no_new_failures_is_the_pass_case():
    sb = _load()
    new, fixed = sb.compare(observed=["test_a.py::t1"], known={"test_a.py::t1"}, complete=True)
    assert new == [] and fixed == []


def _run_script(tests_dir: pathlib.Path, baseline: pathlib.Path, *extra: str):
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--tests-dir", str(tests_dir),
         "--baseline", str(baseline), "--segment-size", "5", "--timeout", "60", *extra],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace",
    )


def test_rf3377_the_script_itself_exits_nonzero_on_a_new_failure():
    """Drive the REAL script end to end and assert its EXIT CODE — the thing a CI
    job or an operator actually keys off. Asserting only on the pure function
    would leave the contract that matters unproven, which is how R-F3373 shipped
    a gate nobody had seen fire."""
    with tempfile.TemporaryDirectory() as td:
        tests_dir = pathlib.Path(td)
        (tests_dir / "test_synthetic_fail.py").write_text(
            "def test_synthetic_failure():\n    assert False\n", encoding="utf-8")
        (tests_dir / "test_synthetic_pass.py").write_text(
            "def test_synthetic_pass():\n    assert True\n", encoding="utf-8")

        empty = tests_dir / "empty.json"
        empty.write_text(json.dumps({"failures": []}), encoding="utf-8")
        red = _run_script(tests_dir, empty)
        assert red.returncode == 1, (
            f"a failure absent from the baseline must exit 1, got {red.returncode}: "
            f"{red.stdout[-400:]}"
        )
        assert "NEW FAILURES" in red.stdout

        # ...and the SAME run against a baseline that already knows about it passes.
        known = tests_dir / "known.json"
        known.write_text(json.dumps(
            {"failures": ["test_synthetic_fail.py::test_synthetic_failure"]}), encoding="utf-8")
        green = _run_script(tests_dir, known)
        assert green.returncode == 0, (
            f"a KNOWN failure must not trip the gate, got {green.returncode}: "
            f"{green.stdout[-400:]}"
        )


def test_rf3377_a_truncated_run_refuses_to_overwrite_the_baseline():
    """--record on a partial run would erase every failure in the segments that
    never executed, silently shrinking the baseline to whatever was sampled."""
    with tempfile.TemporaryDirectory() as td:
        tests_dir = pathlib.Path(td)
        (tests_dir / "test_a.py").write_text("def test_a():\n    assert True\n", encoding="utf-8")
        baseline = tests_dir / "b.json"
        baseline.write_text(json.dumps({"failures": []}), encoding="utf-8")
        proc = _run_script(tests_dir, baseline, "--max-segments", "1", "--record")
        assert proc.returncode == 2, f"expected refusal (2), got {proc.returncode}"
        assert "refusing --record" in proc.stdout


def test_rf3377_the_recorded_baseline_is_wellformed():
    """The gate is only as good as the file it compares against."""
    data = json.loads((ROOT / "docs" / "suite_baseline.json").read_text(encoding="utf-8"))
    assert data["failures"], "an empty baseline would mark every known failure as NEW"
    assert len(data["failures"]) == len(set(data["failures"])), "duplicate entries"
    assert data["totals"]["failed"] == len(data["failures"])
    # R-F3632 — the caveat must MATCH THE METHOD, not equal one hardcoded string.
    #
    # This asserted `"FLOOR" in caveat` unconditionally, written when segmented was the
    # only mode. R-F3625 added --single-process, which is the actual §16 measurement and
    # for which FLOOR is FALSE — so recording a correct single-process baseline turned
    # this guard red, and a guard that goes red for doing the right thing gets deleted
    # or, worse, "fixed" by dropping the assertion entirely.
    #
    # Deriving it from `method` keeps the original intent — the caveat travels WITH the
    # number so it cannot be misquoted — and is STRICTER than before: it now also
    # catches a single-process record that wrongly carries the FLOOR caveat, which the
    # old form could not see.
    method = data.get("method", "")
    caveat = data.get("caveat", "")
    if "single" in method.lower():
        assert "FLOOR" not in caveat, (
            "a single-process run SEES order-dependent failures; calling it a FLOOR "
            "understates it and invites a needless re-measure"
        )
        assert "authoritative" in caveat.lower() or "§16" in caveat, (
            "the §16 figure must say so, or a reader cannot tell it from the floor"
        )
    else:
        assert "FLOOR" in caveat, (
            "the segmented-run caveat must travel WITH the number, or it will be "
            "quoted as the §16 baseline it is not"
        )

    # R-F3622/R-F3631 — the validity record must travel with the number too. A baseline
    # with no proof it was measured on a still tree is the thing those R-numbers exist
    # to prevent; asserting the fields here stops a hand-written file passing as one.
    assert data.get("valid") is True, "a recorded baseline must carry valid=True"
    assert data.get("tree_hash"), "a recorded baseline must carry the tree hash"
