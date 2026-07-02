"""R-F2280 — reclaim orphaned atomic-write .tmp files leaked by KILLED processes.

Root cause: knowledge.py's `_write_facts_sidecar` / `_write_to_disk_atomic` unlink
their mkstemp `.tmp` on any Python exception, but CANNOT when the process is killed
mid-write (SIGKILL / fly SIGTERM / the R-F2277 state_store watchdog's os._exit).
The atomic rename never happens → the `.tmp` is orphaned. The 2026-06-29..07-01
wedge-restart incidents leaked ~1.5 GB of `.aria_kn_facts.*.jsonl.tmp` into /data
(verified live: 46 files, some 100-147 MB). These are INCOMPLETE temp files, never
live knowledge, so removing them does not touch ARIA's infinite memory (§7).

Capability tests drive the actual boot path (`init` → `_sweep_orphaned_sidecar_tmp`)
and assert the user-visible outcome: stale orphans gone, everything else preserved.
"""
from __future__ import annotations

import asyncio
import os
import time

import aria_service.intel.knowledge as kn


def _touch(path, size: int, age_s: float) -> None:
    path.write_bytes(b"x" * size)
    t = time.time() - age_s
    os.utime(path, (t, t))


def test_rf2280_sweep_removes_stale_orphans_keeps_everything_else(tmp_path, monkeypatch):
    monkeypatch.setattr(kn, "_DISK_PATH", tmp_path / "aria_knowledge.json")

    # Stale orphans across all three atomic-write prefixes (old mtime → removed).
    stale = [
        tmp_path / ".aria_kn_facts.abc123.jsonl.tmp",
        tmp_path / ".aria_kn_meta.xyz789.json.tmp",
        tmp_path / ".aria_knowledge.qqq.json.tmp",
    ]
    for p in stale:
        _touch(p, 100, age_s=4000)  # older than the 1800s floor

    # A RECENT temp (a concurrent worker's live write) — must be KEPT.
    recent = tmp_path / ".aria_kn_facts.live.jsonl.tmp"
    _touch(recent, 50, age_s=5)

    # Real files that must NEVER be touched: the canonical store + derived sidecars
    # (no leading dot, no .tmp suffix), and an unrelated .tmp-looking non-match.
    canonical = tmp_path / "aria_knowledge.json"
    canonical.write_bytes(b"REAL-KNOWLEDGE")
    facts_sidecar = tmp_path / "aria_knowledge.json.facts.jsonl"
    facts_sidecar.write_bytes(b"REAL-FACTS")
    unrelated = tmp_path / "somethingelse.tmp"  # .tmp but wrong prefix
    _touch(unrelated, 10, age_s=4000)

    res = kn._sweep_orphaned_sidecar_tmp(min_age_s=1800)

    assert res["removed"] == 3, res
    assert res["bytes_reclaimed"] == 300, res
    assert res["skipped_recent"] == 1, res
    for p in stale:
        assert not p.exists(), f"stale orphan not reclaimed: {p.name}"
    assert recent.exists(), "concurrent-worker live temp was wrongly deleted"
    assert canonical.read_bytes() == b"REAL-KNOWLEDGE", "canonical knowledge touched!"
    assert facts_sidecar.exists(), "derived sidecar wrongly deleted"
    assert unrelated.exists(), "non-matching .tmp wrongly deleted"


def test_rf2280_sweep_never_raises_on_missing_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(kn, "_DISK_PATH", tmp_path / "nope" / "aria_knowledge.json")
    res = kn._sweep_orphaned_sidecar_tmp(min_age_s=0)
    assert res == {"removed": 0, "bytes_reclaimed": 0, "skipped_recent": 0}


def test_rf2280_age_floor_env_override(tmp_path, monkeypatch):
    monkeypatch.setattr(kn, "_DISK_PATH", tmp_path / "aria_knowledge.json")
    monkeypatch.setenv("ARIA_KN_TMP_SWEEP_MIN_AGE_S", "10")
    p = tmp_path / ".aria_kn_facts.mid.jsonl.tmp"
    _touch(p, 20, age_s=30)  # older than the 10s override → removed
    res = kn._sweep_orphaned_sidecar_tmp()  # reads env
    assert res["removed"] == 1 and not p.exists()


def test_rf2280_init_invokes_sweep_on_boot(monkeypatch):
    """init() must call the sweep BEFORE loading — this is the boot wiring."""
    hits = {"n": 0}

    def _fake_sweep(*a, **k):
        hits["n"] += 1
        return {"removed": 0, "bytes_reclaimed": 0, "skipped_recent": 0}

    async def _fake_load():
        return {"facts": []}

    monkeypatch.setattr(kn, "_sweep_orphaned_sidecar_tmp", _fake_sweep)
    monkeypatch.setattr(kn, "_load", _fake_load)
    monkeypatch.setenv("ARIA_SEMANTIC_INDEX_BUILD", "0")  # skip the heavy index build

    asyncio.run(kn.init())
    assert hits["n"] == 1, "init() did not call _sweep_orphaned_sidecar_tmp on boot"
