"""R-F1621 — ROOT-CAUSE capability test for the recurring 5-16s event-loop
wedge (wedge_674): the knowledge-graph json.dump must run with cyclic GC
DISABLED (so gen2 collections can't re-scan the ~87k-fact graph and starve the
asyncio loop mid-dump), must RESTORE the prior GC state afterwards (even on
error, and never enabling GC a caller had off), and boot must FREEZE the
long-lived graph out of GC's scan set.

These drive the REAL functions that wedge the loop (knowledge._write_to_disk_atomic
+ main._freeze_long_lived_state), not proxies.
"""
from __future__ import annotations

import gc
import json

import pytest


def test_dump_runs_with_gc_disabled_and_restores_it(tmp_path, monkeypatch):
    from aria_service.intel import knowledge as K

    target = tmp_path / "aria_knowledge.json"
    monkeypatch.setattr(K, "_DISK_PATH", str(target), raising=False)

    gc.enable()  # start enabled so we can prove it's off mid-dump + restored
    seen = {}
    real_dump = json.dump

    def _spy_dump(data, f, **kw):
        seen["gc_enabled_during_dump"] = gc.isenabled()
        return real_dump(data, f, **kw)

    monkeypatch.setattr(K.json, "dump", _spy_dump)

    data = {
        "facts": [{"id": i, "content": f"fact {i}"} for i in range(50)],
        "queries": [], "learnings": [], "version": 1,
    }
    K._write_to_disk_atomic(data)

    # 1. GC was OFF during the dump — the actual fix.
    assert seen.get("gc_enabled_during_dump") is False, (
        "GC must be disabled during json.dump so gen2 scans of the huge graph "
        "can't starve the event loop"
    )
    # 2. GC restored afterwards.
    assert gc.isenabled() is True, "GC must be re-enabled after the dump"
    # 3. Functional correctness preserved — valid, complete JSON on disk.
    loaded = json.loads(target.read_text(encoding="utf-8"))
    assert len(loaded["facts"]) == 50 and loaded["version"] == 1


def test_dump_restores_gc_even_when_dump_raises(tmp_path, monkeypatch):
    from aria_service.intel import knowledge as K

    monkeypatch.setattr(K, "_DISK_PATH", str(tmp_path / "k.json"), raising=False)
    gc.enable()

    def _boom(*a, **k):
        raise RuntimeError("dump failed")

    monkeypatch.setattr(K.json, "dump", _boom)

    with pytest.raises(RuntimeError):
        K._write_to_disk_atomic({"facts": [], "version": 1})
    assert gc.isenabled() is True, "GC must be restored even when the dump raises"


def test_dump_does_not_enable_gc_if_caller_had_it_disabled(tmp_path, monkeypatch):
    from aria_service.intel import knowledge as K

    monkeypatch.setattr(K, "_DISK_PATH", str(tmp_path / "k.json"), raising=False)
    gc.disable()
    try:
        K._write_to_disk_atomic({"facts": [], "version": 1})
        assert gc.isenabled() is False, (
            "must not turn GC back on if a caller had it disabled (no nesting surprise)"
        )
    finally:
        gc.enable()


def test_freeze_long_lived_state_freezes_and_never_raises():
    from aria_service import main as M

    before = gc.get_freeze_count()
    n = M._freeze_long_lived_state()
    after = gc.get_freeze_count()
    try:
        assert isinstance(n, int) and n >= 0
        assert after >= before, "freeze count must not drop"
        assert after > 0, "gc.freeze() must move live objects to the permanent gen"
    finally:
        gc.unfreeze()  # test isolation — don't leave the runner's heap frozen
