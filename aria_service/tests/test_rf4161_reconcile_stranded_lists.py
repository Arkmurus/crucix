"""R-F4161 (C-180) — stranded legacy list blobs hold entries that are
UNREACHABLE, and reclaiming them must never lose one.

C-178 fixed the cause (`lpush` now migrates before its first write) and
production reclaimed 17.5 MB on its own — the 14.1 MB `crucix:audit:log` blob
migrated on its next push. What remains are lists receiving **no further
pushes**, so nothing triggers their migration.

The bytes are not the point. `lrange` returns early when live rows exist:

    rows = await cur.fetchall()      # from list_entries
    if rows:
        return values[start:end]     # <-- the blob is never read

so an entry living ONLY in the blob cannot be reached through the public API.
Measured on aria-intel 2026-08-19: **38 keys, 1,725,057 bytes, 1,720 entries not
present live** — including `mistake_ledger:by_sig:*` (dedup/lookup inputs) and
`self_metrics:by_domain|by_axis:*` (rollup inputs the predictor reads). Silently
invisible history, not spare disk.

These tests pin the properties that make reclamation safe, on a real SQLite
database built per test — no mocks of the thing under test:

  * nothing is deleted that was not archived AND verified AND superseded
  * merged entries land BELOW the live rows, so ordering is preserved
  * the encoding matches `_migrate_list_if_needed` exactly — a mismatch would
    make every live entry look "missing" and merge duplicates. The first pass at
    the production analysis used `sort_keys=True` and reported 100/100 entries
    unique; that was an artefact of the comparison, and this test exists so the
    tool can never ship with it.
  * dry run is the default and writes nothing
"""
from __future__ import annotations

import importlib.util
import json
import pathlib
import sqlite3

import pytest

_TOOL = (pathlib.Path(__file__).resolve().parents[2]
         / "scripts" / "admin" / "reconcile_stranded_lists.py")


def _load():
    spec = importlib.util.spec_from_file_location("_reconcile_rf4161", _TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)          # type: ignore[union-attr]
    return mod


R = _load()


def _make_db(path: pathlib.Path, *, blob: list, live: list[str]) -> None:
    con = sqlite3.connect(path)
    con.executescript("""
        CREATE TABLE state (key TEXT PRIMARY KEY, value TEXT NOT NULL,
                            kind TEXT NOT NULL DEFAULT 'string', expires_at REAL);
        CREATE TABLE list_entries (list_key TEXT NOT NULL, seq INTEGER NOT NULL,
                                   value TEXT NOT NULL, expires_at REAL,
                                   PRIMARY KEY (list_key, seq));
    """)
    con.execute("INSERT INTO state(key, value, kind) VALUES(?,?,'list')",
                ("k:list", json.dumps(blob)))
    for i, v in enumerate(live):
        con.execute("INSERT INTO list_entries(list_key, seq, value) VALUES(?,?,?)",
                    ("k:list", len(live) - i, v))
    con.commit()
    con.close()


def _rows(path: pathlib.Path):
    con = sqlite3.connect(path)
    out = con.execute(
        "SELECT seq, value FROM list_entries WHERE list_key='k:list' ORDER BY seq DESC"
    ).fetchall()
    blob = con.execute("SELECT COUNT(*) FROM state WHERE key='k:list'").fetchone()[0]
    con.close()
    return out, blob


def test_dry_run_is_the_default_and_writes_nothing(tmp_path):
    db = tmp_path / "s.db"
    _make_db(db, blob=["old1", "old2"], live=["new1"])
    arch = tmp_path / "arch"

    R.reconcile(str(db), apply=False, archive_root=arch)

    rows, blob_present = _rows(db)
    assert blob_present == 1, "dry run deleted the blob"
    assert len(rows) == 1, "dry run merged rows"
    assert not arch.exists(), "dry run created an archive"


def test_apply_merges_only_the_missing_entries(tmp_path):
    db = tmp_path / "s.db"
    # "dup" exists in BOTH — it must not be merged twice
    _make_db(db, blob=["old1", "dup", "old2"], live=["dup", "new1"])
    R.reconcile(str(db), apply=True, archive_root=tmp_path / "arch")

    rows, blob_present = _rows(db)
    values = [v for _, v in rows]
    assert blob_present == 0, "the superseded blob was not removed"
    assert values.count("dup") == 1, f"duplicate merged: {values}"
    assert set(values) == {"dup", "new1", "old1", "old2"}, values


