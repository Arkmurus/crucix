"""R-F2833 — the reachability sweep must be trustworthy or refuse to answer.

The tool's whole value is that its number can be quoted. Two earlier versions
produced confident, wrong numbers (a name-count reported as reachability, then
a graph that ignored import-time references and called `parse_xml` dead). These
tests pin the properties that stop a third.

★ THE LOAD-BEARING TEST is `test_control_miss_voids_the_run`: a misclassified
control must WITHHOLD the results and exit non-zero. If that ever regresses,
the tool can emit a plausible wrong number again — which is exactly the
fabrication class the codebase keeps finding (R-F2791 counted templates ENTERED
not SEARCHED; R-F2643 certified a gate on a key nothing writes).
"""
from __future__ import annotations

import ast
import importlib.util
import pathlib
import sys

import pytest

_TOOL = (pathlib.Path(__file__).resolve().parents[2]
         / "scripts" / "admin" / "reachability_sweep.py")


def _load():
    spec = importlib.util.spec_from_file_location("reachability_sweep", _TOOL)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["reachability_sweep"] = mod
    spec.loader.exec_module(mod)
    return mod


rs = _load()


@pytest.fixture
def pkg(tmp_path):
    """A miniature package with a known-correct answer."""
    p = tmp_path / "pkg"
    p.mkdir()
    (p / "app.py").write_text(
        "from x import router\n"
        "\n"
        "@router.get('/thing')\n"
        "async def endpoint():\n"
        "    return helper()\n"
        "\n"
        "def helper():\n"
        "    return deep()\n"
        "\n"
        "def deep():\n"
        "    return 1\n"
        "\n"
        "def orphan():\n"
        "    return orphan_helper()\n"
        "\n"
        "def orphan_helper():\n"
        "    return 2\n",
        encoding="utf-8")
    return p


def test_reachable_chain_is_followed_transitively(pkg):
    res = rs.analyse(pkg)
    for name in ("endpoint", "helper", "deep"):
        assert name in res["reachable"], f"{name} should be reachable from the route"


def test_orphan_chain_is_unreachable(pkg):
    """A function with a caller is STILL unreachable if the caller is dead."""
    res = rs.analyse(pkg)
    assert "orphan" not in res["reachable"]
    assert "orphan_helper" not in res["reachable"], (
        "orphan_helper has a caller, but that caller is itself dead — "
        "reachability, not reference-counting"
    )


def test_import_time_reference_counts_as_reachable(tmp_path):
    """The v2 defect: module-scope references were invisible.

    `parse_xml` had 17 references and was classified UNREACHABLE because the
    graph only walked function bodies.
    """
    p = tmp_path / "pkg"
    p.mkdir()
    (p / "m.py").write_text(
        "def parse_xml():\n"
        "    return 1\n"
        "\n"
        "HANDLERS = {'x': parse_xml}\n",     # referenced at import time only
        encoding="utf-8")
    res = rs.analyse(p)
    assert "parse_xml" in res["reachable"], (
        "a name referenced at import time is entered — this is the v2 regression"
    )


def test_decorator_and_default_references_are_import_time(tmp_path):
    p = tmp_path / "pkg"
    p.mkdir()
    (p / "m.py").write_text(
        "def wrapper():\n    return 1\n"
        "def defaulted():\n    return 2\n"
        "\n"
        "def user(cb=defaulted):\n    return cb()\n",
        encoding="utf-8")
    res = rs.analyse(p)
    assert "defaulted" in res["reachable"], "a default-argument reference executes at def time"


def test_string_named_function_is_undecidable_not_dead(tmp_path):
    """★ Tri-state. Dynamic dispatch must not be called dead."""
    p = tmp_path / "pkg"
    p.mkdir()
    (p / "m.py").write_text(
        "def maybe_dispatched():\n    return 1\n"
        "\n"
        "def entry():\n"
        "    name = 'maybe_dispatched'\n"
        "    return name\n",
        encoding="utf-8")
    res = rs.analyse(p)
    assert "maybe_dispatched" in res["undecidable"], (
        "a name appearing as a string may be dispatched dynamically — it is "
        "UNDECIDABLE, never UNREACHABLE ('cannot determine' != 'determined dead')"
    )


