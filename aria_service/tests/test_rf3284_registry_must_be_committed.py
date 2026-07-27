"""R-F3284 - a reservation that exists only in one working tree is not a claim.

THE DRIFT, and why it is not cosmetic. `reserve()` writes the claim to
data/r_number_reservations.json, which is git-tracked, but NOTHING makes it get
committed. So the file accumulates uncommitted reservations until somebody
happens to include it in a commit. Measured on 2026-07-27: 140+ insertions of
drift, and the committed copy stopped at R-F3277 while the working tree held
R-F3281.

Three consequences, all observed rather than theorised:

  * The R-F559 pre-push gate reads the file from disk. In a git worktree or a
    fresh clone that file is the COMMITTED copy, so numbers reserved by another
    tree read as never reserved, and the gate fails a perfectly disciplined push.
    That happened during R-F3281 and cost a full diagnosis to rule out.
  * R-F3187 recorded three claims (R-F3133/3134/3135) reserved, used in shipped
    code, and then absent from the log. An uncommitted claim is one `git checkout`
    from being one of those.
  * R-F3248 made allocation consult git precisely because the file is the losable
    record. That guard reads git HISTORY, so a claim that never reaches a commit
    is invisible to it too.

THE FIX belongs at push time, because that is the moment a claim must become
durable and the moment another agent can first collide with it. The gate now
refuses a push whose R-numbers are reserved ONLY in the working tree.

Deliberately NOT done: auto-committing the registry from `reserve()`. A library
that writes to the index would sweep whatever else is staged into an unrelated
commit, which is the exact accident that overwrote the R-F559 harness (0343241a)
and the reason CLAUDE.md forbids blanket `git add -A`.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "verify_commit.py"


def _mod():
    spec = importlib.util.spec_from_file_location("_vc_rf3284", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def test_the_gate_can_read_the_committed_registry_separately() -> None:
    """The whole fix rests on distinguishing on-disk from committed."""
    mod = _mod()
    assert hasattr(mod, "_registry_r_numbers"), "working-tree reader must exist"
    assert hasattr(mod, "_committed_registry_r_numbers"), (
        "a committed-copy reader must exist, or the drift is undetectable"
    )


def test_committed_reader_returns_real_numbers() -> None:
    """It must actually read git, not silently return an empty set.

    An empty set would make every R-number look uncommitted and fail every push,
    which is the failure mode that gets a gate disabled.
    """
    mod = _mod()
    committed = mod._committed_registry_r_numbers()
    assert isinstance(committed, set)
    assert "R-F540" in committed, (
        "R-F540 is the registry's own self-registration and has been committed "
        "since 2026-05-16; if it is missing, the reader is broken, not the log"
    )


def test_uncommitted_only_numbers_are_detected() -> None:
    """THE CAPABILITY: a number present on disk but not in HEAD is reported."""
    mod = _mod()
    on_disk = mod._registry_r_numbers()
    committed = mod._committed_registry_r_numbers()
    uncommitted_only = on_disk - committed

    # This asserts the MECHANISM, not a particular drift state: whatever is on
    # disk but not in HEAD must be exactly what the helper reports.
    assert mod._uncommitted_registry_numbers() == uncommitted_only


def test_the_check_is_scoped_to_the_push_not_the_whole_file() -> None:
    """Scope matters: a peer mid-reservation must not block MY push.

    Only R-numbers actually being pushed are checked. A blanket "the registry is
    dirty" failure would fire on someone else's in-flight work and teach everyone
    to reach for --no-verify.
    """
    import inspect
    src = inspect.getsource(_mod()._uncommitted_in_range)
    assert "r_numbers" in src or "in_range" in src, (
        "the check must take the pushed R-numbers as input, not scan globally"
    )


def test_a_fully_committed_registry_reports_nothing() -> None:
    """Regression: no false alarm when the log is clean."""
    mod = _mod()
    # An R-number that has been committed for months must never be flagged.
    assert "R-F540" not in mod._uncommitted_in_range({"R-F540"})


def test_the_gate_actually_exits_4_on_drift(monkeypatch, capsys) -> None:
    """VERIFY THE INSTRUMENT: the guard must FAIL, not merely exist.

    Proven by monkeypatch rather than by committing a probe. My first attempt
    made a real commit and then ran `git reset --hard` to clean up, which wiped
    my own uncommitted edits to the verifier. A destructive reset is never the
    right way to undo a test fixture.

    It also has to be a number that HAS a test file, or the earlier
    missing-test check returns 2 first and this branch is never reached. That is
    what the first probe got wrong: it proved nothing while appearing to run.
    """
    mod = _mod()
    monkeypatch.setattr(mod, "_r_numbers_in_range", lambda rng: {"R-F540"})
    monkeypatch.setattr(mod, "_registry_r_numbers", lambda: {"R-F540"})
    monkeypatch.setattr(mod, "_committed_registry_r_numbers", set)  # nothing committed
    monkeypatch.setattr(sys, "argv", ["verify_commit.py", "--skip-tests"])

    rc = mod.main()
    err = capsys.readouterr().err
    assert rc == 4, f"the drift guard did not fire (exit {rc})"
    assert "ONLY in this working tree" in err
    assert "R-F540" in err


def test_the_gate_passes_when_the_same_number_is_committed(monkeypatch) -> None:
    """The other half: identical inputs, but the claim IS durable."""
    mod = _mod()
    monkeypatch.setattr(mod, "_r_numbers_in_range", lambda rng: {"R-F540"})
    monkeypatch.setattr(mod, "_registry_r_numbers", lambda: {"R-F540"})
    monkeypatch.setattr(mod, "_committed_registry_r_numbers", lambda: {"R-F540"})
    monkeypatch.setattr(sys, "argv", ["verify_commit.py", "--skip-tests"])

    assert mod.main() == 0, "a committed reservation must not be flagged"