def test_merged_entries_sit_BELOW_the_live_rows(tmp_path):
    """Order is data. The blob holds OLDER history, so it must sort after the
    live rows under `ORDER BY seq DESC`, not jump to the front."""
    db = tmp_path / "s.db"
    _make_db(db, blob=["old_newest", "old_oldest"], live=["live_newest", "live_older"])
    R.reconcile(str(db), apply=True, archive_root=tmp_path / "arch")

    rows, _ = _rows(db)
    values = [v for _, v in rows]
    assert values == ["live_newest", "live_older", "old_newest", "old_oldest"], values


def test_the_encoding_matches_the_migration_exactly(tmp_path):
    """A dict entry already live must be recognised, not merged again.

    `_migrate_list_if_needed` writes `json.dumps(val, default=str)`. If this tool
    encoded differently (e.g. sort_keys=True) every entry would look missing —
    the exact artefact that made the first production analysis report 100/100
    entries unique."""
    same = {"b": 1, "a": 2}
    encoded = json.dumps(same, default=str)           # no sort_keys — key order kept
    db = tmp_path / "s.db"
    _make_db(db, blob=[same], live=[encoded])
    R.reconcile(str(db), apply=True, archive_root=tmp_path / "arch")

    rows, _ = _rows(db)
    assert len(rows) == 1, f"a live entry was merged again: {rows}"


def test_the_archive_is_written_verified_and_manifested(tmp_path):
    db = tmp_path / "s.db"
    _make_db(db, blob=["old1"], live=["new1"])
    arch = tmp_path / "arch"
    R.reconcile(str(db), apply=True, archive_root=arch)

    manifest = json.loads((arch / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["totals"]["keys"] == 1
    entry = manifest["keys"][0]
    assert entry["key"] == "k:list" and entry["merged_entries"] == 1
    body = json.loads((arch / entry["file"]).read_text(encoding="utf-8"))
    import hashlib
    assert hashlib.sha256(body["value"].encode()).hexdigest() == entry["sha256"]
    assert json.loads(body["value"]) == ["old1"], "the archive is not the original blob"


def test_a_blob_is_never_deleted_when_the_archive_cannot_be_written(tmp_path, monkeypatch):
    """The §26 rule, as a test: no destructive step runs ahead of a verified
    archive."""
    db = tmp_path / "s.db"
    _make_db(db, blob=["old1"], live=["new1"])

    real_write = pathlib.Path.write_text

    def _boom(self, *a, **k):
        if self.suffix == ".json" and self.name != "manifest.json":
            raise OSError("disk full")
        return real_write(self, *a, **k)

    monkeypatch.setattr(pathlib.Path, "write_text", _boom)
    R.reconcile(str(db), apply=True, archive_root=tmp_path / "arch")

    rows, blob_present = _rows(db)
    assert blob_present == 1, "blob deleted despite the archive failing"
    assert len(rows) == 1, "entries merged despite the archive failing"


def test_an_unparseable_blob_is_skipped_not_destroyed(tmp_path):
    db = tmp_path / "s.db"
    con = sqlite3.connect(db)
    con.executescript("""
        CREATE TABLE state (key TEXT PRIMARY KEY, value TEXT NOT NULL,
                            kind TEXT NOT NULL DEFAULT 'string', expires_at REAL);
        CREATE TABLE list_entries (list_key TEXT NOT NULL, seq INTEGER NOT NULL,
                                   value TEXT NOT NULL, expires_at REAL,
                                   PRIMARY KEY (list_key, seq));
    """)
    con.execute("INSERT INTO state(key,value,kind) VALUES('k:list','{not json',’list’)"
                .replace("’", "'"))
    con.execute("INSERT INTO list_entries(list_key,seq,value) VALUES('k:list',1,'live')")
    con.commit(); con.close()

    R.reconcile(str(db), apply=True, archive_root=tmp_path / "arch")
    _, blob_present = _rows(db)
    assert blob_present == 1, "an unparseable blob was deleted"


def test_a_list_with_no_live_rows_is_left_to_the_normal_migration(tmp_path):
    """Not stranded: `_migrate_list_if_needed` handles it on the next read or
    push. This tool must not duplicate that path."""
    db = tmp_path / "s.db"
    _make_db(db, blob=["a", "b"], live=[])
    con = sqlite3.connect(db)
    con.execute("DELETE FROM list_entries")
    con.commit(); con.close()

    R.reconcile(str(db), apply=True, archive_root=tmp_path / "arch")
    _, blob_present = _rows(db)
    assert blob_present == 1, "a non-stranded legacy list was touched"
