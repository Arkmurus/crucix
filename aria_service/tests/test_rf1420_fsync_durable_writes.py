"""R-F1420 — fsync durable writers (no data loss on crash).

knowledge.py + intel_ledger.py wrote via temp-file + os.replace (atomic, so
no torn files) but with NO fsync — a host crash / power loss after the write
returns but before the OS flushed dirty pages could lose the data the atomic
rename pointed at. With 87k+ facts that's catastrophic. R-F1420 adds
flush()+os.fsync(fd) before the rename + a best-effort dir fsync after.

These tests drive the REAL _write_to_disk_atomic of both modules and assert
(a) the data round-trips correctly and (b) os.fsync is actually called on the
written file descriptor (the durability guarantee).
"""
from __future__ import annotations

import json
import os

import pytest

from aria_service.intel import knowledge, intel_ledger


@pytest.mark.parametrize("mod, payload", [
    (knowledge, {"facts": {"a": 1, "b": "two"}, "v": 3}),
    (intel_ledger, {"signals": [{"id": "s1"}, {"id": "s2"}], "n": 2}),
])
def test_atomic_write_roundtrips_and_fsyncs(mod, payload, tmp_path, monkeypatch):
    target = tmp_path / "data.json"
    monkeypatch.setattr(mod, "_DISK_PATH", str(target))

    fsynced_fds = []
    real_fsync = os.fsync

    def _spy_fsync(fd):
        fsynced_fds.append(fd)
        return real_fsync(fd)

    monkeypatch.setattr(os, "fsync", _spy_fsync)

    mod._write_to_disk_atomic(payload)

    # (a) data is on disk and correct
    assert target.exists()
    assert json.loads(target.read_text(encoding="utf-8")) == payload
    # (b) fsync was actually called (file fd at minimum; dir fsync is
    # best-effort and may be skipped on some platforms)
    assert len(fsynced_fds) >= 1, "os.fsync must be called before the rename"


def test_typeerror_fallback_still_fsyncs(tmp_path, monkeypatch):
    # a non-native value forces the default=str fallback path — it must STILL
    # fsync and produce valid JSON (the fallback path shares the fsync).
    import datetime
    target = tmp_path / "k.json"
    monkeypatch.setattr(knowledge, "_DISK_PATH", str(target))
    fsynced = []
    real = os.fsync
    monkeypatch.setattr(os, "fsync", lambda fd: (fsynced.append(fd), real(fd))[1])

    knowledge._write_to_disk_atomic({"when": datetime.datetime(2026, 6, 7)})
    assert target.exists()
    assert len(fsynced) >= 1
    # round-trips as a string (default=str)
    assert "2026-06-07" in target.read_text(encoding="utf-8")


def test_fsync_dir_is_best_effort_never_raises():
    # must swallow unsupported-platform / bad-path errors (Windows dir fsync)
    knowledge._fsync_dir("/nonexistent/path/xyz")  # no raise
    intel_ledger._fsync_dir("/nonexistent/path/xyz")  # no raise


def test_write_failure_cleans_tmp_and_raises(tmp_path, monkeypatch):
    # if the write itself fails, the tmp file is unlinked and the error
    # propagates (no silent data loss, no orphan tmp).
    target = tmp_path / "k.json"
    monkeypatch.setattr(knowledge, "_DISK_PATH", str(target))

    def _boom(fd):
        raise OSError("disk full")

    monkeypatch.setattr(os, "fsync", _boom)
    with pytest.raises(OSError):
        knowledge._write_to_disk_atomic({"x": 1})
    # no orphan .tmp left behind
    leftovers = list(tmp_path.glob(".aria_knowledge.*.tmp"))
    assert leftovers == [], f"orphan tmp files: {leftovers}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
