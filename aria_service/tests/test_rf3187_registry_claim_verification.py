"""R-F3187 — the registry handed back R-numbers it never confirmed it had recorded.

THREE CASUALTIES IN ONE DAY (2026-07-26), all from this one flaw:

  * R-F3133 / R-F3134 / R-F3135 — reserved, used in code comments and commit
    messages, shipped and deployed, and ABSENT from data/r_number_reservations.json
    afterwards. Found only because a peer agent's unrelated message prompted a look.
  * R-F3183 — issued TWICE, 8 minutes apart, to two different agents. The later
    write clobbered the earlier entry, and a subsequent `ship R-F3183 <sha>` then
    stamped the WRONG work with the WRONG commit.
  * a CleanHead stash pop conflicted on this file mid-deploy.

CAUSE: `reserve()` returned `r_num` as soon as `_save_atomic` returned, without ever
re-reading the file. `_file_lock` (R-F1026) is deliberately FAIL-OPEN — it proceeds
WITHOUT the lock on timeout (10s), on ANY OSError, and it steals a "stale" lock after
30s from a holder that may still be alive. That is a defensible choice; it must never
hang a deploy. But fail-open writing plus no verification turns a rare race into
silent data loss, and the caller then writes the lost number into shipped code.

Also fixed here, both observed live:
  * `_save_atomic` used ONE shared temp name (`.json.tmp`), so two agents saving at
    once wrote the same scratch file.
  * `os.replace` raises PermissionError (WinError 5) on Windows when another process
    holds the destination — seen live, and it simply lost the claim.

§2 makes this file the claim of record. A claim nobody can read back is not a claim.
"""
import json
import multiprocessing
import os
import sys
from pathlib import Path

import pytest

from aria_service.intel import r_number_registry as reg


@pytest.fixture()
def registry(tmp_path):
    p = tmp_path / "r_number_reservations.json"
    p.write_text(json.dumps(
        {"schema_version": 1, "next_available": 1000, "reservations": []}), encoding="utf-8")
    return p


def _numbers(path):
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    return [r["r_number"] for r in d["reservations"]]


def test_rf3187_a_reserved_number_is_actually_recorded(registry):
    rn = reg.reserve("first claim", path=registry)
    assert rn in _numbers(registry), "the returned number must be in the file"


def test_rf3187_claim_verification_matches_on_claimed_at_not_just_number(registry):
    """THE R-F3183 COLLISION: if another agent takes the same number for different
    work, the number alone reads back as present while OUR claim is gone."""
    rn = reg.reserve("mine", path=registry)
    d = json.loads(registry.read_text(encoding="utf-8"))
    mine = [r for r in d["reservations"] if r["r_number"] == rn][0]
    assert reg._claim_is_recorded(rn, mine["claimed_at"], registry) is True
    # same number, someone else's timestamp -> NOT our claim
    assert reg._claim_is_recorded(rn, "1999-01-01T00:00:00Z", registry) is False


def test_rf3187_reserve_reallocates_when_its_entry_is_clobbered(registry, monkeypatch):
    """Simulate a concurrent writer that drops our entry between save and verify."""
    calls = {"n": 0}
    real_save = reg._save_atomic

    def _clobbering_save(data, path=None):
        real_save(data, path)
        calls["n"] += 1
        if calls["n"] == 1:
            # another agent rewrites the file from an older snapshot: our entry is gone
            d = json.loads(Path(path).read_text(encoding="utf-8"))
            d["reservations"] = [r for r in d["reservations"]
                                 if r.get("title") != "contested"]
            Path(path).write_text(json.dumps(d), encoding="utf-8")

    monkeypatch.setattr(reg, "_save_atomic", _clobbering_save)
    rn = reg.reserve("contested", path=registry)

    assert rn in _numbers(registry), (
        "R-F3187 REGRESSION: reserve returned a number that is NOT in the registry — "
        "this is exactly how R-F3133/3134/3135 were lost")
    assert calls["n"] >= 2, "it must have re-allocated after detecting the clobber"


