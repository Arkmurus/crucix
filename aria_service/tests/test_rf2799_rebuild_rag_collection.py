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


# ── R-F2800: purge gates (the one genuinely destructive path) ───────────────

def _two_collection_store(tmp_path: Path, *, parked_ids, live_ids) -> Path:
    """A store holding a PARKED collection and its LIVE replacement."""
    root = tmp_path / "rag2"
    root.mkdir()
    db = sqlite3.connect(root / "chroma.sqlite3")
    db.executescript(
        """
        create table collections (id text primary key, name text);
        create table collection_metadata (collection_id text, key text);
        create table segments (id text primary key, collection text, scope text);
        create table segment_metadata (segment_id text, key text);
        create table max_seq_id (segment_id text, seq_id integer);
        create table embeddings (id integer primary key, segment_id text, embedding_id text);
        create table embedding_metadata (
            id integer, key text, string_value text,
            int_value integer, float_value real, bool_value integer
        );
        """
    )
    n = 0
    for cid, cname, seg_v, seg_m, ids in (
        ("cid-old", "aria_documents__corrupt_x", "segv-old", "segm-old", parked_ids),
        ("cid-new", "aria_documents", "segv-new", "segm-new", live_ids),
    ):
        db.execute("insert into collections values (?,?)", (cid, cname))
        db.execute("insert into segments values (?,?,?)", (seg_v, cid, "VECTOR"))
        db.execute("insert into segments values (?,?,?)", (seg_m, cid, "METADATA"))
        (root / seg_v).mkdir()
        (root / seg_v / "data_level0.bin").write_bytes(b"x" * 16)
        for eid in ids:
            n += 1
            db.execute("insert into embeddings values (?,?,?)", (n, seg_m, eid))
            db.execute("insert into embedding_metadata values (?,?,?,?,?,?)",
                       (n, "chroma:document", f"body {eid}", None, None, None))
    db.commit()
    db.close()
    return root


def test_purge_refuses_when_parked_holds_unique_records(tmp_path: Path):
    """Equal-or-not, what matters is that nothing unique is destroyed (§7)."""
    mod = _load_tool()
    root = _two_collection_store(tmp_path, parked_ids=["a", "b", "c"], live_ids=["a", "b"])
    rc = mod.purge_parked(str(root), "aria_documents__corrupt_x", "aria_documents", apply=True)
    assert rc == 3, "must refuse — 'c' exists only in the parked copy"
    db = sqlite3.connect(root / "chroma.sqlite3")
    still = db.execute("select count(*) from collections where name=?",
                       ("aria_documents__corrupt_x",)).fetchone()[0]
    db.close()
    assert still == 1, "the parked collection must survive a refused purge"
    assert (root / "segv-old").is_dir(), "its index dir must survive too"


def test_purge_refuses_when_live_collection_is_missing(tmp_path: Path):
    mod = _load_tool()
    root = _two_collection_store(tmp_path, parked_ids=["a"], live_ids=["a"])
    rc = mod.purge_parked(str(root), "aria_documents__corrupt_x", "no_such_collection", apply=True)
    assert rc == 2


def test_purge_dry_run_removes_nothing(tmp_path: Path):
    mod = _load_tool()
    root = _two_collection_store(tmp_path, parked_ids=["a", "b"], live_ids=["a", "b"])
    rc = mod.purge_parked(str(root), "aria_documents__corrupt_x", "aria_documents", apply=False)
    assert rc == 0
    assert (root / "segv-old").is_dir(), "dry run must not delete the index dir"
    db = sqlite3.connect(root / "chroma.sqlite3")
    assert db.execute("select count(*) from collections").fetchone()[0] == 2
    db.close()


def test_purge_removes_only_the_parked_collection(tmp_path: Path, monkeypatch):
    """The live collection, its rows and its index dir must be untouched."""
    mod = _load_tool()
    root = _two_collection_store(tmp_path, parked_ids=["a", "b"], live_ids=["a", "b"])
    # verify() would need chromadb + the real embedder; the gate itself is what
    # we are testing here, so stub it green.
    monkeypatch.setattr(mod, "verify", lambda *a, **k: 0)

    rc = mod.purge_parked(str(root), "aria_documents__corrupt_x", "aria_documents", apply=True)
    assert rc == 0

    assert not (root / "segv-old").exists(), "parked index dir must be gone"
    assert (root / "segv-new").is_dir(), "LIVE index dir must be untouched"

    db = sqlite3.connect(root / "chroma.sqlite3")
    names = [r[0] for r in db.execute("select name from collections")]
    assert names == ["aria_documents"], f"only the live collection should remain, got {names}"
    # live records intact
    live_seg = db.execute("select id from segments where collection='cid-new' and scope='METADATA'").fetchone()[0]
    assert db.execute("select count(*) from embeddings where segment_id=?", (live_seg,)).fetchone()[0] == 2
    # parked rows fully gone — no orphans left behind
    assert db.execute("select count(*) from embeddings where segment_id='segm-old'").fetchone()[0] == 0
    assert db.execute("select count(*) from segments where collection='cid-old'").fetchone()[0] == 0
    db.close()


