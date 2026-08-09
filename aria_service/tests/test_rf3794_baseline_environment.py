"""R-F3794 — a suite baseline must record the ENVIRONMENT it was measured on.

WHY THIS EXISTS
---------------
A baseline diff answers "which tests newly fail". Every reader has taken that to
mean "which commits broke something". It does not: the failure set is a function of
the CODE and the ENVIRONMENT, and only the first was ever recorded.

Measured 2026-08-08 — diffing a valid 126-failure run against the 103-failure
2026-08-01 baseline produced "36 new failures". At least five were caused by no
commit at all: this box's venv was rebuilt on 2026-08-03, and the FastAPI it
resolved had changed `include_router` so that `app.routes` no longer enumerates
(R-F3791; defects.md C-12). Read as regressions, those five would have sent someone
hunting a diff that does not exist.

C-01 predicted this ("a bump can move the baseline with no commit at all") and
pinning did not fix it. Pinning makes a set REPRODUCIBLE; this makes a shift
LEGIBLE. Both are needed, and only the second one prevents the misattribution.

The contract has two halves, and the second is the one that makes it a warning
rather than a decoration: drift must be reported (1-4), and an ABSENT fingerprint
must also be reported rather than passing as "unchanged" (5).
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "admin" / "suite_baseline.py"


def _load():
    spec = importlib.util.spec_from_file_location("_suite_baseline_rf3794", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def mod():
    return _load()


# ── the fingerprint itself ───────────────────────────────────────────────────

def test_the_fingerprint_identifies_the_running_interpreter(mod):
    fp = mod.environment_fingerprint()

    assert fp["python"] == sys.version.split()[0]
    assert fp["packages_sha256"], "a real fingerprint must be produced on this box"
    assert len(fp["packages_sha256"]) == 16
    assert fp["packages"] > 0


def test_the_fingerprint_is_stable_across_calls(mod):
    """A hash that moved on its own would flag drift on every single run and be
    muted within a week."""
    assert (mod.environment_fingerprint()["packages_sha256"]
            == mod.environment_fingerprint()["packages_sha256"])


def test_the_fingerprint_names_the_package_that_actually_moved_the_baseline(mod):
    """fastapi is the package that caused C-12, so it must be readable at a glance
    rather than buried inside an opaque hash."""
    import fastapi

    assert mod.environment_fingerprint()["key_packages"]["fastapi"] == fastapi.__version__


def test_pip_ordering_does_not_change_the_hash(mod, monkeypatch):
    """pip freeze ordering is not guaranteed; an order-sensitive hash would report
    drift that did not happen."""
    def _freeze(order):
        class R:
            stdout = "\n".join(order)
        return lambda *a, **k: R()

    pkgs = ["alpha==1.0", "beta==2.0", "gamma==3.0"]
    monkeypatch.setattr(mod.subprocess, "run", _freeze(pkgs))
    first = mod.environment_fingerprint()["packages_sha256"]
    monkeypatch.setattr(mod.subprocess, "run", _freeze(list(reversed(pkgs))))
    assert mod.environment_fingerprint()["packages_sha256"] == first


def test_editable_installs_are_excluded(mod, monkeypatch):
    """An `-e` line embeds an absolute checkout path, which would make the hash
    machine-specific and useless for comparing two runs."""
    class R:
        stdout = "alpha==1.0\n-e git+ssh://x#egg=aria\nbeta==2.0\n"

    monkeypatch.setattr(mod.subprocess, "run", lambda *a, **k: R())
    fp = mod.environment_fingerprint()
    assert fp["packages"] == 2


def test_a_failed_freeze_records_absence_not_a_value(mod, monkeypatch):
    """The honesty half: if the freeze cannot run, say so. A fabricated or empty
    hash would compare equal to another failed run and certify 'no drift'."""
    def _boom(*a, **k):
        raise OSError("pip is gone")

    monkeypatch.setattr(mod.subprocess, "run", _boom)
    fp = mod.environment_fingerprint()
    assert fp["packages_sha256"] is None
    assert "pip is gone" in fp["error"]


# ── the drift report ─────────────────────────────────────────────────────────

def test_identical_environments_report_nothing(mod):
    env = {"python": "3.13.14", "packages_sha256": "abc123", "key_packages": {}}
    assert mod.environment_drift_report(env, dict(env)) == []


def test_a_changed_package_set_is_reported(mod):
    base = {"python": "3.14.3", "packages_sha256": "aaaa",
            "key_packages": {"fastapi": "0.115.0"}}
    now = {"python": "3.13.14", "packages_sha256": "bbbb",
           "key_packages": {"fastapi": "0.141.1"}}

    report = "\n".join(mod.environment_drift_report(base, now))
    assert "ENVIRONMENT CHANGED" in report
    assert "fastapi: 0.115.0 -> 0.141.1" in report
    assert "3.14.3 -> 3.13.14" in report
    # The point of the warning: stop the reader attributing it to a commit.
    assert "not a code regression" in report


def test_a_missing_baseline_fingerprint_still_warns(mod):
    """The half that keeps this honest. An un-fingerprinted baseline cannot prove
    the environment held, so it must not read as 'unchanged'."""
    now = {"python": "3.13.14", "packages_sha256": "bbbb", "key_packages": {}}

    for absent in (None, {}, {"python": "3.13.14"}):
        report = "\n".join(mod.environment_drift_report(absent, now))
        assert "CANNOT be ruled out" in report, f"silent for {absent!r}"


def test_the_current_committed_baseline_is_flagged_as_unfingerprinted(mod):
    """Capability, against the real file: docs/suite_baseline.json was recorded
    before R-F3794, so anyone diffing against it today must be told so."""
    import json

    doc = json.loads((ROOT / "docs" / "suite_baseline.json").read_text(encoding="utf-8"))
    report = mod.environment_drift_report(doc.get("environment"),
                                          mod.environment_fingerprint())
    if not (doc.get("environment") or {}).get("packages_sha256"):
        assert "CANNOT be ruled out" in "\n".join(report)


def test_the_recorder_persists_the_fingerprint(mod):
    """The record path must actually write it, or the compare path has nothing to
    read on the next run."""
    from aria_service.tests._source_probe import function_source

    src = function_source(mod, "main")
    assert '"environment": environment_fingerprint()' in src
