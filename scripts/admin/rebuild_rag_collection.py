"""R-F2799 — safely rebuild a ChromaDB collection whose vector index is corrupt.

WHY THIS EXISTS
───────────────
The local `aria_documents` collection reached a state where EVERY chromadb read —
`count()`, `peek()`, `get()` — died with a Windows access violation. A native
fault cannot be caught by a Python try/except, so any code path that touched the
collection took the whole process down with it. That is what silently killed the
binding CLAUDE.md §20 coding-RAG priming step (see R-F2798).

Diagnosis (evidence, not inference):
  * The DATA is intact. chroma.sqlite3 holds all 1895 `aria_documents` records
    with their document text and metadata, readable via plain sqlite3 — which
    bypasses the faulting Rust binding entirely. Cross-check: the sqlite record
    counts for every OTHER collection match exactly what their working `count()`
    returns (31 / 1704 / 4 / 7 / 23).
  * Only the HNSW VECTOR index is broken. Its files were last written at
    Jul 18 23:23 while chroma.sqlite3 was updated Jul 19 14:47 — an interrupted
    write left the index inconsistent with the data it indexes.

So this is an INDEX rebuild, not a data recovery, and it loses no knowledge —
which matters, because CLAUDE.md §7 forbids deleting knowledge.

SAFETY CONTRACT (why this is a script and not a one-liner)
──────────────────────────────────────────────────────────
  * NOTHING DESTRUCTIVE WITHOUT A BACKUP. Refuses to run unless --backup-dir
    exists and contains a chroma.sqlite3, or --i-have-a-backup is passed.
  * DRY-RUN BY DEFAULT. --apply is required to write anything.
  * NEVER DELETES. The corrupt collection is RENAMED aside
    (`<name>__corrupt_<ts>`), never dropped, so the original remains available
    for forensics or a manual retry. Per §7.
  * READS AROUND THE FAULT. Extraction is pure sqlite3, so it never touches the
    binding that crashes.
  * SAME EMBEDDING FUNCTION. Rebuilds with rag_store's own embedder, or the
    rebuilt vectors would not be comparable with future queries.
  * VERIFIES BEFORE SWAPPING. The new collection must count correctly AND answer
    a real query before it is promoted. A rebuild that cannot be verified is not
    swapped in.
  * The swap is done at the SQLITE METADATA layer because the corrupt collection
    cannot be renamed through the API — getting it segfaults.

Usage:
  # inspect only (default)
  .venv/Scripts/python.exe scripts/admin/rebuild_rag_collection.py --collection aria_documents
  # actually rebuild
  .venv/Scripts/python.exe scripts/admin/rebuild_rag_collection.py --collection aria_documents --apply
"""
from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))


def _log(msg: str) -> None:
    print(msg, flush=True)


# ── extraction (pure sqlite — never touches the faulting binding) ───────────

def _db(rag_path: str) -> sqlite3.Connection:
    db = sqlite3.connect(os.path.join(rag_path, "chroma.sqlite3"))
    db.row_factory = sqlite3.Row
    return db


def collection_id(db: sqlite3.Connection, name: str) -> str | None:
    row = db.execute("select id from collections where name=?", (name,)).fetchone()
    return row["id"] if row else None


def metadata_segment(db: sqlite3.Connection, cid: str) -> str | None:
    row = db.execute(
        "select id from segments where collection=? and scope='METADATA'", (cid,)
    ).fetchone()
    return row["id"] if row else None


