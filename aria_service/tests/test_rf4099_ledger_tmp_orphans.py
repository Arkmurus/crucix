"""R-F4099 (C-143) — CAPABILITY: orphaned ledger temp files must be seen.

`intel_ledger._write_to_disk_atomic` creates its temp via `mkstemp` and unlinks
it ONLY on the `except` branch. A process killed mid-write — every deploy,
every restart — orphans it. With `FLUSH_DEBOUNCE_S = 2.0` (C-141) the write
window is a large fraction of uptime, so the two defects compound.

Measured in-machine on aria-intel 2026-08-17:

    108 files · 401,138,594 bytes · 382.6 MB
    oldest 2026-05-17 11:15   newest 2026-07-31 07:15
    prefix .aria_signals.*.json.tmp   (individual files up to 25 MB)
    /dev/vdc  30G  17G used  60%  /data

A repo-wide search found **no cleanup routine anywhere** — no boot sweep, no
timer, no unlink outside the exception handler. The only reference to the
prefix was the `mkstemp` call that creates them.

§26 GOVERNS WHAT THIS FIX MAY DO. "Never touch data stores destructively
(archive with a manifest; `rm` is never the answer)." So the sweep:

  * is **report-only by default** — it measures and reports, and removes
    nothing unless the operator sets `ARIA_LEDGER_TMP_SWEEP=1`;
  * writes a **manifest** (name, size, mtime) BEFORE any removal, so what went
    is recoverable knowledge even when the bytes are not;
  * is **prefix-scoped and age-gated**, so it can never reach the canonical
    file or an in-flight write.

Run: python -m pytest aria_service/tests/test_rf4099_ledger_tmp_orphans.py -v
"""
from __future__ import annotations

import json
import os
import time

import pytest


def _mk(dirpath, name, size=64, age_days=0.0):
    p = os.path.join(dirpath, name)
    with open(p, "wb") as f:
        f.write(b"x" * size)
    if age_days:
        old = time.time() - age_days * 86400
        os.utime(p, (old, old))
    return p


@pytest.fixture()
def ledger_dir(tmp_path, monkeypatch):
    from aria_service.intel import intel_ledger as il

    d = str(tmp_path)
    canonical = os.path.join(d, "aria_signals.json")
    with open(canonical, "w", encoding="utf-8") as f:
        json.dump({"signals": [{"id": "keep-me"}]}, f)
    monkeypatch.setattr(il, "_DISK_PATH", canonical, raising=False)
    return d, canonical


# ══════════════════════════════════════════════════════════════════════
# 1. THE MEASUREMENT — 382.6 MB must be visible
# ══════════════════════════════════════════════════════════════════════

def test_orphans_are_counted_and_sized(ledger_dir):
    from aria_service.intel import intel_ledger as il
    d, _ = ledger_dir
    _mk(d, ".aria_signals.aaaaaa.json.tmp", 1000, age_days=30)
    _mk(d, ".aria_signals.bbbbbb.json.tmp", 2000, age_days=60)

    rep = il.tmp_orphan_report()

    assert rep["count"] == 2
    assert rep["bytes"] == 3000
    assert rep["oldest_age_days"] >= 59


def test_a_quiet_directory_reports_zero(ledger_dir):
    from aria_service.intel import intel_ledger as il
    rep = il.tmp_orphan_report()
    assert rep["count"] == 0 and rep["bytes"] == 0
    assert rep["would_remove"] == 0


# ══════════════════════════════════════════════════════════════════════
# 2. THE SAFETY BOUNDARIES — what it must never reach
# ══════════════════════════════════════════════════════════════════════

def test_it_never_touches_the_canonical_ledger(ledger_dir, monkeypatch):
    from aria_service.intel import intel_ledger as il
    d, canonical = ledger_dir
    monkeypatch.setenv("ARIA_LEDGER_TMP_SWEEP", "1")
    _mk(d, ".aria_signals.cccccc.json.tmp", 100, age_days=30)

    il.sweep_tmp_orphans()

    assert os.path.exists(canonical), "the canonical ledger was removed"
    with open(canonical, encoding="utf-8") as f:
        assert json.load(f)["signals"][0]["id"] == "keep-me"


def test_it_never_touches_a_fresh_tmp(ledger_dir, monkeypatch):
    """A young temp file may be an IN-FLIGHT write. Removing it corrupts a
    flush that is happening right now."""
    from aria_service.intel import intel_ledger as il
    d, _ = ledger_dir
    monkeypatch.setenv("ARIA_LEDGER_TMP_SWEEP", "1")
    fresh = _mk(d, ".aria_signals.dddddd.json.tmp", 100, age_days=0)
    old = _mk(d, ".aria_signals.eeeeee.json.tmp", 100, age_days=30)

    il.sweep_tmp_orphans()

    assert os.path.exists(fresh), "an in-flight write was destroyed"
    assert not os.path.exists(old)


def test_it_never_touches_unrelated_files(ledger_dir, monkeypatch):
    from aria_service.intel import intel_ledger as il
    d, _ = ledger_dir
    monkeypatch.setenv("ARIA_LEDGER_TMP_SWEEP", "1")
    other = _mk(d, "aria_knowledge.json", 100, age_days=90)
    other2 = _mk(d, ".something_else.abc.json.tmp", 100, age_days=90)

    il.sweep_tmp_orphans()

    assert os.path.exists(other)
    assert os.path.exists(other2), "the sweep is not scoped to its own prefix"


# ══════════════════════════════════════════════════════════════════════
# 3. §26 — report-only unless the operator says otherwise
# ══════════════════════════════════════════════════════════════════════

def test_it_removes_nothing_by_default(ledger_dir, monkeypatch):
    from aria_service.intel import intel_ledger as il
    d, _ = ledger_dir
    monkeypatch.delenv("ARIA_LEDGER_TMP_SWEEP", raising=False)
    p = _mk(d, ".aria_signals.ffffff.json.tmp", 100, age_days=90)

    out = il.sweep_tmp_orphans()

    assert os.path.exists(p), (
        "§26: the reclaim is the operator's call. This must MEASURE by default "
        "and remove only when ARIA_LEDGER_TMP_SWEEP=1."
    )
    assert out["removed"] == 0
    assert out["would_remove"] == 1


def test_a_manifest_is_written_before_anything_is_removed(ledger_dir, monkeypatch):
    """Archive with a manifest — what went must remain knowable."""
    from aria_service.intel import intel_ledger as il
    d, _ = ledger_dir
    monkeypatch.setenv("ARIA_LEDGER_TMP_SWEEP", "1")
    _mk(d, ".aria_signals.gggggg.json.tmp", 4242, age_days=30)

    out = il.sweep_tmp_orphans()

    assert out["removed"] == 1
    mpath = out.get("manifest")
    assert mpath and os.path.exists(mpath), "no manifest was written"
    with open(mpath, encoding="utf-8") as f:
        man = json.load(f)
    entry = man["removed"][0]
    assert entry["bytes"] == 4242
    assert "gggggg" in entry["name"]
    assert entry.get("mtime")