def test_purge_is_a_noop_when_parked_does_not_exist(tmp_path: Path):
    mod = _load_tool()
    root = _two_collection_store(tmp_path, parked_ids=["a"], live_ids=["a"])
    rc = mod.purge_parked(str(root), "never_existed", "aria_documents", apply=True)
    assert rc == 0, "absent parked collection is a clean no-op, not an error"


# ── R-F2808: the §7 guarantee must not pass vacuously ──────────────────────

def test_purge_refuses_when_the_parked_metadata_segment_is_missing(tmp_path: Path):
    """A missing segment made the "would destroy knowledge" refusal a no-op.

    `where segment_id = NULL` matches nothing in SQL, so pids was empty, missing
    was empty, the §7 refusal was skipped — and the rows and index dirs were
    then deleted. The trigger is exactly the sqlite inconsistency this tool
    exists to clean up after.
    """
    mod = _load_tool()
    root = _two_collection_store(tmp_path, parked_ids=["a", "b"], live_ids=["a", "b"])
    db = sqlite3.connect(root / "chroma.sqlite3")
    db.execute("delete from segments where id='segm-old'")   # lose the METADATA segment
    db.commit(); db.close()

    rc = mod.purge_parked(str(root), "aria_documents__corrupt_x", "aria_documents", apply=True)
    assert rc == 3, "must refuse when it cannot prove what the parked copy holds"
    assert (root / "segv-old").is_dir(), "nothing may be deleted on a refusal"
    db = sqlite3.connect(root / "chroma.sqlite3")
    assert db.execute("select count(*) from collections where name=?",
                      ("aria_documents__corrupt_x",)).fetchone()[0] == 1
    db.close()


def test_purge_refuses_when_the_live_collection_has_no_records(tmp_path: Path):
    mod = _load_tool()
    root = _two_collection_store(tmp_path, parked_ids=["a"], live_ids=["a"])
    db = sqlite3.connect(root / "chroma.sqlite3")
    db.execute("delete from segments where id='segm-new'")
    db.commit(); db.close()
    assert mod.purge_parked(str(root), "aria_documents__corrupt_x", "aria_documents",
                            apply=True) == 3


# ── R-F2808: metadata type fidelity across the rebuild ─────────────────────

def test_extraction_preserves_metadata_types(tmp_path: Path):
    """sqlite returns bool_value as 0/1; storing that as an int breaks any
    downstream chroma filter written as {"k": True}."""
    mod = _load_tool()
    root = tmp_path / "rag3"
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
    db.execute("insert into collections values ('c','aria_documents')")
    db.execute("insert into segments values ('sm','c','METADATA')")
    db.execute("insert into embeddings values (1,'sm','doc-0')")
    db.execute("insert into embedding_metadata values (1,'chroma:document','body',NULL,NULL,NULL)")
    db.execute("insert into embedding_metadata values (1,'is_cold',NULL,NULL,NULL,1)")
    db.execute("insert into embedding_metadata values (1,'not_cold',NULL,NULL,NULL,0)")
    db.execute("insert into embedding_metadata values (1,'rank',NULL,7,NULL,NULL)")
    db.execute("insert into embedding_metadata values (1,'score',NULL,NULL,0.75,NULL)")
    db.execute("insert into embedding_metadata values (1,'source',' web ',NULL,NULL,NULL)")
    db.commit(); db.close()

    recs = mod.extract_records(mod._db(str(root)), "sm")
    md = recs[0]["metadata"]
    assert md["is_cold"] is True, f"bool True round-tripped as {md['is_cold']!r}"
    assert md["not_cold"] is False, f"bool False round-tripped as {md['not_cold']!r}"
    assert md["rank"] == 7 and isinstance(md["rank"], int)
    assert md["score"] == 0.75 and isinstance(md["score"], float)
    assert md["source"] == " web "