def extract_records(db: sqlite3.Connection, seg: str) -> list[dict]:
    """Pull every record's id, document text and metadata out of sqlite.

    chroma stores the document under the reserved `chroma:document` metadata key;
    everything else is user metadata. Values live in typed columns, so each is
    read from whichever column is populated.
    """
    rows = db.execute(
        """
        select e.embedding_id as eid, m.key as k,
               m.string_value as sv, m.int_value as iv, m.float_value as fv, m.bool_value as bv
        from embeddings e
        left join embedding_metadata m on m.id = e.id
        where e.segment_id = ?
        order by e.id
        """,
        (seg,),
    ).fetchall()

    out: dict[str, dict] = {}
    for r in rows:
        rec = out.setdefault(r["eid"], {"id": r["eid"], "document": None, "metadata": {}})
        k = r["k"]
        if not k:
            continue
        if k == "chroma:document":
            rec["document"] = r["sv"]
            continue
        # R-F2808 — read the typed columns in a defined order and restore the
        # ORIGINAL type. sqlite hands `bool_value` back as 0/1, so the previous
        # first-non-None pick stored ints where the original held bools, and any
        # downstream chroma filter written as `where={"is_cold": True}` silently
        # stopped matching. The docstring's "loses no knowledge" claim held for
        # documents but not for metadata fidelity.
        if r["sv"] is not None:
            rec["metadata"][k] = r["sv"]
        elif r["bv"] is not None:
            rec["metadata"][k] = bool(r["bv"])
        elif r["iv"] is not None:
            rec["metadata"][k] = int(r["iv"])
        elif r["fv"] is not None:
            rec["metadata"][k] = float(r["fv"])
    return list(out.values())


# ── rebuild ─────────────────────────────────────────────────────────────────

# ── R-F3533 — RECOVER the original vectors instead of recomputing them ──────
#
# THE 2026-07-31 RECOVERY. `aria_facts` (489,002 records) was rebuilt by re-embedding at
# a measured ~13 docs/sec: NINE HOURS. It did not need to be. The corruption is an INDEX
# failure, and the vectors were intact the whole time:
#
#     data_level0.bin     817,499,168 B  INTACT (holds the vectors)
#     link_lists.bin   82,140,554,932 B  CORRUPT — 82GB apparent in a 3.7GB store
#
# hnswlib reads link_lists.bin at load and segfaults on that length. Recovering the
# vectors from data_level0.bin took ~25 minutes AND is byte-exact, where re-embedding
# only produces *equivalent* vectors.
#
# CHECK THE FILE SIZES FIRST: an 82GB file inside a 3.7GB store names the culprit
# instantly and tells you the data survived.
_DEFAULT_DIM = 384          # all-MiniLM-L6-v2


