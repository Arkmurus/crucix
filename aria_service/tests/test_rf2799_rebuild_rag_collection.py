"""R-F2799 — safety contract for the ChromaDB collection rebuild tool.

The tool exists because the local `aria_documents` collection reached a state
where EVERY chromadb read (count/peek/get) died with a Windows access violation,
which a Python try/except cannot catch — so anything touching it killed the
process, including the binding CLAUDE.md §20 priming step (R-F2798).

The data was never lost: chroma.sqlite3 held all 1895 records with their document
text, readable via plain sqlite3, which bypasses the faulting binding. Only the
HNSW vector index was inconsistent (index files last written Jul 18 23:23; sqlite
updated Jul 19 14:47 — an interrupted write).

These tests pin the properties that make the tool safe to point at real data.
They run against a PURPOSE-BUILT sqlite fixture, never the real store.
"""
from __future__ import annotations

import importlib.util
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
TOOL = REPO / "scripts" / "admin" / "rebuild_rag_collection.py"


def _load_tool():
    spec = importlib.util.spec_from_file_location("rebuild_rag_collection", TOOL)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def _fixture_store(tmp_path: Path, *, records: int = 3, with_docs: bool = True) -> Path:
    """A minimal chroma-shaped sqlite: collections -> segments -> embeddings+metadata."""
    root = tmp_path / "rag"
    root.mkdir()
    db = sqlite3.connect(root / "chroma.sqlite3")
    db.executescript(
        """
        create table collections (id text primary key, name text);
        create table segments (id text primary key, collection text, scope text);
        create table embeddings (id integer primary key, segment_id text, embedding_id text);
        create table embedding_metadata (
            id integer, key text, string_value text,
            int_value integer, float_value real, bool_value integer
        );
        """
    )
    db.execute("insert into collections values ('cid-1','aria_documents')")
    db.execute("insert into segments values ('seg-vec','cid-1','VECTOR')")
    db.execute("insert into segments values ('seg-meta','cid-1','METADATA')")
    for i in range(records):
        db.execute("insert into embeddings values (?,?,?)", (i + 1, "seg-meta", f"doc-{i}"))
        if with_docs:
            db.execute(
                "insert into embedding_metadata values (?,?,?,?,?,?)",
                (i + 1, "chroma:document", f"document body {i}", None, None, None),
            )
        db.execute(
            "insert into embedding_metadata values (?,?,?,?,?,?)",
            (i + 1, "source", f"src-{i}", None, None, None),
        )
        db.execute(
            "insert into embedding_metadata values (?,?,?,?,?,?)",
            (i + 1, "rank", None, i, None, None),
        )
    db.commit()
    db.close()
    return root


# ── extraction: the half that must work while the binding is faulting ───────

def test_extracts_documents_and_metadata_from_sqlite(tmp_path: Path):
    mod = _load_tool()
    root = _fixture_store(tmp_path, records=3)
    db = mod._db(str(root))
    cid = mod.collection_id(db, "aria_documents")
    seg = mod.metadata_segment(db, cid)
    recs = mod.extract_records(db, seg)
    db.close()

    assert len(recs) == 3
    by_id = {r["id"]: r for r in recs}
    assert by_id["doc-1"]["document"] == "document body 1"
    assert by_id["doc-1"]["metadata"]["source"] == "src-1"
    # typed columns must be read from whichever one is populated
    assert by_id["doc-1"]["metadata"]["rank"] == 1
    # the reserved document key must NOT leak into user metadata
    assert "chroma:document" not in by_id["doc-1"]["metadata"]


def test_metadata_segment_is_the_one_that_holds_records(tmp_path: Path):
    """Records hang off the METADATA segment, not the VECTOR segment.

    Reading the VECTOR segment would report 0 and make an intact collection look
    empty — which would then 'justify' swapping a populated collection for an
    empty one. That must not be possible.
    """
    mod = _load_tool()
    root = _fixture_store(tmp_path, records=2)
    db = mod._db(str(root))
    seg = mod.metadata_segment(db, mod.collection_id(db, "aria_documents"))
    db.close()
    assert seg == "seg-meta"


# ── safety guards ───────────────────────────────────────────────────────────

def test_dry_run_is_the_default_and_writes_nothing(tmp_path: Path):
    root = _fixture_store(tmp_path, records=2)
    before = (root / "chroma.sqlite3").read_bytes()
    proc = subprocess.run(
        [sys.executable, str(TOOL), "--collection", "aria_documents", "--rag-path", str(root)],
        capture_output=True, text=True, timeout=300,
    )
    assert proc.returncode == 0, proc.stderr[-500:]
    assert "DRY RUN" in proc.stdout
    assert (root / "chroma.sqlite3").read_bytes() == before, "dry run must not write"


def test_apply_refuses_without_a_verified_backup(tmp_path: Path):
    """--apply must fail CLOSED when no backup is proven to exist."""
    root = _fixture_store(tmp_path, records=2)
    before = (root / "chroma.sqlite3").read_bytes()
    proc = subprocess.run(
        [sys.executable, str(TOOL), "--collection", "aria_documents",
         "--rag-path", str(root), "--apply"],
        capture_output=True, text=True, timeout=300,
    )
    assert proc.returncode == 1
    assert "REFUSING" in proc.stdout
    assert (root / "chroma.sqlite3").read_bytes() == before, "must not write without a backup"


def test_apply_refuses_when_backup_dir_is_not_a_real_backup(tmp_path: Path):
    root = _fixture_store(tmp_path, records=2)
    empty = tmp_path / "not_a_backup"
    empty.mkdir()
    proc = subprocess.run(
        [sys.executable, str(TOOL), "--collection", "aria_documents", "--rag-path", str(root),
         "--apply", "--backup-dir", str(empty)],
        capture_output=True, text=True, timeout=300,
    )
    assert proc.returncode == 1
    assert "NOT A VALID BACKUP" in proc.stdout


def test_unknown_collection_is_an_error_not_a_silent_noop(tmp_path: Path):
    root = _fixture_store(tmp_path, records=1)
    proc = subprocess.run(
        [sys.executable, str(TOOL), "--collection", "does_not_exist", "--rag-path", str(root)],
        capture_output=True, text=True, timeout=300,
    )
    assert proc.returncode == 2
    assert "ERROR" in proc.stdout


def test_refuses_to_swap_when_nothing_is_recoverable(tmp_path: Path):
    """An empty rebuild must never be promoted over a populated collection."""
    mod = _load_tool()
    root = _fixture_store(tmp_path, records=3, with_docs=False)
    rc = mod.rebuild(str(root), "aria_documents", apply=True, batch=8)
    assert rc == 3, "must refuse rather than swap in an empty collection"
