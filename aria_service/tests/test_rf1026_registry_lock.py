"""R-F1026 — the R-number registry must be bulletproof under concurrency:
a cross-process file lock serialises reserve/ship/abandon, and it must NEVER
hang the caller (times out -> proceeds; steals a stale lock from a crashed holder).
"""
from __future__ import annotations

import os
import threading
import time

from aria_service.intel import r_number_registry as reg


def test_concurrent_reserves_yield_unique_numbers(tmp_path):
    p = tmp_path / "r.json"
    out: list[str] = []
    guard = threading.Lock()

    def worker():
        rn = reg.reserve("concurrent", path=p)
        with guard:
            out.append(rn)

    threads = [threading.Thread(target=worker) for _ in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=20)

    assert len(out) == 12
    assert len(set(out)) == 12, f"duplicate R-numbers issued: {sorted(out)}"


def test_file_lock_times_out_instead_of_hanging(tmp_path, monkeypatch):
    """A fresh lock held by 'another process' must NOT block forever — the lock
    times out and proceeds (the whole point: never freeze the loop)."""
    p = tmp_path / "r.json"
    lockp = p.with_suffix(".json.lock")
    lockp.write_text("99999")  # simulate a live held lock
    monkeypatch.setattr(reg, "_FILE_LOCK_TIMEOUT_S", 0.4)
    monkeypatch.setattr(reg, "_FILE_LOCK_STALE_S", 9999)  # not considered stale

    start = time.monotonic()
    with reg._file_lock(p):
        pass
    elapsed = time.monotonic() - start
    assert elapsed < 3.0, f"file lock hung for {elapsed:.2f}s (must time out)"


def test_file_lock_steals_stale_lock(tmp_path, monkeypatch):
    """A stale lock left by a crashed holder is stolen so the loop isn't blocked."""
    p = tmp_path / "r.json"
    lockp = p.with_suffix(".json.lock")
    lockp.write_text("dead-holder")
    old = time.time() - 10_000
    os.utime(lockp, (old, old))
    monkeypatch.setattr(reg, "_FILE_LOCK_STALE_S", 30)

    acquired = {"v": False}
    with reg._file_lock(p):
        acquired["v"] = True
    assert acquired["v"] is True


def test_reserve_still_works_and_marks_shipped(tmp_path):
    p = tmp_path / "r.json"
    rn = reg.reserve("normal path", path=p)
    assert rn.startswith("R-F")
    reg.mark_shipped(rn, "deadbeef", path=p)
    shipped = [r for r in reg.list_reservations(status_filter="shipped", path=p)]
    assert any(r["r_number"] == rn for r in shipped)
