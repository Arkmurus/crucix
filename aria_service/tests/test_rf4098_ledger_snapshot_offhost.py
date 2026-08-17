"""R-F4098 (C-142) — CAPABILITY: the ledger's "off-host backup" was neither.

`intel_ledger._flush_loop` carries:

    SNAPSHOT_INTERVAL_S = 600.0  # 10 min — Redis off-host backup cadence

R-F334 built that as a genuine off-host tier and it was one. R-F745 then
flipped the default backend to sqlite and Upstash was cancelled (§6/§18), and
nothing revisited THIS module — so on production it gzips the whole ledger and
`rs.set`s it every 600 s into `/data/aria_state.db`, the SAME volume as the
`/data/aria_signals.json` it is meant to protect. Measured in-machine
2026-08-17:

    intel_ledger      keys=  11  bytes=  8,179,418     (8.18 MB, every 10 min)
    knowledge_shards  keys= 225  bytes= 83,232,780
    /data/aria_state.db  630 MB   ...and timing out reads (C-140)

A copy that shares a failure domain with its original protects nothing that
volume loss would not already take. It is pure cost — plus a whole-ledger gzip
whose own docstring records it wedging the loop.

This is C-98 exactly, in the module C-98's fix did not cover. The transferable
lesson C-98 recorded: *"When a mechanism looks expensive, check whether the
thing it was built for is still true."*

THE TRI-STATE IS LOAD-BEARING and its safety default is the OPPOSITE of a
write's: an UNMEASURABLE target must keep BACKING UP. "I don't know" is a
reason to keep a copy, never to silently stop making one. A remote backend must
still snapshot, so re-pointing the state store off-host resumes the backup with
NO code change.

Run: python -m pytest aria_service/tests/test_rf4098_ledger_snapshot_offhost.py -v
"""
from __future__ import annotations

import pytest


# ══════════════════════════════════════════════════════════════════════
# THE DEFECT — same volume must not be called a backup
# ══════════════════════════════════════════════════════════════════════

def test_same_volume_is_not_offhost(monkeypatch):
    from aria_service.intel import intel_ledger as il

    monkeypatch.setattr(il, "_device_of", lambda p: 2049, raising=False)
    from aria_service.intel import redis_store as rs
    monkeypatch.setattr(rs, "_BACKEND", "sqlite", raising=False)

    assert il._snapshot_target_is_offhost() is False
    assert il._should_snapshot(False) is False, (
        "an 8.18 MB gzip every 600 s into the volume it backs up is pure cost"
    )


def test_a_different_volume_is_a_real_backup(monkeypatch):
    from aria_service.intel import intel_ledger as il
    from aria_service.intel import redis_store as rs

    monkeypatch.setattr(rs, "_BACKEND", "sqlite", raising=False)
    devs = iter([2049, 2050])
    monkeypatch.setattr(il, "_device_of", lambda p: next(devs), raising=False)

    assert il._snapshot_target_is_offhost() is True
    assert il._should_snapshot(True) is True


def test_a_remote_backend_still_snapshots(monkeypatch):
    """Re-pointing the state store off-host must resume the backup with NO
    code change — do not simplify this branch away."""
    from aria_service.intel import intel_ledger as il
    from aria_service.intel import redis_store as rs

    for backend in ("upstash", "redis"):
        monkeypatch.setattr(rs, "_BACKEND", backend, raising=False)
        assert il._snapshot_target_is_offhost() is True, backend


# ══════════════════════════════════════════════════════════════════════
# THE SAFETY DEFAULT — unknown keeps backing up
# ══════════════════════════════════════════════════════════════════════

def test_unmeasurable_still_snapshots(monkeypatch):
    from aria_service.intel import intel_ledger as il
    from aria_service.intel import redis_store as rs

    monkeypatch.setattr(rs, "_BACKEND", "sqlite", raising=False)
    monkeypatch.setattr(il, "_device_of", lambda p: None, raising=False)

    assert il._snapshot_target_is_offhost() is None, "unmeasurable is not False"
    assert il._should_snapshot(None) is True, (
        "'I could not measure' must never silently stop backing data up — the "
        "safety default here is the opposite of a write's"
    )


def test_only_a_measured_false_skips():
    from aria_service.intel import intel_ledger as il

    assert il._should_snapshot(True) is True
    assert il._should_snapshot(None) is True
    assert il._should_snapshot(False) is False


# ══════════════════════════════════════════════════════════════════════
# THE WIRE — the loop must actually consult the gate
# ══════════════════════════════════════════════════════════════════════

def test_the_flush_loop_consults_the_gate():
    from ._source_probe import function_source
    from aria_service.intel import intel_ledger as il

    src = function_source(il, "_flush_loop")
    assert "_should_snapshot" in src or "_snapshot_target_is_offhost" in src, (
        "the gate exists but the loop still snapshots unconditionally — the "
        "8.18 MB every 600 s is unchanged"
    )


def test_the_skip_announces_once_not_every_cycle():
    """C-98: a 600 s steady state defeats a 300 s cooldown and would emit ~144
    gap signals a day — the sanctions_coverage_degraded flood shape."""
    from ._source_probe import function_source
    from aria_service.intel import intel_ledger as il

    src = function_source(il, "_flush_loop")
    assert "_snapshot_skip_announced" in src, (
        "the skip must be announced once per process, not once per cycle"
    )