def test_rf3187_raises_rather_than_returning_an_unrecorded_number(registry, monkeypatch):
    """If every attempt is clobbered, FAIL LOUDLY. Returning a number the caller
    then writes into shipped code is the worst outcome."""
    def _never_persists(data, path=None):
        pass  # write silently goes nowhere

    monkeypatch.setattr(reg, "_save_atomic", _never_persists)
    with pytest.raises(reg.RegistryWriteError) as ei:
        reg.reserve("doomed", path=registry)
    assert "NOTHING was claimed" in str(ei.value)


def test_rf3187_save_retries_a_windows_permission_error(registry, monkeypatch):
    """OBSERVED LIVE: PermissionError [WinError 5] on os.replace lost the claim."""
    attempts = {"n": 0}
    real_replace = os.replace

    def _flaky(src, dst):
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise PermissionError(5, "Access is denied")
        return real_replace(src, dst)

    monkeypatch.setattr(reg.os, "replace", _flaky)
    rn = reg.reserve("survives contention", path=registry)
    assert rn in _numbers(registry)
    assert attempts["n"] >= 3, "it must have retried rather than losing the claim"


def test_rf3187_save_surfaces_persistent_permission_failure(registry, monkeypatch):
    def _always(src, dst):
        raise PermissionError(5, "Access is denied")
    monkeypatch.setattr(reg.os, "replace", _always)
    with pytest.raises(reg.RegistryWriteError):
        reg.reserve("never lands", path=registry)


def test_rf3187_temp_file_is_unique_per_process(registry):
    """Two agents saving at once must not share one scratch file."""
    import inspect
    src = inspect.getsource(reg._save_atomic)
    assert 'with_suffix(".json.tmp")' not in src, (
        "R-F3187 REGRESSION: a single shared temp name is back — two processes "
        "serialising into the same scratch file corrupt each other")
    assert "os.getpid()" in src


def test_rf3187_no_temp_files_are_left_behind(registry):
    reg.reserve("tidy", path=registry)
    leftovers = list(Path(registry).parent.glob("*.tmp"))
    assert not leftovers, f"temp files left behind: {leftovers}"


# ── the real thing: separate PROCESSES, which is what actually broke ──────────

def _child_reserve(path_str, title, out):
    """Runs in a separate process — the exact scenario that lost R-F3133/3134/3135."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from aria_service.intel import r_number_registry as r
    try:
        out.put(r.reserve(title, path=Path(path_str)))
    except Exception as exc:                       # noqa: BLE001
        out.put(f"ERROR: {type(exc).__name__}: {exc}")


@pytest.mark.skipif(sys.platform == "win32" and sys.version_info < (3, 8),
                    reason="spawn semantics")
def test_rf3187_concurrent_processes_never_share_a_number(registry):
    """THE ACTUAL FAILURE: two agents reserving at the same moment.

    Every claim returned must be distinct AND present in the file. Before R-F3187 a
    clobbered claim was returned anyway and silently vanished.
    """
    ctx = multiprocessing.get_context("spawn")
    q = ctx.Queue()
    procs = [ctx.Process(target=_child_reserve, args=(str(registry), f"claim-{i}", q))
             for i in range(6)]
    for p in procs:
        p.start()
    for p in procs:
        p.join(timeout=120)

    got = [q.get() for _ in range(len(procs))]
    errors = [g for g in got if str(g).startswith("ERROR")]
    assert not errors, f"reserve failed under concurrency: {errors}"
    assert len(set(got)) == len(got), f"DUPLICATE R-numbers issued: {got}"

    recorded = _numbers(registry)
    missing = [g for g in got if g not in recorded]
    assert not missing, (
        f"R-F3187 REGRESSION: {missing} were returned to a caller but are NOT in the "
        f"registry — the exact way R-F3133/3134/3135 were lost")
    assert len(recorded) == len(procs), f"expected {len(procs)} entries, got {recorded}"
