"""R-F3200 — ship/abandon wrote the status and never checked it landed.

R-F3187 made `reserve()` prove its claim reached disk. `mark_shipped` and
`mark_abandoned` were left on the IDENTICAL unverified path: save, return, never look
again — same `_file_lock`, same fail-open behaviour (proceeds unlocked on a 10s
timeout, on ANY OSError, and steals a "stale" lock after 30s from a holder that may
still be alive). A concurrent writer silently drops the status change.

This has a MEASURED symptom in this repo, not a hypothetical one: R-F3095 exists
because 372 reservations sat `in_progress` with `commit_sha: null` while their code was
committed and live. An unverified ship-mark that gets clobbered leaves exactly that
residue — the drift someone then reconciles against git by hand.

A FALSE ship-mark is worse than a missing one, because it attributes work to a commit
that does not contain it. That happened live on 2026-07-26: a `ship R-F3183 <sha>`
stamped a colliding reservation — another agent's vetting work — with an unrelated
commit's SHA. So verification checks the SHA too, not just the status word: a ship
recorded against the wrong commit must read as NOT recorded.
"""
import json
from pathlib import Path

import pytest

from aria_service.intel import r_number_registry as reg


@pytest.fixture()
def registry(tmp_path):
    p = tmp_path / "r_number_reservations.json"
    p.write_text(json.dumps(
        {"schema_version": 1, "next_available": 1000, "reservations": []}),
        encoding="utf-8")
    return p


def _entry(path, rn):
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    return next((r for r in d["reservations"] if r["r_number"] == rn), None)


def test_rf3200_ship_is_recorded(registry):
    rn = reg.reserve("work", path=registry)
    reg.mark_shipped(rn, "abc1234", path=registry)
    e = _entry(registry, rn)
    assert e["status"] == "shipped" and e["commit_sha"] == "abc1234"


def test_rf3200_abandon_is_recorded(registry):
    rn = reg.reserve("work", path=registry)
    reg.mark_abandoned(rn, "superseded", path=registry)
    e = _entry(registry, rn)
    assert e["status"] == "abandoned" and e["abandon_reason"] == "superseded"


def test_rf3200_verification_checks_the_sha_not_just_the_status():
    """THE LIVE R-F3183 CASE: a ship stamped against the WRONG commit is a lie, and
    must not verify as recorded."""
    import tempfile
    p = Path(tempfile.mkdtemp()) / "r.json"
    p.write_text(json.dumps({"schema_version": 1, "next_available": 1, "reservations": [
        {"r_number": "R-F1", "status": "shipped", "commit_sha": "aaaaaaa"}]}),
        encoding="utf-8")
    assert reg._status_is_recorded("R-F1", "shipped", p, commit_sha="aaaaaaa") is True
    assert reg._status_is_recorded("R-F1", "shipped", p, commit_sha="bbbbbbb") is False


def test_rf3200_ship_retries_when_clobbered(registry, monkeypatch):
    """A concurrent writer reverts our entry between save and verify."""
    rn = reg.reserve("contested", path=registry)
    calls = {"n": 0}
    real_save = reg._save_atomic

    def _clobber(data, path=None):
        real_save(data, path)
        calls["n"] += 1
        if calls["n"] == 1:
            d = json.loads(Path(path).read_text(encoding="utf-8"))
            for r in d["reservations"]:
                if r["r_number"] == rn:
                    r["status"] = "in_progress"
                    r["commit_sha"] = None
            Path(path).write_text(json.dumps(d), encoding="utf-8")

    monkeypatch.setattr(reg, "_save_atomic", _clobber)
    reg.mark_shipped(rn, "sha9999", path=registry)

    e = _entry(registry, rn)
    assert e["status"] == "shipped" and e["commit_sha"] == "sha9999", (
        "R-F3200 REGRESSION: a clobbered ship-mark was reported as successful — this "
        "is how 372 entries drifted to in_progress while their code was live")
    assert calls["n"] >= 2, "it must have retried after detecting the clobber"


def test_rf3200_raises_when_the_status_never_lands(registry, monkeypatch):
    """Fail loudly rather than letting the caller believe it was recorded."""
    rn = reg.reserve("doomed", path=registry)
    monkeypatch.setattr(reg, "_save_atomic", lambda data, path=None: None)
    with pytest.raises(reg.RegistryWriteError) as ei:
        reg.mark_shipped(rn, "neverlands", path=registry)
    assert "NOT saved" in str(ei.value)


def test_rf3200_abandon_raises_when_it_never_lands(registry, monkeypatch):
    rn = reg.reserve("doomed", path=registry)
    monkeypatch.setattr(reg, "_save_atomic", lambda data, path=None: None)
    with pytest.raises(reg.RegistryWriteError):
        reg.mark_abandoned(rn, "nope", path=registry)


def test_rf3200_unknown_r_number_still_raises_keyerror(registry):
    """Contract preserved: an unreserved number is a CALLER error, not a write
    failure, and callers (scripts/admin, reconcile) depend on KeyError."""
    with pytest.raises(KeyError):
        reg.mark_shipped("R-F999999", "abc1234", path=registry)
    with pytest.raises(KeyError):
        reg.mark_abandoned("R-F999999", "reason", path=registry)


def test_rf3200_invalid_r_number_still_raises_valueerror(registry):
    with pytest.raises(ValueError):
        reg.mark_shipped("nonsense", "abc1234", path=registry)


def test_rf3200_ship_is_idempotent(registry):
    """Re-shipping the same number must stay safe — the docstring promises it."""
    rn = reg.reserve("work", path=registry)
    reg.mark_shipped(rn, "abc1234", path=registry)
    reg.mark_shipped(rn, "abc1234", path=registry)
    d = json.loads(registry.read_text(encoding="utf-8"))
    assert sum(1 for r in d["reservations"] if r["r_number"] == rn) == 1


def test_rf3200_windows_permission_error_is_survived(registry, monkeypatch):
    """The WinError 5 path must not lose a ship-mark either."""
    rn = reg.reserve("work", path=registry)
    attempts = {"n": 0}
    real_replace = reg.os.replace

    def _flaky(src, dst):
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise PermissionError(5, "Access is denied")
        return real_replace(src, dst)

    monkeypatch.setattr(reg.os, "replace", _flaky)
    reg.mark_shipped(rn, "sha5555", path=registry)
    assert _entry(registry, rn)["commit_sha"] == "sha5555"