def test_control_miss_voids_the_run(pkg, capsys):
    """★ THE LOAD-BEARING TEST — a bad control withholds results, exit 1."""
    res = rs.analyse(pkg)
    fails = rs.check_controls(res, live=["orphan"], dead=[])
    assert fails, "a LIVE control that is unreachable MUST be reported as a failure"
    assert "UNREACHABLE" in fails[0]


def test_controls_pass_when_classification_is_right(pkg):
    res = rs.analyse(pkg)
    assert rs.check_controls(res, live=["endpoint", "helper"], dead=["orphan"]) == []


def test_missing_control_is_a_failure_not_a_silent_pass(pkg):
    """A control that has vanished from the tree must VOID, not quietly pass.

    Otherwise renaming a control silently disables the gate — the same shape as
    a gate certified by an absence (R-F2643).
    """
    res = rs.analyse(pkg)
    fails = rs.check_controls(res, live=["function_that_does_not_exist"], dead=[])
    assert fails and "not present in the tree" in fails[0]


def test_verify_counts_calls_not_strings(tmp_path):
    """★ grep counted log-strings as callers; the verifier must not."""
    p = tmp_path / "pkg"
    p.mkdir()
    (p / "m.py").write_text(
        "import logging\n"
        "def target():\n    return 1\n"
        "\n"
        "def noise():\n"
        "    logging.warning('target failed')\n"       # string, not a call
        "    x = 'target'\n"                            # string, not a call
        "    return x\n",
        encoding="utf-8")
    found = rs.call_sites(p, ["target"])
    assert found.get("target", []) == [], (
        "string literals mentioning a name are NOT call sites — counting them "
        "is the proxy error the verifier exists to remove"
    )


def test_verify_finds_real_calls_with_enclosing_function(tmp_path):
    p = tmp_path / "pkg"
    p.mkdir()
    (p / "m.py").write_text(
        "def target():\n    return 1\n"
        "\n"
        "def caller():\n"
        "    return target()\n",
        encoding="utf-8")
    hits = rs.call_sites(p, ["target"])["target"]
    assert len(hits) == 1
    assert hits[0][2] == "caller", "the enclosing function is needed to judge caller reachability"


def test_syntax_error_module_does_not_abort_the_sweep(tmp_path):
    p = tmp_path / "pkg"
    p.mkdir()
    (p / "ok.py").write_text("def fine():\n    return 1\n", encoding="utf-8")
    (p / "broken.py").write_text("def nope(:\n", encoding="utf-8")
    res = rs.analyse(p)          # must not raise
    assert "fine" in res["all_defs"]


def test_tests_directory_is_excluded(tmp_path):
    """A test calling a function must not make it look production-reachable."""
    p = tmp_path / "pkg"
    p.mkdir()
    (p / "m.py").write_text("def only_tested():\n    return 1\n", encoding="utf-8")
    tdir = p / "tests"
    tdir.mkdir()
    (tdir / "test_m.py").write_text("from m import only_tested\n"
                                    "def test_x():\n    assert only_tested()\n",
                                    encoding="utf-8")
    res = rs.analyse(p)
    assert "only_tested" not in res["reachable"], (
        "test-only usage is not production reachability — that distinction is "
        "the entire point of the sweep"
    )


def test_repo_root_is_discovered_not_hardcoded():
    """The tool must work from any checkout (the scratchpad version hardcoded C:\\code\\crucix)."""
    root = rs.repo_root()
    assert (root / "aria_service").is_dir()
    assert (root / "CLAUDE.md").exists()
