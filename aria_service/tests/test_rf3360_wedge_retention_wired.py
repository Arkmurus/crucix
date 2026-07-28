"""R-F3360 — the wedge-stack retention cap had one caller, and not the writer that fills the dir.

THE DEFECT. R-F1435 added `_prune_wedge_dir()` (keep <= _MAX_WEDGE_FILES files,
total <= _MAX_WEDGE_DIR_BYTES) after a 128MB dump runaway filled /data on
2026-06-07. It is called from exactly ONE place: the end of
`save_blackout_wedge()` in self_restart.py — i.e. only when the self-restart
blackout path fires.

But that is not the writer that fills the directory. The R-F704 event-loop stall
detector in `main.py` opens its own handle in `lifespan()`
(`wedge_{pid}_{epoch}.log`, append mode) and writes a full `dump_traceback` on
every stall. It never prunes, and it creates a NEW file on every process start —
so the directory grows once per boot, forever.

MEASURED LIVE (aria-intel, 2026-07-28): **513 files**, oldest dated 2026-06-05 —
seven weeks of accumulation against a cap of 50 that had been in place the whole
time. Total was only ~5.2MB, so this was not yet an outage; it is the same
failure class that already caused one, with the guard that was written to stop
it never running on the path that causes it.

THE FIX. Prune at wedge-dir setup in `lifespan()` — once per boot, off the hot
path, immediately before the new log is opened. That is the moment a new file is
added, so it is exactly where the budget should be enforced.

These tests drive the real prune function against a real directory (not a mock)
and assert the WIRING, because a retention cap nobody calls is the whole defect.
"""
from __future__ import annotations

import ast
import os
import time
from pathlib import Path

import pytest

from aria_service.intel import self_restart as sr

MAIN_SRC = (Path(sr.__file__).resolve().parents[1] / "main.py").read_text(encoding="utf-8")


def _mk(dirpath: Path, n: int, size: int = 64, base_mtime: float | None = None) -> list[Path]:
    made = []
    base = base_mtime or (time.time() - 10_000)
    for i in range(n):
        p = dirpath / f"wedge_700_{i:04d}.log"
        p.write_bytes(b"x" * size)
        os.utime(p, (base + i, base + i))      # deterministic age order
        made.append(p)
    return made


# ── the cap itself works on a real directory ────────────────────────────────

def test_prune_enforces_the_file_cap_and_keeps_the_newest(tmp_path):
    cap = sr._MAX_WEDGE_FILES
    _mk(tmp_path, cap + 12)
    assert len(list(tmp_path.iterdir())) == cap + 12
    sr._prune_wedge_dir(str(tmp_path))
    left = sorted(p.name for p in tmp_path.iterdir())
    assert len(left) <= cap, f"cap {cap} not enforced: {len(left)} files remain"
    # oldest deleted first — the 12 lowest indices must be gone
    assert "wedge_700_0000.log" not in left
    assert f"wedge_700_{cap + 11:04d}.log" in left, "newest wedge was deleted"


def test_prune_enforces_the_byte_budget(tmp_path, monkeypatch):
    monkeypatch.setattr(sr, "_MAX_WEDGE_DIR_BYTES", 500)
    monkeypatch.setattr(sr, "_MAX_WEDGE_FILES", 1000)   # isolate the size rule
    _mk(tmp_path, 20, size=100)                          # 2000 bytes total
    sr._prune_wedge_dir(str(tmp_path))
    total = sum(p.stat().st_size for p in tmp_path.iterdir())
    assert total <= 500, f"byte budget not enforced: {total}"


def test_prune_is_best_effort_on_a_missing_dir(tmp_path):
    sr._prune_wedge_dir(str(tmp_path / "does-not-exist"))   # must not raise


def test_prune_leaves_a_dir_under_budget_untouched(tmp_path):
    _mk(tmp_path, 5)
    sr._prune_wedge_dir(str(tmp_path))
    assert len(list(tmp_path.iterdir())) == 5


# ── ROOT GUARD: the R-F704 writer's boot path must actually call it ─────────

def test_lifespan_wedge_setup_prunes_before_opening_a_new_log():
    """The defect was not a broken cap — it was a cap nobody called on the path
    that fills the directory. Assert the call exists in main.py and that it is
    positioned before the new wedge log is opened."""
    assert "prune_wedge_dir" in MAIN_SRC, (
        "the R-F704 wedge writer still never prunes — 513 files accumulated "
        "against a cap of 50 because the only caller was the blackout path"
    )
    prune_at = MAIN_SRC.index("prune_wedge_dir")
    open_at = MAIN_SRC.index("_wedge_log_fh = open(")
    assert prune_at < open_at, (
        "prune must run BEFORE the new log is opened, so the budget accounts "
        "for the file about to be added"
    )


def test_main_parses_after_the_wedge_change():
    """Boot-path edit: a SyntaxError here is a total outage (CLAUDE.md 9/11c)."""
    ast.parse(MAIN_SRC)


def test_prune_is_exposed_for_cross_module_use():
    """main.py must not reach for a private; the retention helper is now part of
    self_restart's surface."""
    assert callable(getattr(sr, "prune_wedge_dir", None))
    assert sr.prune_wedge_dir is not None
