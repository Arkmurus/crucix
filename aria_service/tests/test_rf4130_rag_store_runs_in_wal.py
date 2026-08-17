"""R-F4130 (C-165) — the RAG store ran in rollback-journal mode, and its fsyncs
froze the whole process.

C-165 attributed §28's surviving open item. Measured on aria-intel at 93 min
uptime::

    PSI cpu     some 3.30s    full 0
    PSI memory  some 0        full 0
    PSI io      some 78.30s   full 77.87s      <- 77.9s of 93 min

`io full` means EVERY runnable task is blocked — the process freezes — which is
exactly the signature §28 could not explain: 50 of 59 stall dumps showing a bare
`asyncio` frame with nothing blocking. Nothing blocks in Python because the block
is beneath it, in the kernel, waiting on the volume.

The writer, attributed to the app process (PID 721, NOT PID 1, which is `init`
and reads a misleading 30 MB): **+647 MB of block writes in 45 s** (~1.24 TB/day)
and ~6,175 write syscalls/sec, while no file grows to match — rewrite and fsync
churn, not new data.

The outlier::

    chroma.sqlite3           journal=delete   sync=FULL   5.08 GB   <- only one
    aria_state.db            journal=wal      sync=FULL   0.63 GB
    aria_knowledge_store.db  journal=wal      sync=FULL   1.42 GB

In rollback-journal mode each commit copies the original pages to a `-journal`,
fsyncs, writes the new pages, fsyncs, then deletes the journal and fsyncs the
directory — roughly three fsyncs per commit against a 5 GB file on a
network-backed volume. Confirmed live, not idle: the rollback journal was present
in 1 of 12 one-second samples with `mtime age 0s`.

**Why this is safe to do in code rather than by hand.** Two other databases on the
SAME volume already run WAL, so the mode is proven on this filesystem. The switch
is a header change, O(1) on a 5 GB file, reversible, and idempotent. It is applied
at the one moment nothing holds the file — after `mkdir`, BEFORE
`PersistentClient` is constructed — so it never races chroma's own connection.

**It must never break boot.** `rag_store` documents a native SIGSEGV in chromadb's
Rust core and carries a crash-loop breaker for it; this helper sits inside that
existing guard, is fully exception-wrapped, and uses a short timeout so a locked
database is skipped rather than waited on. RAG being degraded is survivable; the
service failing to boot is not (§9).
"""
from __future__ import annotations

import sqlite3

import pytest

from aria_service.intel import rag_store as rs


def _make_db(path, mode="delete"):
    c = sqlite3.connect(str(path))
    c.execute(f"pragma journal_mode={mode}")
    c.execute("create table if not exists t (x int)")
    c.execute("insert into t values (1)")
    c.commit()
    c.close()


def _mode(path) -> str:
    c = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return c.execute("pragma journal_mode").fetchone()[0].lower()
    finally:
        c.close()


def test_it_switches_a_rollback_journal_database_to_wal(tmp_path):
    db = tmp_path / "chroma.sqlite3"
    _make_db(db, "delete")
    assert _mode(db) == "delete", "precondition"

    assert rs._ensure_rag_wal(str(tmp_path)) is True
    assert _mode(db) == "wal", (
        "the RAG store is still in rollback-journal mode — every commit costs "
        "~3 fsyncs against a 5 GB file")


def test_it_is_idempotent(tmp_path):
    """WAL is persistent in the file header, so every boot after the first is a
    no-op. It must not thrash the mode."""
    db = tmp_path / "chroma.sqlite3"
    _make_db(db, "wal")
    assert rs._ensure_rag_wal(str(tmp_path)) is True
    assert _mode(db) == "wal"


def test_a_missing_database_is_not_an_error(tmp_path):
    """First boot on a fresh volume: chroma has not created the file yet. That is
    normal, and must not look like a failure."""
    assert rs._ensure_rag_wal(str(tmp_path)) is False


def test_it_never_raises(tmp_path, monkeypatch):
    """§9 — RAG degraded is survivable; failing to boot is not. `rag_store`
    documents a native SIGSEGV in chromadb's core, so nothing added near that
    path may introduce a new way to die."""
    db = tmp_path / "chroma.sqlite3"
    db.write_bytes(b"this is not a sqlite database at all")
    assert rs._ensure_rag_wal(str(tmp_path)) is False   # no exception

    def _boom(*a, **k):
        raise OSError("volume gone")

    monkeypatch.setattr(sqlite3, "connect", _boom)
    assert rs._ensure_rag_wal(str(tmp_path)) is False


def test_it_runs_before_the_client_is_constructed():
    """Ordering is what makes this safe: switching journal_mode requires that no
    other connection holds the database. After `PersistentClient` exists, chroma
    holds it."""
    import inspect

    src = inspect.getsource(rs)
    # Match the REAL construction, not the string that appears inside this
    # module's own docstrings — a first draft matched a docstring at index 14552
    # and reported the call as too late. Literal matching in prose is the
    # R-F3858 class.
    i_call = src.index("_ensure_rag_wal(RAG_PATH)")
    i_client = src.index("local_client = chromadb.PersistentClient(")
    assert i_call < i_client, (
        "the WAL switch must run BEFORE PersistentClient, or it races chroma's "
        "own connection to the same file")


def test_the_switch_is_reported_not_silent():
    """§21a — a mode change to a 5 GB production store must reach the brain, not
    only a debug log."""
    import inspect

    src = inspect.getsource(rs._ensure_rag_wal)
    assert "logger" in src, "the switch is entirely silent"
    assert "R-F4130" in src, "the log line carries no R-number to trace it by"
