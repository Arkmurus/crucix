"""R-F2920 — the file-integrity audit must detect, repair, and know when to stop.

Context: on 2026-07-23 Kaspersky File Anti-Virus deleted
aria_service/static/aria_client/aria.bat from the tree (initiator git.exe, verdict
Trojan). The deploy built from that tree and shipped the file ABSENT — it served 404
in production while every other check passed.

R-F2919 gates the deploy. This is the audit: it runs over time, records every result
including the clean ones, repairs from git, and — critically — STOPS repairing a file
that keeps disappearing, because fighting an antivirus in a loop hides the problem
instead of surfacing it.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "_fim_rf2920",
    Path(__file__).resolve().parents[2] / "scripts" / "file_integrity_monitor.py",
)
fim = importlib.util.module_from_spec(_SPEC)
sys.modules["_fim_rf2920"] = fim
_SPEC.loader.exec_module(fim)


@pytest.fixture()
def ledger(tmp_path, monkeypatch):
    """Isolate the ledger so a test never writes the developer's real audit history."""
    path = tmp_path / "ledger.json"
    monkeypatch.setattr(fim, "LEDGER", path)
    return path


def test_rf2920_clean_tree_is_recorded_as_evidence(ledger, monkeypatch):
    """A clean run must be RECORDED, not skipped.

    'Nothing was wrong' is only trustworthy if the check demonstrably ran — otherwise a
    silent monitor and a broken monitor look identical.
    """
    monkeypatch.setattr(fim, "missing_files", lambda: [])
    r = fim.audit()
    assert r["clean"] is True
    assert r["missing"] == []
    runs = json.loads(ledger.read_text(encoding="utf-8"))["runs"]
    assert len(runs) == 1 and runs[0]["clean"] is True


def test_rf2920_missing_file_is_detected(ledger, monkeypatch):
    monkeypatch.setattr(fim, "missing_files",
                        lambda: ["aria_service/static/aria_client/aria.bat"])
    r = fim.audit(do_restore=False)
    assert r["clean"] is False
    assert "aria_service/static/aria_client/aria.bat" in r["missing"]
    assert r["restored"] == [], "must not restore unless asked"


def test_rf2920_restore_repairs_and_is_recorded(ledger, monkeypatch):
    monkeypatch.setattr(fim, "missing_files", lambda: ["a/b.bat"])
    monkeypatch.setattr(fim, "restore", lambda p: True)
    r = fim.audit(do_restore=True)
    assert r["restored"] == ["a/b.bat"]
    assert r["restore_failed"] == []


def test_rf2920_a_failed_restore_is_reported_not_swallowed(ledger, monkeypatch):
    """`git checkout` exiting 0 is not evidence — the AV deletes the file again
    milliseconds later. The ledger must record what is TRUE after re-checking."""
    monkeypatch.setattr(fim, "missing_files", lambda: ["a/b.bat"])
    monkeypatch.setattr(fim, "restore", lambda p: False)      # vanished again
    r = fim.audit(do_restore=True)
    assert r["restore_failed"] == ["a/b.bat"]
    assert r["restored"] == []


def test_rf2920_repeated_loss_flaps_and_stops_restoring(ledger, monkeypatch):
    """The important behaviour: do not fight the antivirus in a loop.

    After _FLAP_THRESHOLD losses the file is escalated and left alone, so the operator
    sees a persistent problem instead of a monitor quietly churning forever.
    """
    monkeypatch.setattr(fim, "missing_files", lambda: ["a/b.bat"])
    restores: list[str] = []

    def _restore(p):
        restores.append(p)
        return True

    monkeypatch.setattr(fim, "restore", _restore)

    for _ in range(fim._FLAP_THRESHOLD - 1):
        r = fim.audit(do_restore=True)
        assert r["flapping"] == []

    r = fim.audit(do_restore=True)          # threshold reached
    assert r["flapping"] == ["a/b.bat"]
    assert len(restores) == fim._FLAP_THRESHOLD - 1, (
        "a flapping file was restored again — that is the loop this guard prevents"
    )


def test_rf2920_flap_counts_persist_across_runs(ledger, monkeypatch):
    """Flap state must be durable; the AV events are minutes or hours apart."""
    monkeypatch.setattr(fim, "missing_files", lambda: ["a/b.bat"])
    monkeypatch.setattr(fim, "restore", lambda p: True)
    fim.audit(do_restore=True)
    counts = json.loads(ledger.read_text(encoding="utf-8"))["flap_counts"]
    assert counts["a/b.bat"] == 1


def test_rf2920_alert_fires_only_on_a_real_problem(ledger, monkeypatch):
    calls: list = []
    monkeypatch.setattr(fim, "_alert_operator", lambda r: calls.append(r))

    monkeypatch.setattr(fim, "missing_files", lambda: [])
    fim.audit(alert=True)
    assert calls == [], "alerted on a clean tree — that is how alerts get ignored"

    monkeypatch.setattr(fim, "missing_files", lambda: ["a/b.bat"])
    fim.audit(alert=True)
    assert len(calls) == 1


def test_rf2920_ledger_failure_never_breaks_the_audit(tmp_path, monkeypatch):
    """Auditing must not become the thing that breaks the build."""
    monkeypatch.setattr(fim, "LEDGER", tmp_path / "nope" / "deep" / "l.json")
    monkeypatch.setattr(fim, "missing_files", lambda: [])

    def _boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(Path, "write_text", _boom, raising=False)
    r = fim.audit()          # must not raise
    assert r["clean"] is True


def test_rf2920_ledger_is_not_committed():
    """Per-machine antivirus history must not enter shared git history."""
    root = Path(fim.__file__).resolve().parents[1]
    ignored = (root / ".gitignore").read_text(encoding="utf-8")
    assert "data/file_integrity_ledger.json" in ignored


def test_rf2920_deploy_scripts_carry_the_integrity_gate():
    """R-F2919 — the audit is the ongoing view; the deploy gate is the hard stop.
    Both deploy scripts must refuse to build from a tree with missing files, and
    CLAUDE.md §11 requires the two scripts to mirror each other."""
    root = Path(fim.__file__).resolve().parents[1]
    ps1 = (root / "scripts" / "deploy.ps1").read_text(encoding="utf-8")
    sh = (root / "scripts" / "deploy.sh").read_text(encoding="utf-8")
    for src, name in ((ps1, "deploy.ps1"), (sh, "deploy.sh")):
        assert "git ls-files -d" in src, f"{name} has no tree-integrity gate"
        assert "TREE INTEGRITY" in src, f"{name} does not report the gate"
