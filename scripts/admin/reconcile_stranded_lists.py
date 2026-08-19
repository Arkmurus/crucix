#!/usr/bin/env python3
"""R-F4161 (C-180) — reconcile stranded legacy list blobs.

WHAT IS STRANDED, AND WHY IT IS NOT MERELY BLOAT
------------------------------------------------
R-F1515 moved lists from a JSON blob in `state` (one row per list) to a
row-per-entry `list_entries` table, migrating each key lazily on first read.
R-F4157 (C-178) found that `lpush` never triggered that migration and the read
paths only reach it when `list_entries` is EMPTY — so the first push to a legacy
list created live rows, every later read short-circuited, and the blob was
orphaned.

R-F4157 fixed the cause and production reclaimed 17.5 MB on its own (the 14.1 MB
`crucix:audit:log` blob migrated on its next push). What remains are lists that
receive **no further pushes**, so nothing ever triggers their migration.

The important part is not the bytes. `lrange` returns early when live rows
exist:

    rows = await cur.fetchall()      # from list_entries
    if rows:
        return values[start:end]     # <-- the blob is never read

so every entry that lives ONLY in the blob is **unreachable through the public
API**. Measured on aria-intel 2026-08-19: 38 keys, 1,725,057 bytes, and **1,720
entries not present in the live rows** — including `mistake_ledger:by_sig:*`
(dedup/lookup inputs) and `self_metrics:by_domain|by_axis:*` (rollup inputs the
predictor reads). This is silently invisible history, not spare disk.

WHAT THIS TOOL DOES
-------------------
Per stranded key, in this order, and never out of it:

  1. ARCHIVE the blob verbatim to the archive dir, with a manifest row carrying
     the key, byte count, entry count and a SHA-256 of the exact bytes.
  2. VERIFY the archive by reading it back and re-hashing. A key whose archive
     cannot be verified is SKIPPED — untouched.
  3. MERGE the missing entries into `list_entries` at sequence numbers BELOW the
     current minimum, preserving blob order (index 0 is newest by lpush
     convention). Negative seqs are fine: `ORDER BY seq DESC` still orders
     correctly, `lpush` derives new seqs from MAX(seq)+1, and nothing in
     state_store assumes seq > 0 (verified by grep before writing this).
  4. VERIFY the row count grew by exactly the number merged.
  5. Only then DELETE the legacy `state` row.

§26 governs: "never touch data stores destructively (archive with a manifest;
`rm` is never the answer)". Nothing is deleted that has not been archived,
verified, and superseded by rows proven present.

DRY RUN IS THE DEFAULT. `--apply` is required to write anything.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
import time
from pathlib import Path

DEFAULT_DB = os.getenv("ARIA_STATE_DB") or "/data/aria_state.db"


def _as_migrated(v) -> str:
    """Serialise exactly as `state_store._migrate_list_if_needed` does.

    Getting this wrong is not a cosmetic bug: a mismatched encoding makes every
    blob entry look 'missing', which would merge duplicates of rows that are
    already live. A first pass at the analysis used `sort_keys=True` and
    reported 100/100 entries unique — an artefact of the comparison, not the
    data.
    """
    return v if isinstance(v, str) else json.dumps(v, default=str)


def _safe_name(key: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", key)[:180]


def find_stranded(con: sqlite3.Connection) -> list[dict]:
    rows = con.execute(
        """
        SELECT s.key, s.value, LENGTH(s.value) AS blob_bytes,
               (SELECT COUNT(*) FROM list_entries le WHERE le.list_key = s.key) AS live_rows
        FROM state s
        WHERE s.kind = 'list'
          AND (SELECT COUNT(*) FROM list_entries le WHERE le.list_key = s.key) > 0
        ORDER BY blob_bytes DESC
        """
    ).fetchall()

    out: list[dict] = []
    for key, value, blob_bytes, live_rows in rows:
        try:
            blob = json.loads(value)
        except Exception as e:
            out.append({"key": key, "error": f"unparseable: {e}", "blob_bytes": blob_bytes})
            continue
        if not isinstance(blob, list):
            out.append({"key": key, "error": "not-a-list", "blob_bytes": blob_bytes})
            continue
        live = {r[0] for r in con.execute(
            "SELECT value FROM list_entries WHERE list_key = ?", (key,))}
        missing = [_as_migrated(v) for v in blob if _as_migrated(v) not in live]
        out.append({
            "key": key,
            "raw": value,
            "blob_bytes": blob_bytes,
            "blob_entries": len(blob),
            "live_rows": live_rows,
            "missing": missing,
        })
    return out


def reconcile(db: str, apply: bool, archive_root: Path) -> int:
    con = sqlite3.connect(db, timeout=30.0)
    # The app writes to this database continuously. Without a busy timeout a
    # single concurrent write turns into "database is locked" and the key is
    # skipped — safe (the blob stays put) but it would take several passes to
    # converge. 30s of patience makes one pass enough. WAL means readers never
    # block, so this waits only behind another writer.
    try:
        con.execute("PRAGMA busy_timeout=30000")
    except Exception:
        pass
    stranded = find_stranded(con)

    total_bytes = sum(s.get("blob_bytes", 0) for s in stranded)
    total_missing = sum(len(s.get("missing", [])) for s in stranded)
    print(f"stranded keys        : {len(stranded)}")
    print(f"reclaimable bytes    : {total_bytes:,}")
    print(f"unreachable entries  : {total_missing}")
    print(f"mode                 : {'APPLY' if apply else 'DRY RUN (nothing written)'}")
    print(f"archive dir          : {archive_root}")
    print()

    if not apply:
        for s in stranded[:40]:
            if s.get("error"):
                print(f"  SKIP {s['key'][:60]:60} {s['error']}")
                continue
            print(f"  {s['key'][:60]:60} bytes={s['blob_bytes']:>8} "
                  f"entries={s['blob_entries']:>5} live={s['live_rows']:>5} "
                  f"would_merge={len(s['missing']):>5}")
        print("\nDry run complete. Re-run with --apply to archive, merge and reclaim.")
        return 0

    archive_root.mkdir(parents=True, exist_ok=True)
    manifest_path = archive_root / "manifest.json"
    manifest: list[dict] = []
    merged_keys = failed_keys = 0
    reclaimed = 0

    for s in stranded:
        key = s["key"]
        if s.get("error"):
            print(f"  SKIP {key}: {s['error']}")
            failed_keys += 1
            continue
        raw: str = s["raw"]
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        entry = {
            "key": key,
            "blob_bytes": s["blob_bytes"],
            "blob_entries": s["blob_entries"],
            "live_rows_before": s["live_rows"],
            "merged_entries": len(s["missing"]),
            "sha256": digest,
            "archived_at": time.time(),
            "file": _safe_name(key) + ".json",
        }

        # 1. ARCHIVE
        target = archive_root / entry["file"]
        try:
            target.write_text(json.dumps(
                {"key": key, "sha256": digest, "value": raw}, ensure_ascii=False),
                encoding="utf-8")
        except Exception as e:
            print(f"  SKIP {key}: archive write failed: {e}")
            failed_keys += 1
            continue

        # 2. VERIFY ARCHIVE — read back and re-hash before anything destructive
        try:
            back = json.loads(target.read_text(encoding="utf-8"))
            if hashlib.sha256(back["value"].encode("utf-8")).hexdigest() != digest:
                raise ValueError("sha256 mismatch on read-back")
        except Exception as e:
            print(f"  SKIP {key}: archive verify failed: {e}")
            failed_keys += 1
            continue

        # 3. MERGE missing entries BELOW the existing minimum seq
        try:
            before = con.execute(
                "SELECT COUNT(*) FROM list_entries WHERE list_key=?", (key,)).fetchone()[0]
            min_seq = con.execute(
                "SELECT COALESCE(MIN(seq), 0) FROM list_entries WHERE list_key=?",
                (key,)).fetchone()[0]
            # blob index 0 is NEWEST -> gets the highest of the new (lower) seqs
            n = len(s["missing"])
            for i, val in enumerate(s["missing"]):
                con.execute(
                    "INSERT OR IGNORE INTO list_entries(list_key, seq, value, expires_at) "
                    "VALUES(?,?,?,?)",
                    (key, min_seq - 1 - i, val, None),
                )
            con.commit()

            # 4. VERIFY the rows landed
            after = con.execute(
                "SELECT COUNT(*) FROM list_entries WHERE list_key=?", (key,)).fetchone()[0]
            if after != before + n:
                raise ValueError(f"row count {before} -> {after}, expected +{n}")
        except Exception as e:
            print(f"  SKIP {key}: merge failed ({e}) — blob LEFT IN PLACE")
            failed_keys += 1
            continue

        # 5. Only now remove the superseded blob
        try:
            con.execute("DELETE FROM state WHERE key=? AND kind='list'", (key,))
            con.commit()
        except Exception as e:
            print(f"  WARN {key}: merged but blob delete failed: {e}")

        manifest.append(entry)
        merged_keys += 1
        reclaimed += s["blob_bytes"]
        print(f"  OK   {key[:58]:58} merged={n:>5} reclaimed={s['blob_bytes']:>8}")

    manifest_path.write_text(json.dumps({
        "created_at": time.time(),
        "db": db,
        "keys": manifest,
        "totals": {"keys": merged_keys, "bytes": reclaimed,
                   "entries_merged": sum(m["merged_entries"] for m in manifest)},
    }, indent=2), encoding="utf-8")

    print()
    print(f"reconciled keys : {merged_keys}")
    print(f"skipped keys    : {failed_keys}")
    print(f"bytes reclaimed : {reclaimed:,}")
    print(f"manifest        : {manifest_path}")
    return 0 if failed_keys == 0 else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--apply", action="store_true",
                    help="actually archive, merge and reclaim (default is a dry run)")
    ap.add_argument("--archive-dir", default=None)
    a = ap.parse_args()

    root = Path(a.archive_dir) if a.archive_dir else Path(
        os.path.dirname(a.db) or ".") / "archive" / f"stranded_lists_{int(time.time())}"
    if not Path(a.db).exists():
        print(f"state db not found: {a.db}", file=sys.stderr)
        return 2
    return reconcile(a.db, a.apply, root)


if __name__ == "__main__":
    raise SystemExit(main())