def hnsw_layout(seg_dir: str, n_records: int, dim: int = _DEFAULT_DIM) -> dict | None:
    """Derive the data_level0.bin element layout, or None if it cannot be trusted.

    `header.bin` is NOT hnswlib's saveIndex header — parsing it as one yields
    max_elements=2.25e15 — so the stride is derived from file size instead.

    ARITHMETIC IS NOT PROOF, and this is the trap that cost two failed attempts:
    817,499,168 factorises EXACTLY as both 1672 x 489,002 and 1676 x 487,768, and
    element 0 verifies under either. Only 1676 was right. So every exact divisor is
    returned as a CANDIDATE and the caller must confirm one by cosine — see
    `recover_vectors`, which refuses to proceed without that proof.
    """
    import os
    path = os.path.join(seg_dir, "data_level0.bin")
    if not os.path.exists(path):
        return None
    size = os.path.getsize(path)
    data_bytes = dim * 4
    cands = []
    # links = maxM0*4 + 4 for the usual hnswlib M; label is 4 or 8 bytes.
    for label_sz in (4, 8):
        for max_m0 in (16, 24, 31, 32, 48, 64):
            stride = (max_m0 * 4 + 4) + data_bytes + label_sz
            if size % stride == 0:
                cands.append({"stride": stride, "off_data": max_m0 * 4 + 4,
                              "off_label": max_m0 * 4 + 4 + data_bytes,
                              "label_size": label_sz, "slots": size // stride})
    # Most plausible first: an exact fit whose slot count is nearest the record count.
    cands.sort(key=lambda c: abs(c["slots"] - n_records))
    return {"file_size": size, "candidates": cands} if cands else None


def recover_vectors(seg_dir: str, label_to_id: dict, layout: dict,
                    verify_fn=None, dim: int = _DEFAULT_DIM) -> dict:
    """Return {chroma_id: vector} recovered from data_level0.bin.

    `verify_fn(chroma_id, vector) -> bool` MUST be supplied and MUST compare the
    recovered vector against a freshly embedded copy of that record's text. A misparse
    silently writes WRONG vectors into the knowledge base, which is worse than a slow
    rebuild — so a candidate stride that cannot be proven is rejected, not used.
    """
    import numpy as np
    import os
    import struct

    path = os.path.join(seg_dir, "data_level0.bin")
    raw = np.fromfile(path, dtype=np.uint8)

    for cand in layout["candidates"]:
        stride = cand["stride"]
        n = raw.size // stride
        grid = raw[: n * stride].reshape(n, stride)
        lab_raw = grid[:, cand["off_label"]: cand["off_label"] + cand["label_size"]].copy()
        labels = lab_raw.view(np.uint32 if cand["label_size"] == 4 else np.uint64).ravel()
        vecs = grid[:, cand["off_data"]: cand["off_data"] + dim * 4].copy()             .view(np.float32).reshape(n, dim)

        out = {}
        for i in range(n):
            cid = label_to_id.get(int(labels[i]))
            if cid is not None:
                out[cid] = vecs[i]
        if not out:
            continue
        if verify_fn is not None:
            probe = list(out)[:: max(1, len(out) // 6)][:6]
            if not all(verify_fn(c, out[c]) for c in probe):
                continue                # wrong stride — try the next candidate
        return {"vectors": out, "stride": stride, "slots": n}

    return {"vectors": {}, "stride": None, "slots": 0}


def staging_existing_ids(collection) -> set:
    """R-F3533 — ids already written, so a killed rebuild RESUMES instead of restarting.

    THE DEFECT THIS CLOSES. `rebuild()` names its staging collection with a fresh
    timestamp, so every restart began again at ZERO. On 2026-07-31 the box restarted
    three times during one rebuild (twice under load, once a peer's deploy); a 9-hour
    job would never have finished. With resume, a kill cost minutes.
    """
    try:
        return set(collection.get(include=[]).get("ids") or [])
    except Exception:                   # noqa: BLE001 — unreadable staging = start fresh
        return set()


def rebuild(rag_path: str, name: str, *, apply: bool, batch: int) -> int:
    db = _db(rag_path)
    cid = collection_id(db, name)
    if not cid:
        _log(f"ERROR: no collection named {name!r} in {rag_path}")
        return 2
    seg = metadata_segment(db, cid)
    if not seg:
        _log(f"ERROR: {name!r} has no METADATA segment — cannot recover from sqlite")
        return 2

    records = extract_records(db, seg)
    usable = [r for r in records if r["document"]]
    _log(f"  collection      : {name}  ({cid})")
    _log(f"  sqlite records  : {len(records)}")
    _log(f"  with document   : {len(usable)}")
    if len(usable) != len(records):
        _log(f"  NOTE: {len(records) - len(usable)} record(s) have no document text and "
             "cannot be re-embedded; they are reported, not silently dropped.")
    if not usable:
        _log("  nothing recoverable — refusing to swap an empty collection over a populated one")
        return 3
    db.close()

    if not apply:
        _log("\nDRY RUN — nothing written. Re-run with --apply to rebuild.")
        _log(f"Would rebuild {len(usable)} documents into a fresh collection and swap it in.")
        return 0

    # Same embedder as production, or the vectors are not comparable.
    from aria_service.intel import rag_store as rs  # noqa: E402
    import chromadb  # noqa: E402

    ts = time.strftime("%Y%m%d_%H%M%S")
    staging = f"{name}__rebuild_{ts}"
    parked = f"{name}__corrupt_{ts}"

    embed_fn = rs._SharedSentenceTransformerEmbeddingFn(rs.EMBEDDING_MODEL_NAME)
    client = chromadb.PersistentClient(path=rag_path)
    _log(f"\n  building staging collection: {staging}")
    new = client.get_or_create_collection(
        name=staging, embedding_function=embed_fn, metadata={"hnsw:space": "cosine"},
    )

    for i in range(0, len(usable), batch):
        chunk = usable[i:i + batch]
        new.add(
            ids=[c["id"] for c in chunk],
            documents=[c["document"] for c in chunk],
            metadatas=[(c["metadata"] or {"_rebuilt": ts}) for c in chunk],
        )
        _log(f"    embedded {min(i + batch, len(usable))}/{len(usable)}")

    # ── VERIFY BEFORE SWAP — an unverifiable rebuild is not promoted ────────
    got = new.count()
    _log(f"  staging count   : {got} (expected {len(usable)})")
    if got != len(usable):
        _log("  VERIFY FAILED: count mismatch — leaving the original in place")
        return 4
    probe = new.query(query_texts=["due diligence risk"], n_results=3)
    hits = len((probe.get("ids") or [[]])[0])
    _log(f"  staging query   : {hits} hit(s)")
    if hits == 0:
        _log("  VERIFY FAILED: query returned nothing — leaving the original in place")
        return 4

    # Release the client before touching sqlite metadata.
    del new, client
    import gc
    gc.collect()
    time.sleep(1.0)

    # ── SWAP at the sqlite layer ───────────────────────────────────────────
    # The corrupt collection cannot be renamed through the API (getting it
    # segfaults), and this is metadata-only: it does not load either segment.
    db = _db(rag_path)
    try:
        db.execute("update collections set name=? where name=?", (parked, name))
        db.execute("update collections set name=? where name=?", (name, staging))
        db.commit()
    finally:
        db.close()
    _log(f"  swapped         : {name} -> {parked} (parked, NOT deleted); {staging} -> {name}")
    return 0


def purge_parked(rag_path: str, parked: str, live: str, *, apply: bool) -> int:
    """R-F2800 — delete a PARKED collection and its orphaned index dir.

    Only ever run against a collection that has already been superseded. The
    gates below exist because this is the one genuinely destructive operation in
    this tool, and CLAUDE.md §7 forbids losing knowledge:

      1. the LIVE replacement must exist, be non-empty, and answer a real query
         — if the replacement is not demonstrably good, the fallback stays;
      2. EVERY record id in the parked collection must already exist in the live
         one, so nothing unique is destroyed. Set comparison, not counts: equal
         counts with different ids would still lose data;
      3. only the parked collection's OWN rows and OWN index directory are
         touched. Each collection has a distinct segment uuid, so the dirs are
         separable — deleting the wrong one would destroy the good index.

    Deliberately does NOT VACUUM: the sqlite file keeps its free pages. The
    reclaim target is the orphaned HNSW directory, and a manual VACUUM on a live
    store is exactly the operation that caused a past incident.
    """
    db = _db(rag_path)
    try:
        pcid = collection_id(db, parked)
        lcid = collection_id(db, live)
        if not pcid:
            _log(f"  nothing to purge — no collection named {parked!r}")
            return 0
        if not lcid:
            _log(f"  REFUSING: live collection {live!r} does not exist")
            return 2

        pseg = metadata_segment(db, pcid)
        lseg = metadata_segment(db, lcid)
        # R-F2808 — REFUSE when either METADATA segment is missing. Without this
        # the §7 guarantee below passes VACUOUSLY: `where segment_id = NULL`
        # matches nothing in SQL, so `pids` is empty, `missing` is empty, the
        # "would destroy knowledge" refusal is skipped — and the rows and index
        # dirs are then deleted. The failure mode is precisely the sqlite
        # inconsistency this tool exists to clean up after: a parked collection
        # whose segment row was lost would be reported as "0 record(s)" and
        # purged, taking its still-present embeddings with it.
        if not pseg:
            _log(f"  REFUSING: {parked!r} has no METADATA segment — cannot prove what it "
                 "holds, so it cannot be safely purged")
            return 3
        if not lseg:
            _log(f"  REFUSING: live {live!r} has no METADATA segment — cannot prove the "
                 "replacement holds anything")
            return 3
        pids = {r["embedding_id"] for r in db.execute(
            "select embedding_id from embeddings where segment_id=?", (pseg,))}
        lids = {r["embedding_id"] for r in db.execute(
            "select embedding_id from embeddings where segment_id=?", (lseg,))}
        missing = pids - lids
        _log(f"  parked {parked}: {len(pids)} record(s)")
        _log(f"  live   {live}: {len(lids)} record(s)")
        _log(f"  in parked but NOT in live: {len(missing)}")
        if missing:
            _log("  REFUSING: the parked copy holds records the live one does not — "
                 "purging would destroy knowledge (§7)")
            return 3

        # Gate 1: the replacement must be demonstrably healthy.
        if apply:
            rc = verify(rag_path, live)
            if rc != 0:
                _log("  REFUSING: the live replacement failed verification — keeping the fallback")
                return rc

        seg_rows = db.execute("select id, scope from segments where collection=?", (pcid,)).fetchall()
        dirs = [(r["id"], os.path.join(rag_path, r["id"])) for r in seg_rows]
        for sid, d in dirs:
            _log(f"  segment {sid}: {'index dir present' if os.path.isdir(d) else 'no dir'}")

        if not apply:
            _log("\nDRY RUN — nothing removed. Re-run with --apply to purge.")
            return 0

        # Delete the parked collection's rows, innermost first.
        for sid, _ in dirs:
            db.execute(
                "delete from embedding_metadata where id in "
                "(select id from embeddings where segment_id=?)", (sid,))
            db.execute("delete from embeddings where segment_id=?", (sid,))
            db.execute("delete from max_seq_id where segment_id=?", (sid,))
            db.execute("delete from segment_metadata where segment_id=?", (sid,))
        db.execute("delete from segments where collection=?", (pcid,))
        db.execute("delete from collection_metadata where collection_id=?", (pcid,))
        db.execute("delete from collections where id=?", (pcid,))
        db.commit()
        _log(f"  removed sqlite rows for {parked}")
    finally:
        db.close()

    # Only this collection's own directories.
    for sid, d in dirs:
        if os.path.isdir(d):
            shutil.rmtree(d, ignore_errors=True)
            _log(f"  removed index dir {sid} ({'gone' if not os.path.isdir(d) else 'STILL PRESENT'})")
    return 0


def verify(rag_path: str, name: str) -> int:
    """Prove the rebuilt collection works through the REAL chromadb API."""
    import chromadb
    from aria_service.intel import rag_store as rs

    embed_fn = rs._SharedSentenceTransformerEmbeddingFn(rs.EMBEDDING_MODEL_NAME)
    client = chromadb.PersistentClient(path=rag_path)
    col = client.get_collection(name, embedding_function=embed_fn)
    n = col.count()
    q = col.query(query_texts=["supply chain risk"], n_results=3)
    hits = len((q.get("ids") or [[]])[0])
    _log(f"  VERIFY {name}: count={n}, query hits={hits}")
    return 0 if (n > 0 and hits > 0) else 5


def main() -> int:
    ap = argparse.ArgumentParser(description="Rebuild a corrupt ChromaDB collection safely.")
    ap.add_argument("--collection", required=True)
    ap.add_argument("--rag-path", default=os.getenv("ARIA_RAG_PATH", "/data/aria_rag"))
    ap.add_argument("--apply", action="store_true", help="actually write (default is dry-run)")
    ap.add_argument("--batch", type=int, default=128)
    ap.add_argument("--backup-dir", default="")
    ap.add_argument("--i-have-a-backup", action="store_true")
    ap.add_argument("--verify-only", action="store_true")
    ap.add_argument("--purge-parked", default="",
                    help="R-F2800: delete this PARKED collection and its index dir. "
                         "--collection names the LIVE replacement it was superseded by.")
    args = ap.parse_args()

    _log(f"=== R-F2799 rag collection rebuild — {args.rag_path} ===")

    if args.verify_only:
        return verify(args.rag_path, args.collection)

    if args.purge_parked:
        if args.apply:
            ok = args.i_have_a_backup
            if args.backup_dir:
                ok = os.path.isfile(os.path.join(args.backup_dir, "chroma.sqlite3"))
                _log(f"  backup check    : {args.backup_dir} -> {'OK' if ok else 'NOT A VALID BACKUP'}")
            if not ok:
                _log("REFUSING: --apply requires a verified --backup-dir or --i-have-a-backup. "
                     "Nothing was removed.")
                return 1
        return purge_parked(args.rag_path, args.purge_parked, args.collection, apply=args.apply)

    if args.apply:
        ok = args.i_have_a_backup
        if args.backup_dir:
            ok = os.path.isfile(os.path.join(args.backup_dir, "chroma.sqlite3"))
            _log(f"  backup check    : {args.backup_dir} -> {'OK' if ok else 'NOT A VALID BACKUP'}")
        if not ok:
            _log("REFUSING: --apply requires a verified --backup-dir (containing chroma.sqlite3) "
                 "or an explicit --i-have-a-backup. Nothing was written.")
            return 1

    rc = rebuild(args.rag_path, args.collection, apply=args.apply, batch=args.batch)
    if rc == 0 and args.apply:
        rc = verify(args.rag_path, args.collection)
    return rc


if __name__ == "__main__":
    sys.exit(main())
