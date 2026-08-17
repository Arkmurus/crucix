"""
Knowledge Base — persistent verified facts, queries, and learnings.
Ported from lib/aria/knowledge.mjs.

Persistence layout (F94, 2026-04-30):
  primary  : disk JSON at /data/aria_knowledge.json  (atomic write)
  hydrate  : disk → legacy Redis blob → empty
  snapshot : periodic copy to Redis for off-host backup (every
             SNAPSHOT_INTERVAL_S, only if dirty since last snapshot)

Why this shape:
  Brain_hook absorbs ~14 knowledge modules per chat turn; each calls
  store_fact() which used to rewrite the entire ~4 MB blob to Redis
  per absorb. That single per-call serialize+SET drove the brain_hook
  circuit breaker (p95 absorb ≈ 2.5s) and threatened the Upstash
  per-value cap (warn at 4 MB / cap ~5 MB). Disk write is local + fast
  (~10 ms for 4 MB on SSD); debounced flush coalesces bursts; Redis is
  reduced to a periodic snapshot so the off-host backup story stays.

API surface is unchanged — every existing caller works without edits.
"""
from __future__ import annotations
# R-F4025 — module-level so tests can monkeypatch `knowledge.wire_*` and so the
# persistence path never pays an import on a failure branch.
from .engine_wiring import wire_failure, wire_success

import asyncio
import base64
import gc
import gzip
import json
import logging
import os
import re
import tempfile
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import redis_store as rs
from .wire import fail_wire  # R-F1789 §21 brain-wiring

logger = logging.getLogger("aria.intel.knowledge")

KEY = "crucix:aria:knowledge"

# R-F334 (2026-05-11) — Redis key-split. Live fly log 21:19:39:
# "Redis SET crucix:aria:knowledge: value size 4023932 bytes exceeds
# warn threshold (4000000)". With ~700 facts/day growth, the single
# gzipped blob will hit Upstash's 25 MB error threshold in ~6 months.
# Sharding moves the canonical Redis backup to N independent keys, each
# ~500KB gzipped, so the per-key write stays well under the warn limit
# and grows by adding shards (not by inflating one giant blob).
#
# Naming:
#   crucix:aria:knowledge:meta        = {"shard_count": N, "version": 2, ...}
#   crucix:aria:knowledge:shard:0..N-1 = gzipped JSON slice of the snapshot
# Legacy single-blob KEY is kept as a read-fallback for one migration
# cycle; new writes go to shards.
SHARD_META_KEY = "crucix:aria:knowledge:meta"
SHARD_KEY_FMT = "crucix:aria:knowledge:shard:{i}"
# Target ~500 KB gzipped per shard — well below the 4 MB warn threshold
# and well above the per-key fixed cost of metadata + envelope. With
# 5-8× gzip ratio this maps to ~2.5-4 MB of raw JSON per shard.
SHARD_TARGET_BYTES = 500_000
SHARD_MAX_FACTS_PER_SHARD = 5000   # hard cap on items per shard regardless of size
# Permanent memory — no TTL, 1M-entry caps (was 30k/20k/10k on 180d TTL).
# Operator explicitly asked for forever memory; disk footprint at current
# ingest is ~50 MB/yr, well inside standard Redis provisioning.
# R-F239 (2026-05-11) — these are WARN THRESHOLDS, not hard caps.
# Per the "ARIA has infinite memory" operator rule
# (memory/aria_infinite_memory.md), ARIA must never forget anything.
# The store-truncation at MAX_*-overflow was a regression that's been
# replaced with a warn-only path. The sentinel value is set high
# enough (100M) that real growth wouldn't trip it for years; if it
# does, the operator gets a brain_hook absorb prompting offload to
# cold storage, NOT deletion of the data.
MAX_FACTS = 100_000_000     # was 1M with truncation; now warn-only at 100M
MAX_QUERIES = 100_000_000   # was 1M with truncation; now warn-only at 100M
MAX_LEARNINGS = 100_000_000 # was 1M with truncation; now warn-only at 100M
WARN_FACTS = int(os.getenv("ARIA_KB_WARN_FACTS", "1000000"))
_kb_warn_throttle = 0

_cache: dict[str, list] | None = None

# ── R-F1622 — O(1) dedup/contradiction indices over _cache["facts"] ──────────
# store_fact used to run THREE O(len(facts)) scans per call (contradictions +
# content-hash dedup + topic dedup). At ~87k facts that was ~3.5s/call — the
# brain_hook "knowledge: timeout (>3.5s)" floods, the absorb circuit tripping
# at p95=120-190s, and a major event-loop wedge contributor (the Python-level
# 87k-dict iteration holds the GIL even when run in a thread). Knowledge is
# append-mostly (§7 never deletes), so these indices are maintained
# incrementally: rebuilt once when the cache (re)loads or its length drifts
# (out-of-band append), updated on each new-fact insert. Lookups become O(1) /
# O(facts-in-this-one-topic) and run INLINE — no to_thread, so no interleave
# window either (the dedup→mutate sequence is now atomic on the loop).
_topic_index: dict[str, list] = {}      # topic.strip().lower() -> [fact refs], newest-first
_content_index: dict[str, dict] = {}    # "content_hash|source_domain" -> fact ref
_index_count: int = -1                  # len(facts) the indices reflect (-1 = unbuilt)
_indexed_cache_id: int = 0              # id(db) the indices were built for

# R-F939 (2026-05-27) — search_knowledge lowercased-text cache. Pre-R-F939
# search_knowledge rebuilt `f"{topic} {content}".lower()` for EVERY fact on
# EVERY chat query (the 7-layer-context "knowledge" layer). At ~67k facts —
# several with 4000-char case-library bodies — that pure-Python string build +
# lower is a CPU hog that, run in the context thread-pool alongside the autonomous
# chain_correlator + the knowledge disk-write, contended for the GIL and produced
# 5-6s event-loop stalls at cold boot / task cycles (wedge_672 2026-05-27
# 10:15-10:22). Cache the lc text per fact id, keyed on content length so an
# in-place content edit (knowledge.py:892/908) rebuilds just that entry; cleared
# wholesale when the facts list is replaced (reload) so stale/removed ids don't
# accumulate. Searches vastly outnumber writes, so steady-state searches now scan
# precomputed strings instead of rebuilding 67k of them per query.
_search_lc: dict[str, tuple[int, str]] = {}
_search_lc_facts_id: int = 0

# Debounced-flush state. Writes mark _dirty; a single background task
# flushes to disk after FLUSH_DEBOUNCE_S. Multiple rapid store_fact()
# calls (brain_hook burst) coalesce into one disk write instead of N.
_dirty: bool = False
_dirty_since_snapshot: bool = False
# R-F3972 (C-61) — monotonic timestamp of the oldest pending BOOKKEEPING-only
# change (a usage counter bump that learned nothing). None when there is none.
# Kept separate from `_dirty` so a duplicate page cannot force a ~300 MB
# canonical+sidecar rewrite; see `_save`.
_dirty_bookkeeping_since: float | None = None
#: How long bookkeeping may wait for a material flush to carry it before it is
#: written on its own. Long, because the data it protects is a counter.
BOOKKEEPING_MAX_AGE_S = 300.0

# ── R-F3985 (C-72) — the sidecar is BOOT acceleration, not a live mirror ─────
# It has exactly one consumer, `_read_from_disk_chunked`, once per process. It
# was rewritten on EVERY canonical flush, doubling the I/O of each flush.
# Writing it on a plain timer would be WRONG: the reader only uses it when its
# `_canonical` marker matches the canonical file, so a lagging sidecar is never
# read and R-F2144's acceleration would be silently deleted while the cost
# merely moved. The right question is "could a boot follow?" — see
# `_should_write_sidecar`.
# SIDECAR_MIN_INTERVAL_S is DERIVED from COMPACT_MAX_AGE_S and so is defined
# below, next to it — see R-F4039 (C-103).
#: None means NEVER WRITTEN in this process. Deliberately not 0.0:
#: `time.monotonic()`'s origin is platform-defined (typically uptime), so
#: 0.0 would mean 'written a long time ago' on one host and 'written just
#: now' on another — the decision must not depend on that.
_last_sidecar_write: float | None = None


def _should_write_sidecar(*, final: bool, now: float | None = None) -> bool:
    """Whether this flush should also refresh the boot sidecar.

    `final=True` (shutdown, explicit flush) ALWAYS writes: a clean restart is
    the common case and the one the sidecar exists for, and writing it in the
    same call as the canonical guarantees the marker matches.

    Otherwise at most once per SIDECAR_MIN_INTERVAL_S, as a crash hedge. That
    hedge is real rather than theoretical because C-61 made flushes
    MATERIAL-only, so quiet periods now exist in which a written sidecar stays
    current.

    A stale sidecar is SAFE by construction: the marker check makes the reader
    fall back to the monolithic load and regenerate off the boot path — the
    same route every fresh deploy already takes.
    """
    if final:
        return True
    if _last_sidecar_write is None:
        return True          # never written in this process
    _now = time.monotonic() if now is None else now
    return (_now - _last_sidecar_write) >= SIDECAR_MIN_INTERVAL_S
_flush_task: asyncio.Task | None = None
_flusher_started: bool = False
_flusher_loop: object | None = None  # R-F3321: the loop _flush_task belongs to
_flusher_stop = False
FLUSH_DEBOUNCE_S = 2.0
SNAPSHOT_INTERVAL_S = 600.0  # 10 min — Redis off-host backup cadence

# ── R-F4022 (C-95) — persistence costs O(change), not O(graph) ──────────────
#
# Every debounced flush used to call `_write_to_disk_atomic`, which serialises
# the WHOLE graph, fsyncs it, renames it and fsyncs the directory. Measured on
# aria-intel 2026-08-14: a 389 MB rewrite completing every ~18-26 s, back to
# back, to persist ~9-11 KB of new content — ~39,000x write amplification, with
# a tmp file present in every sample (the volume never stopped writing).
# `_write_to_disk_atomic` appeared in 18 of 18 loop-stall dumps in a 20-minute
# window (median 6.8 s, max 10.8 s), 12 of them inside `os.fsync`, while the
# main thread sat idle in `selectors.select` — the R-F3252 signature of
# starvation rather than a blocking call.
#
# It is SELF-WORSENING: §7 forbids eviction, so the graph only grows and the
# cost of persisting one fact rises without bound as ARIA learns. Raising
# FLUSH_DEBOUNCE_S would trade durability for latency and leave the O(graph)
# term intact — the §1 band-aid. The complexity had to change, not the cadence.
#
# So the hot path now APPENDS the changed records to a journal (O(change)) and
# the full snapshot is rewritten only on compaction. The journal is an UPSERT
# log keyed by record id — deliberately not positional, because new facts are
# `insert(0, ...)`d at the HEAD, and because the same design then expresses an
# in-place edit for free.
#
# Compaction still happens on a bounded schedule, so boot replay stays small
# and the snapshot never drifts far from the cache.
JOURNAL_MAX_BYTES = int(os.getenv("ARIA_KNOWLEDGE_JOURNAL_MAX_BYTES",
                                  str(32 * 1024 * 1024)))
COMPACT_MAX_AGE_S = float(os.getenv("ARIA_KNOWLEDGE_COMPACT_MAX_AGE_S", "900"))

# R-F4045 (C-105) — never spend an O(N) whole-graph rewrite to retire a journal
# that is a rounding error next to N.
#
# C-95 made the HOT path O(change), but the age trigger below still forced a
# full snapshot every COMPACT_MAX_AGE_S regardless of how little had changed.
# Measured live 2026-08-16: the journal grows ~120 KB / 150 s (~2.9 MB/hour), so
# a 15-minute cycle rewrote **410 MB to retire ~1.4 MB** — ~290x amplification,
# costing ~6.6 s + ~10.3 s of FULL io pressure per compaction. That is C-95's
# own defect on a timer, and C-95's comment names the principle: "The complexity
# had to change, not the cadence."
#
# Expressed as a RATIO, not a byte count, so the bound survives the graph
# growing — §7 forbids eviction, so a fixed threshold would silently decay into
# a no-op exactly as C-103's sidecar throttle did. Amplification is now capped
# at 1/RATIO by construction (20x at 0.05).
#
# What this does NOT relax: `_journal_due` (JOURNAL_MAX_BYTES) still bounds boot
# replay, `final` still compacts on shutdown, and `_needs_compaction` still
# forces a full write after a structural change (a deletion must never be
# expressed as an upsert journal — replaying one would resurrect what was
# purged). Only the *age* trigger is gated.
COMPACT_MIN_JOURNAL_RATIO = float(
    os.getenv("ARIA_KNOWLEDGE_COMPACT_MIN_JOURNAL_RATIO", "0.05"))
COMPACT_MIN_JOURNAL_BYTES = int(
    os.getenv("ARIA_KNOWLEDGE_COMPACT_MIN_JOURNAL_BYTES", str(1024 * 1024)))


def _snapshot_size() -> int:
    """Bytes of the canonical snapshot, or -1 when it cannot be measured."""
    try:
        return os.path.getsize(_DISK_PATH)
    except Exception:
        return -1


def _journal_worth_compacting() -> bool:
    """Is there enough journal to justify rewriting the whole graph?

    Fails SAFE: if the snapshot size cannot be read we return True and compact.
    Skipping a durable write on an unknown is how data quietly stops being
    persisted — the same reasoning as `_save`'s "no declared record => full
    rewrite" default.
    """
    journal = _journal_size()
    if journal <= 0:
        return False
    snapshot = _snapshot_size()
    if snapshot < 0:
        return True          # cannot measure -> do the durable thing
    floor = max(COMPACT_MIN_JOURNAL_BYTES,
                int(COMPACT_MIN_JOURNAL_RATIO * snapshot))
    return journal >= floor

# R-F4039 (C-103) — DERIVED from the compaction cadence, not a magic number.
#
# The sidecar is written ONLY from `_write_to_disk_atomic`, which C-95 made
# COMPACTION-only, so the soonest a second call can arrive is COMPACT_MAX_AGE_S.
# A hedge interval shorter than that can NEVER throttle: at 600 vs 900 the
# "at most once per interval" rule in `_should_write_sidecar` was a no-op and
# every compaction paid a second full-graph write.
#
# Measured live on aria-intel 2026-08-16, one compaction:
#     aria_knowledge.json             410,841,606 B  13:35:49
#     aria_knowledge.json.facts.jsonl 410,823,992 B  13:36:06   (+17s)
# 821 MB per compaction, spanning 6,573 ms then 10,330 ms of FULL io pressure
# (`full` = every runnable task in the VM blocked — the starved-event-loop
# signature). This halves the per-event cost to ~411 MB.
#
# Do NOT restate this as a daily total. Compaction is BOUNDED by
# COMPACT_MAX_AGE_S but only fires when there is something to compact: the same
# sampler observed exactly ONE compaction (at boot, when `_needs_compaction`
# starts True) and then none for the following 900 s, with steady-state writes
# of only 23-37 MB per 30 s window. An earlier draft of this comment assumed
# one compaction every 15 min and inflated the saving accordingly.
#
# Expressed as a multiple so it cannot silently become a no-op again if either
# constant moves; a regression test pins the relationship. Skipping a write is
# SAFE BY CONSTRUCTION — a stale sidecar fails its marker check and the reader
# falls back to the monolithic load, the route every fresh deploy already takes.
SIDECAR_MIN_INTERVAL_S = max(3600.0, 4.0 * COMPACT_MAX_AGE_S)

#: Records changed since the last flush, as (kind, record) — held in memory and
#: written by the debounced flusher, so the hot path never does file I/O (the
#: same reason `_save` only ever set a flag before).
_pending_journal: list[tuple[str, dict]] = []
#: True when the next flush MUST write a full snapshot. Starts True so the
#: first flush of a process establishes a base the journal can build on.
_needs_compaction: bool = True
_last_compaction_at: float | None = None
#: Bookkeeping records (accessCount / last_seen_at) awaiting persistence,
#: keyed by record id so a page re-encountered N times in one window costs one
#: journal line. C-61 kept these off the hot path; this keeps them off the
#: COMPACTION path too — without it `_bk_due` would force a full rewrite every
#: BOOKKEEPING_MAX_AGE_S, and a crawl loop makes that continuous.
_pending_bookkeeping: dict[str, dict] = {}
#: Guards journal file access only (append / truncate) — never held across the
#: snapshot write.
_journal_lock = threading.Lock()
#: Serialises flushes. Two concurrent flushes were previously merely wasteful
#: (two full writes); now one can compact while the other journals, and the
#: compaction's `del _pending_journal[:n]` would drop the wrong entries. It
#: also stops two ~389 MB writes from ever overlapping on the volume.
_flush_lock = asyncio.Lock()


#: R-F4025 (C-97) — §21a wiring for the persistence path, rate-limited.
#:
#: R-F4022 shipped four failure branches as bare `logger` calls, which §21a
#: defines as DARK. They matter more than average: these are the branches where
#: ARIA FORGETS. A failed journal append leaves recent facts only in memory; a
#: failed replay leaves facts on disk unloaded. §7 says losing a fact is never
#: acceptable, and the brain could not see either.
#:
#: Rate-limited because the flusher runs every FLUSH_DEBOUNCE_S (2 s): an
#: unguarded per-failure signal would fill the 500-slot capability ledger in
#: ~17 minutes. This is the same exemption `loop_monitor` and `cost_tracker`
#: already carry, and the same flood shape §17 records for Brave refusals.
PERSISTENCE_WIRE_COOLDOWN_S = 300.0
_persistence_wired_at: dict[str, float] = {}


def _reset_persistence_wire_state() -> None:
    """Test hook — the cooldown is process-global, so it leaks between tests."""
    _persistence_wired_at.clear()


def _wire_persistence(*, source: str, detail: str = "", summary: str = "",
                      ok: bool = False, once: bool = False) -> None:
    """Emit one brain signal per source per cooldown. NEVER raises.

    Observability must not become the outage: if the brain is unreachable, the
    flush still has to complete, or a reporting problem becomes a data-loss
    problem.
    """
    try:
        now = time.monotonic()
        # Keyed by source AND outcome. Sharing one key would let a SUCCESS
        # silence the failure that follows it within the cooldown — the
        # compaction path emits both, so that is not hypothetical: it is what
        # this function did when first written, and the test caught it.
        key = f"{source}:{'ok' if ok else 'fail'}"
        prev = _persistence_wired_at.get(key)
        # `once` is for a STEADY STATE rather than an event. The same precedent
        # CLAUDE.md records for sanctions_coverage_degraded: while the condition
        # holds every occurrence is degraded, so a per-occurrence signal is a
        # ledger-filling flood. A 600 s condition also defeats a 300 s cooldown
        # entirely — it would emit forever, ~144/day.
        if once and prev is not None:
            return
        if prev is not None and now - prev < PERSISTENCE_WIRE_COOLDOWN_S:
            return
        _persistence_wired_at[key] = now
        if ok:
            wire_success(module="knowledge", summary=summary, source_id=source)
        else:
            wire_failure(module="knowledge", detail=detail,
                         gap_type="engine_failure", source=source)
    except Exception:
        pass


def _device_of(path) -> int | None:
    """`st_dev` of the nearest existing directory for `path`, else None.

    Separate and patchable so the off-host decision can be tested
    deterministically — device ids are platform-specific and a temp dir is
    always on the caller's own volume.
    """
    try:
        p = os.path.dirname(str(path)) or "."
        for _ in range(4):                 # walk up to a directory that exists
            if os.path.isdir(p):
                return os.stat(p).st_dev
            parent = os.path.dirname(p)
            if parent == p:
                break
            p = parent
    except OSError:
        pass
    return None


def _snapshot_target_is_offhost() -> bool | None:
    """Is the R-F334 snapshot target a DIFFERENT failure domain to the graph?

    R-F4028 (C-98). R-F334 called this the "Redis off-host backup tier" and it
    was one. R-F745 then flipped the default backend to sqlite and Upstash was
    cancelled (§6/§18), and nothing revisited it — so on production it wrote
    83.7 MB of gzipped shards every 600 s into `/data/aria_state.db`, the SAME
    volume as the `/data/aria_knowledge.json` it backs up. A copy that shares a
    failure domain with its original protects nothing that volume loss would
    not already take, so it was pure cost: ~500 MB/hour plus a whole-graph gzip.

    Returns True (genuinely elsewhere), False (same volume), or **None =
    COULD NOT MEASURE**.

    The tri-state is load-bearing and its safety default is the OPPOSITE of
    `_save`'s. There, an undeclared change must write EVERYTHING. Here, an
    unmeasurable target must keep BACKING UP — "I don't know" is a reason to
    keep a copy, never to silently stop making one.
    """
    try:
        from . import redis_store as _rs
        backend = str(getattr(_rs, "_BACKEND", "") or "").strip().lower()
        if backend and backend != "sqlite":
            # A remote store (upstash/redis) is a real second failure domain.
            return True
        from . import state_store as _ss
        db = getattr(_ss, "_DB_PATH", None)
        if not db:
            db = os.getenv("ARIA_STATE_DB_PATH", "/data/aria_state.db")
        dev_state = _device_of(db)
        dev_graph = _device_of(_DISK_PATH)
        if dev_state is None or dev_graph is None:
            return None
        return dev_state != dev_graph
    except Exception:
        return None


def _should_snapshot(offhost: bool | None) -> bool:
    """Run the snapshot unless we KNOW it is not a backup.

    Only a measured False skips. None (unknown) runs — see
    `_snapshot_target_is_offhost`.
    """
    return offhost is not False


def _journal_path() -> str:
    """Derived from `_DISK_PATH` at call time, not cached at import.

    Tests and dev shells retarget `_DISK_PATH`; a module-level constant would
    leave the journal pointing at the previous volume.
    """
    return _DISK_PATH + ".journal.jsonl"


def _journal_size() -> int:
    try:
        return os.path.getsize(_journal_path())
    except OSError:
        return 0


# R-F4073 (C-125) — only compact the journal once it is worth the read+rewrite.
# Well below JOURNAL_MAX_BYTES and C-105's ratio floor, so redundancy is removed
# BEFORE it can pull a 411 MB snapshot rewrite forward.
JOURNAL_COMPACT_MIN_BYTES = int(
    os.getenv("ARIA_KNOWLEDGE_JOURNAL_COMPACT_MIN_BYTES", str(2 * 1024 * 1024)))


def _compact_journal() -> int:
    """Drop superseded journal entries. Returns bytes reclaimed.

    R-F4073 — WHY. The journal grows with every WRITE, but only needs to grow
    with every distinct RECORD. Measured live 2026-08-16: 369 entries, **64
    distinct ids — 82.7% repeat upserts**, one record rewritten 163 times.
    Because compaction fires on journal SIZE, that redundancy pulls forward
    every whole-graph rewrite by ~5.8x.

    WHY IT IS SAFE. `_replay_journal` is an id-keyed UPSERT, so the final state
    depends only on the last write per id; superseded entries cannot affect it.

    THE SUBTLE PART. Replay inserts an unseen record at the HEAD, preserving the
    newest-first order `store_fact`'s `insert(0, ...)` establishes. Head-insert
    order therefore follows FIRST appearance while content follows LAST write —
    so this keeps **first-appearance order with last-write content**. Keeping the
    last occurrence instead would silently reorder newly inserted facts.

    Entries with no id cannot be deduped and are preserved verbatim; a corrupt
    line is preserved too, because the journal holds every fact written since
    the last snapshot and guessing is not worth losing memory over (§7).

    Atomic (tmp + fsync + rename) for the same reason.
    """
    path = _journal_path()
    try:
        if not os.path.exists(path):
            return 0
        before = os.path.getsize(path)
    except OSError:
        return 0

    order: list[str] = []           # first-appearance order of keyed records
    latest: dict[str, str] = {}     # id -> most recent raw line
    passthrough: list[str] = []     # unkeyed/corrupt lines, preserved verbatim
    total = 0

    try:
        with open(path, "r", encoding="utf-8") as fh:
            for raw in fh:
                line = raw.strip()
                if not line:
                    continue
                total += 1
                rid = None
                try:
                    entry = json.loads(line)
                    rec = entry.get("rec")
                    if isinstance(rec, dict):
                        rid = rec.get("id")
                except Exception:
                    rid = None      # corrupt — preserve verbatim
                if rid:
                    if rid not in latest:
                        order.append(rid)
                    latest[rid] = line
                else:
                    passthrough.append(line)
    except OSError:
        return 0

    kept = len(order) + len(passthrough)
    if kept == 0 or kept == total:
        return 0                    # nothing superseded — don't pay for a rewrite

    lines = [latest[r] for r in order] + passthrough

    target_dir = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(prefix=".aria_journal.", suffix=".jsonl.tmp",
                               dir=target_dir)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            for ln in lines:
                fh.write(ln + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
        _fsync_dir(target_dir)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise

    try:
        return max(0, before - os.path.getsize(path))
    except OSError:
        return 0


def _append_journal(entries: list[tuple[str, dict]]) -> None:
    """Append changed records durably. Sync — always called via to_thread.

    fsync'd per flush, so the crash window is the SAME ~2 s debounce as before:
    this makes persistence cheaper, never weaker (§7).
    """
    if not entries:
        return
    path = _journal_path()
    parent = os.path.dirname(path) or "."
    try:
        os.makedirs(parent, exist_ok=True)
    except OSError:
        pass
    with _journal_lock:
        with open(path, "a", encoding="utf-8") as fh:
            for kind, rec in entries:
                fh.write(json.dumps({"kind": kind, "rec": rec},
                                    ensure_ascii=False, default=str) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
    _fsync_dir(parent)


def _truncate_journal_after_compaction(size_before: int) -> None:
    """Drop only the journal bytes the just-written snapshot already contains.

    A blind delete would lose any record appended WHILE the snapshot was being
    serialised — `_write_to_disk_atomic` iterates a shallow copy, so a fact
    inserted after that copy is taken is in neither the snapshot nor, after a
    delete, the journal. Keeping the tail makes the race harmless.
    """
    path = _journal_path()
    with _journal_lock:
        try:
            current = os.path.getsize(path)
        except OSError:
            return
        try:
            if current <= size_before:
                os.unlink(path)
                return
            with open(path, "rb") as fh:
                fh.seek(size_before)
                tail = fh.read()
            tmp = path + ".tmp"
            with open(tmp, "wb") as fh:
                fh.write(tail)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, path)
        except OSError:
            # Raised to the ONE call site, which wires it and swallows it there.
            # Handling it here as well would either double-report or (worse)
            # hide it from the call site that knows the flush succeeded.
            raise


def _replay_journal(data: dict | None) -> dict | None:
    """Upsert journalled records onto a freshly loaded snapshot.

    Keyed by `id`, so replaying a record that the snapshot ALREADY contains is
    a no-op rather than a duplicate — which matters because compaction and
    journal-append legitimately race (see `_truncate_journal_after_compaction`).
    New records are inserted at the head to preserve the newest-first ordering
    that `store_fact`'s `insert(0, ...)` establishes.
    """
    if not isinstance(data, dict):
        return data
    path = _journal_path()
    if not os.path.exists(path):
        return data

    _lists = {"fact": "facts", "query": "queries", "learning": "learnings"}
    _index: dict[str, dict[str, dict]] = {}
    for kind, key in _lists.items():
        seq = data.get(key)
        if not isinstance(seq, list):
            data[key] = seq = []
        _index[kind] = {
            r["id"]: r for r in seq
            if isinstance(r, dict) and r.get("id")
        }

    applied = 0
    corrupt = 0
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    kind = entry.get("kind")
                    rec = entry.get("rec")
                    key = _lists.get(kind)
                    if not key or not isinstance(rec, dict):
                        corrupt += 1
                        continue
                    rid = rec.get("id")
                    if not rid:
                        corrupt += 1
                        continue
                    existing = _index[kind].get(rid)
                    if existing is not None:
                        existing.clear()
                        existing.update(rec)
                    else:
                        data[key].insert(0, rec)
                        _index[kind][rid] = rec
                    applied += 1
                except (json.JSONDecodeError, TypeError, AttributeError):
                    # A torn final line is EXPECTED after a crash mid-append.
                    # Skip it and keep every complete record before it — the
                    # alternative (discarding the journal) would forget facts.
                    corrupt += 1
    except OSError as e:
        # The most dangerous branch in this module: the facts ARE on disk and
        # are not being loaded. §7 — this must never be a log line alone.
        logger.error("knowledge: journal replay failed, snapshot only: %s", e)
        _wire_persistence(
            source="knowledge:journal_replay",
            detail=(f"journal replay FAILED ({e}) — facts written since the "
                    f"last compaction are on disk at {_journal_path()} but were "
                    f"NOT loaded into the cache; §7 forget risk"),
        )
        return data

    if applied or corrupt:
        logger.info(
            "knowledge: R-F4022 replayed %d journalled records (%d unreadable) "
            "onto the snapshot", applied, corrupt,
        )
    return data


def _resolve_disk_path() -> str:
    """Match rag_store's resolution rules so the same volume is used.
    Override with ARIA_KNOWLEDGE_PATH for tests / dev shells. Falls back
    to the OS temp dir on hosts without /data (Windows dev, CI)."""
    override = os.getenv("ARIA_KNOWLEDGE_PATH", "").strip()
    if override:
        return override
    if Path("/data").exists() and os.access("/data", os.W_OK):
        return "/data/aria_knowledge.json"
    fallback = os.path.join(tempfile.gettempdir(), "aria_knowledge.json")
    logger.warning(
        "knowledge: /data volume not mounted — falling back to %s. "
        "State will NOT persist across restarts. Mount a fly.io volume "
        "at /data to enable persistence.",
        fallback,
    )
    return fallback


_DISK_PATH = _resolve_disk_path()


# Redis snapshot is the off-host backup tier — disk is canonical post-F94.
# At ~700 facts/day the raw-JSON blob crossed Upstash's 4 MB warn threshold
# (5.72 MB at 8508 facts on 2026-05-01) and would hit the 25 MB error
# threshold in ~24 days. JSON of facts compresses 5-8× with gzip, so a
# small base64+gzip wrapper buys multi-month headroom without the
# operational complexity of sharding. Magic prefix lets the loader
# distinguish gzipped values from a legacy raw-JSON blob.
_GZ_PREFIX = "GZ1:"


def _encode_snapshot(obj: dict) -> str:
    # R-F727 (2026-05-19): same GIL fast-path as _write_to_disk_atomic
    # — sharded snapshot encode runs in to_thread per R-F714 but
    # `default=str` forces CPython's pure-Python encoder which holds the
    # GIL through the iteration. wedge_673 captured this exact pattern.
    try:
        raw = json.dumps(obj).encode("utf-8")
    except TypeError:
        raw = json.dumps(obj, default=str).encode("utf-8")
    gz = gzip.compress(raw, compresslevel=6)
    return _GZ_PREFIX + base64.b64encode(gz).decode("ascii")


# R-F334 (2026-05-11) — sharded snapshot helpers.

def _split_into_shards(cache: dict) -> list[dict]:
    """Split the in-memory cache into shards each <= SHARD_TARGET_BYTES
    gzipped. Each shard is a self-contained dict with the same top-level
    keys (facts/queries/learnings/version) but only a SLICE of the
    lists. Read-side concatenates them back in order.

    Algorithm: round-robin distribute items across N shards where N is
    chosen so each shard fits the size target. Estimate N from total
    items × average bytes-per-item.
    """
    if not cache:
        return []
    facts = cache.get("facts") or []
    queries = cache.get("queries") or []
    learnings = cache.get("learnings") or []
    version = cache.get("version", 1)

    total_items = len(facts) + len(queries) + len(learnings)
    if total_items == 0:
        # Empty cache — one shard with the version header
        return [{"facts": [], "queries": [], "learnings": [], "version": version,
                 "shard_index": 0, "shard_count": 1}]

    # Estimate shard count. Encode a 100-item sample to measure
    # bytes-per-item, then size from there. Floor at 1 shard.
    sample = {
        "facts": facts[:100], "queries": queries[:100],
        "learnings": learnings[:100], "version": version,
    }
    try:
        sample_bytes = len(_encode_snapshot(sample))
        sample_items = (len(sample["facts"]) + len(sample["queries"])
                        + len(sample["learnings"]))
        bytes_per_item = sample_bytes / max(sample_items, 1)
    except Exception:
        bytes_per_item = 200  # conservative fallback

    items_per_shard = max(
        100,
        min(
            SHARD_MAX_FACTS_PER_SHARD,
            int(SHARD_TARGET_BYTES / max(bytes_per_item, 1)),
        ),
    )
    n_shards = max(1, (total_items + items_per_shard - 1) // items_per_shard)

    shards: list[dict] = []
    for i in range(n_shards):
        # Slice each list into the i-th chunk
        start_f = (len(facts) * i) // n_shards
        end_f = (len(facts) * (i + 1)) // n_shards
        start_q = (len(queries) * i) // n_shards
        end_q = (len(queries) * (i + 1)) // n_shards
        start_l = (len(learnings) * i) // n_shards
        end_l = (len(learnings) * (i + 1)) // n_shards
        shards.append({
            "facts": facts[start_f:end_f],
            "queries": queries[start_q:end_q],
            "learnings": learnings[start_l:end_l],
            "version": version,
            "shard_index": i,
            "shard_count": n_shards,
        })
    return shards


def _merge_shards(shards: list[dict]) -> dict | None:
    """Reassemble shards into a single cache dict. Ordered by shard_index."""
    if not shards:
        return None
    sorted_shards = sorted(
        shards, key=lambda s: s.get("shard_index", 0) if isinstance(s, dict) else 0,
    )
    merged_facts: list = []
    merged_queries: list = []
    merged_learnings: list = []
    version = 1
    for s in sorted_shards:
        if not isinstance(s, dict):
            continue
        merged_facts.extend(s.get("facts") or [])
        merged_queries.extend(s.get("queries") or [])
        merged_learnings.extend(s.get("learnings") or [])
        version = s.get("version", version)
    return {
        "facts": merged_facts,
        "queries": merged_queries,
        "learnings": merged_learnings,
        "version": version,
    }


async def _save_sharded_snapshot(cache: dict) -> dict:
    """R-F334: write the cache as N sharded keys + a meta key.
    Returns {shard_count, total_bytes, items} for the caller's log line."""
    # R-F714 (2026-05-19): the per-shard _encode_snapshot loop ran
    # synchronously on the event loop — with 18 shards × ~500KB gzip
    # each, live fly stacks captured 19-25s wedges here. Encode in a
    # worker thread so the loop stays responsive to other coroutines.
    def _encode_all():
        s = _split_into_shards(cache)
        encoded = []
        total = 0
        for sh in s:
            enc = _encode_snapshot(sh)
            total += len(enc)
            encoded.append((sh["shard_index"], enc))
        return s, encoded, total

    # R-F787 (2026-05-21) — gate via the global snapshot semaphore so
    # this encoder thread doesn't run concurrently with intel_ledger
    # and neural_memory encoders. The R-F714 to_thread wrap stopped
    # the LOOP doing this work, but with all three modules flushing
    # in the same brain_hook absorb, three GIL-holding threads could
    # starve the loop for 30s+. Throttle to 1 at a time.
    from ._snapshot_throttle import run_in_thread_throttled
    shards, encoded_shards, total_bytes = await run_in_thread_throttled(_encode_all)
    write_tasks = [
        rs.set(SHARD_KEY_FMT.format(i=idx), enc) for idx, enc in encoded_shards
    ]

    # Write all shards concurrently
    if write_tasks:
        await asyncio.gather(*write_tasks, return_exceptions=True)

    # Write meta last so the reader either sees the COMPLETE state
    # or falls back to legacy. (Atomic-ish across keys.)
    n_shards = len(shards)
    meta = {
        "version": 2,
        "shard_count": n_shards,
        "total_bytes": total_bytes,
        "items": {
            "facts": len(cache.get("facts") or []),
            "queries": len(cache.get("queries") or []),
            "learnings": len(cache.get("learnings") or []),
        },
        "written_at": time.time(),
    }
    await rs.set(SHARD_META_KEY, json.dumps(meta))

    # Best-effort delete of stale legacy blob — only after the shards
    # are durable. If this fails, _load() prefers shards over legacy
    # so the legacy stays as harmless dead data until the next write.
    try:
        # Note: rs.delete might not exist on all backends; check first
        if hasattr(rs, "delete"):
            await rs.delete(KEY)
    except Exception:
        pass

    return {
        "shard_count": n_shards,
        "total_bytes": total_bytes,
        "items": meta["items"],
    }


async def _load_sharded_snapshot() -> dict | None:
    """R-F334: read the sharded snapshot via meta + N shard gets in
    parallel. Returns the reassembled cache dict, or None if meta is
    absent (caller falls back to legacy single-blob)."""
    try:
        meta_raw = await rs.get(SHARD_META_KEY)
    except Exception as e:
        logger.debug("R-F334 meta read failed: %s", e)
        return None
    if not meta_raw:
        return None
    try:
        if isinstance(meta_raw, (bytes, bytearray)):
            meta_raw = meta_raw.decode("utf-8")
        meta = json.loads(meta_raw) if isinstance(meta_raw, str) else meta_raw
    except Exception as e:
        logger.warning("R-F334 meta parse failed: %s", e)
        return None
    n_shards = int(meta.get("shard_count", 0))
    if n_shards <= 0:
        return None

    # Parallel-fetch all shard keys
    get_tasks = [
        rs.get(SHARD_KEY_FMT.format(i=i)) for i in range(n_shards)
    ]
    raws = await asyncio.gather(*get_tasks, return_exceptions=True)
    decoded: list[dict] = []
    for i, raw in enumerate(raws):
        if isinstance(raw, Exception):
            logger.warning("R-F334 shard %d read errored: %s", i, raw)
            continue
        s = _decode_snapshot(raw)
        if s is None:
            # _decode_snapshot only accepts dicts with 'facts' key.
            # Shards always have it so a None here is a real miss.
            logger.warning("R-F334 shard %d decoded to None", i)
            continue
        # The shard dict may be missing shard_index if it was a partial
        # write; default to i.
        if isinstance(s, dict) and "shard_index" not in s:
            s["shard_index"] = i
        decoded.append(s)
    if not decoded:
        return None
    merged = _merge_shards(decoded)
    if merged:
        logger.info(
            "R-F334: loaded sharded snapshot — %d shards, %d facts, %d queries, "
            "%d learnings",
            n_shards,
            len(merged.get("facts", [])),
            len(merged.get("queries", [])),
            len(merged.get("learnings", [])),
        )
    return merged


def _decode_snapshot(value: Any) -> dict | None:
    """Decode a Redis snapshot. Returns None if the value is empty or
    unparseable. Accepts both new gzipped payloads (prefix `GZ1:`) and
    legacy raw-JSON blobs so existing snapshots migrate forward on the
    next read. Tolerates both str and bytes (the Redis client returns
    either depending on decode_responses)."""
    if not value:
        return None
    if isinstance(value, dict):
        return value if "facts" in value else None
    if isinstance(value, (bytes, bytearray)):
        try:
            value = value.decode("utf-8")
        except Exception:
            return None
    if not isinstance(value, str):
        return None
    if value.startswith(_GZ_PREFIX):
        try:
            gz = base64.b64decode(value[len(_GZ_PREFIX):])
            raw = gzip.decompress(gz)
            data = json.loads(raw)
            if isinstance(data, dict) and "facts" in data:
                return data
        except Exception as e:
            logger.warning("knowledge: gzip snapshot decode failed: %s", e)
        return None
    try:
        data = json.loads(value)
        if isinstance(data, dict) and "facts" in data:
            return data
    except Exception:
        pass
    return None


def _read_from_disk() -> dict | None:
    try:
        with open(_DISK_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and "facts" in data:
            return data
    except FileNotFoundError:
        return None
    except Exception as e:
        logger.warning("knowledge: disk load failed at %s: %s", _DISK_PATH, e)
    return None


# ── R-F2144: derived JSONL facts sidecar for chunked, loop-safe boot reads ──
# The canonical store stays the monolithic aria_knowledge.json (write path
# unchanged — already GIL-yielding via R-F1668). But a single json.load of the
# ~223k-fact blob at boot holds the GIL for the whole parse+object-construction
# and starves the one event loop (the 2026-06-29 warmup starvation; verified:
# to_thread/orjson do NOT help a monolithic GIL-bound parse). This sidecar
# stores facts LINE-DELIMITED so boot can stream them in chunks with
# `await asyncio.sleep(0)` yields. It is DERIVED + best-effort: written beside
# the canonical file, and a missing/stale/corrupt sidecar simply FALLS BACK to
# the monolithic load (today's behaviour) — so a sidecar bug can never lose data.

def _sidecar_paths() -> tuple[str, str]:
    base = str(_DISK_PATH)
    return base + ".facts.jsonl", base + ".meta.json"


def _canonical_marker() -> dict | None:
    """mtime+size of the canonical file — used to verify a sidecar is current."""
    try:
        st = os.stat(_DISK_PATH)
        return {"mtime": st.st_mtime, "size": st.st_size}
    except OSError:
        return None


def _write_facts_sidecar(data: dict, marker: dict | None = None) -> None:
    """Best-effort: (re)write the derived JSONL facts + meta sidecar from `data`.
    Never raises — the canonical write already succeeded; this is a read
    optimisation. GIL-yielding (time.sleep(0)) so it is safe in the flush thread.

    `marker` lets a caller pin the canonical mtime+size that `data` came from
    (the fallback-regen path), so a concurrent canonical rewrite can't leave a
    stale-but-marker-valid sidecar. When None, the marker is taken now (the
    canonical write path, where the file was just written)."""
    if not (isinstance(data, dict) and isinstance(data.get("facts"), list)):
        return
    jsonl_path, meta_path = _sidecar_paths()
    target_dir = os.path.dirname(jsonl_path) or "."
    try:
        fd, tmp = tempfile.mkstemp(prefix=".aria_kn_facts.", suffix=".jsonl.tmp", dir=target_dir)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                for _i, _fact in enumerate(list(data["facts"])):
                    f.write(json.dumps(_fact, default=str))
                    f.write("\n")
                    if (_i & 0x7FF) == 0:
                        time.sleep(0)  # release GIL between chunks
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, jsonl_path)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        meta = {k: v for k, v in data.items() if k != "facts"}
        meta["_canonical"] = marker if marker is not None else _canonical_marker()
        meta["_n_facts"] = len(data["facts"])
        fd2, tmp2 = tempfile.mkstemp(prefix=".aria_kn_meta.", suffix=".json.tmp", dir=target_dir)
        try:
            with os.fdopen(fd2, "w", encoding="utf-8") as f:
                json.dump(meta, f, default=str)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp2, meta_path)
        except Exception:
            try:
                os.unlink(tmp2)
            except OSError:
                pass
            raise
    except Exception as e:  # best-effort — never fatal
        logger.debug("knowledge: facts sidecar write skipped (non-fatal): %s", e)


async def _read_from_disk_chunked() -> dict | None:
    """R-F2144: prefer the derived JSONL sidecar — stream facts in GIL-yielding
    chunks so a boot load never starves the event loop. The sidecar is used only
    when it is CURRENT vs the canonical file (mtime+size match, or the canonical
    file is absent and the sidecar is the only surviving copy). Absent/stale/
    corrupt → fall back to the monolithic load and regenerate the sidecar off the
    boot path so the next boot is fast."""
    jsonl_path, meta_path = _sidecar_paths()
    try:
        if os.path.exists(jsonl_path) and os.path.exists(meta_path):
            with open(meta_path, "r", encoding="utf-8") as f:
                meta = json.load(f)
            if isinstance(meta, dict):
                marker = meta.get("_canonical")
                current = _canonical_marker()
                if marker == current or current is None:
                    facts: list = []
                    with open(jsonl_path, "r", encoding="utf-8") as f:
                        for _i, line in enumerate(f):
                            line = line.strip()
                            if not line:
                                continue
                            facts.append(json.loads(line))
                            if (_i & 0x3FF) == 0:        # every 1024 facts
                                await asyncio.sleep(0)   # yield the event loop
                    data = {k: v for k, v in meta.items()
                            if k not in ("_canonical", "_n_facts")}
                    data["facts"] = facts
                    logger.info(
                        "knowledge: loaded %d facts from JSONL sidecar "
                        "(R-F2144 chunked, loop-safe)", len(facts))
                    return data
    except Exception as e:
        logger.warning("knowledge: sidecar read failed, falling back to monolithic: %s", e)

    # Fallback: the canonical monolithic load (today's behaviour, lossless).
    data = _read_from_disk()
    if data is not None:
        try:  # regenerate the sidecar off the boot critical path; pin the
            # marker to the file we just read so a concurrent rewrite can't
            # leave a stale-but-marker-valid sidecar.
            _mk = _canonical_marker()
            asyncio.create_task(asyncio.to_thread(_write_facts_sidecar, data, _mk))
        except Exception:
            pass
    return data


def _fsync_dir(dir_path: str) -> None:
    """R-F1420 — fsync a directory so a contained rename is durable.
    Best-effort: directory fsync is unsupported on some platforms
    (notably Windows), so any failure is swallowed — the file-level
    fsync is the load-bearing durability guarantee; this hardens the
    rename entry on filesystems that support it (Linux/prod)."""
    try:
        dfd = os.open(dir_path, os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    except (OSError, AttributeError, ValueError):
        pass


def _write_to_disk_atomic(data: dict, write_sidecar: bool = True) -> None:
    """Atomic write via temp file + rename so a crash mid-write can't
    corrupt the canonical knowledge file.

    R-F727 (2026-05-19): json.dump fast path without `default=`. The
    C-accelerated `_json` encoder releases the GIL between operations;
    passing `default=str` forces the pure-Python encoder, which holds
    the GIL through the whole serialisation. See wedge_673 root-cause:
    5 worker threads (3× neural_memory encode + 1× ledger dump + this
    one) all in pure-Python json frames simultaneously starved the
    event loop for 213.97s. Fast path first; fall back to default=str
    on TypeError to stay safe against any stray non-native value."""
    target = _DISK_PATH
    target_dir = os.path.dirname(target) or "."
    # mkstemp in the same directory so rename is atomic on the same FS.
    fd, tmp_path = tempfile.mkstemp(
        prefix=".aria_knowledge.", suffix=".json.tmp", dir=target_dir,
    )
    # R-F1621 — disable cyclic GC for the duration of the dump. ROOT CAUSE of
    # the recurring 5-16s event-loop wedge (wedge_674: main blocker is THIS
    # json.dump, not aiosqlite as the profiler's aggregate frames suggested):
    # serialising the ~87k-fact graph allocates a flood of short-lived str
    # objects, which repeatedly trips gen0/gen1 collections; each collection
    # also scans the huge long-lived graph in older generations, holding the
    # GIL in C and starving the asyncio loop between the encoder's write-chunk
    # yields. Disabling GC across the dump removes that collection cost; the
    # transient garbage is reclaimed normally once GC is re-enabled. Pairs with
    # the boot-time gc.freeze() (main._freeze_long_lived_state) that moves the
    # never-deleted (§7) graph OUT of GC's scan set entirely. Restore the prior
    # GC state in finally so we never leave collection off (and never enable it
    # if it was already disabled by a caller).
    _gc_was_enabled = gc.isenabled()
    if _gc_was_enabled:
        gc.disable()
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            # R-F1668 — STREAM the facts array in GIL-yielding chunks instead of
            # one json.dump(). The C json encoder does NOT release the GIL during
            # serialisation, so dumping the ~87k-fact graph in a single call held
            # the GIL ~2-7s in this worker thread and starved the asyncio event
            # loop (the residual stall after the R-F1656/1664/1665 absorb cures —
            # cause of the periodic "event loop stalled" + state_store timeouts).
            # A time.sleep(0) every 2048 facts releases the GIL between chunks so
            # the loop keeps serving requests; the on-disk JSON content is
            # unchanged and the recovery path is identical. We iterate a SHALLOW
            # COPY of the facts list so a concurrent store_fact append can't break
            # iteration (also safer than the old live-list json.dump).
            # Stream ONLY when facts is genuinely a list (the real _cache shape).
            # For any other shape (facts is a dict / missing, or data isn't a
            # dict) fall back to json.dump so arbitrary payloads serialise
            # correctly — a generic dict under "facts" must NOT be coerced.
            if isinstance(data, dict) and isinstance(data.get("facts"), list):
                _facts = list(data["facts"])  # shallow copy — safe vs concurrent append
                f.write("{")
                _first = True
                for _k, _v in data.items():
                    if _k == "facts":
                        continue
                    if not _first:
                        f.write(",")
                    _first = False
                    f.write(json.dumps(_k))
                    f.write(":")
                    f.write(json.dumps(_v, default=str))
                if not _first:
                    f.write(",")
                f.write('"facts":[')
                for _i, _fact in enumerate(_facts):
                    if _i:
                        f.write(",")
                    f.write(json.dumps(_fact, default=str))
                    if (_i & 0x7FF) == 0:
                        time.sleep(0)  # release GIL -> event loop runs
                f.write("]}")
            else:
                try:
                    json.dump(data, f)
                except TypeError:
                    f.seek(0)
                    f.truncate()
                    json.dump(data, f, default=str)
            # R-F1420 — flush + fsync the DATA to disk BEFORE the rename.
            # Atomic rename protects against torn/partial files but NOT
            # against losing still-in-page-cache data on a host crash /
            # power loss (the audit's "no-fsync OOM-mid-flush" risk). With
            # 87k+ facts this is the difference between "lose the last write"
            # and "lose everything written since the last OS flush".
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, target)
        _fsync_dir(target_dir)  # R-F1420: make the rename entry itself durable
        # R-F2144: refresh the derived JSONL facts sidecar so the next boot can
        # stream facts in loop-yielding chunks. Best-effort — the canonical write
        # above already succeeded; a sidecar failure must NOT fail the flush.
        # R-F3985 (C-72) — only when a boot might follow. See
        # `_should_write_sidecar`; a stale sidecar is detected by its marker and
        # falls back, so skipping a write is safe while doing it every time
        # doubled the I/O of every flush for a once-per-process reader.
        if write_sidecar:
            try:
                global _last_sidecar_write
                _write_facts_sidecar(data)
                _last_sidecar_write = time.monotonic()
            except Exception:
                pass
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
    finally:
        # R-F1621 — always restore GC (and never enable it if a caller had it
        # disabled). Re-enabling does NOT force a collection; the deferred
        # transient garbage is reclaimed on the next natural collection.
        if _gc_was_enabled:
            gc.enable()


async def _flush_to_disk(final: bool = False) -> None:
    """Persist pending changes. Serialised — see `_flush_lock`."""
    async with _flush_lock:
        await _flush_to_disk_locked(final=final)


async def _flush_to_disk_locked(final: bool = False) -> None:
    """Synchronously serialize the cache and write to disk in a thread
    executor (json.dump is sync C; doing it on the event loop blocks
    every other coroutine for the duration of the dump)."""
    global _dirty, _dirty_since_snapshot, _dirty_bookkeeping_since
    global _needs_compaction, _last_compaction_at
    if not _cache:
        return
    # R-F3972 (C-61) — a bookkeeping-only change waits for a material flush to
    # carry it, and is written on its own only once it has waited too long.
    _bk_due = (
        _dirty_bookkeeping_since is not None
        and (time.monotonic() - _dirty_bookkeeping_since) >= BOOKKEEPING_MAX_AGE_S
    )
    if not _dirty and not _bk_due:
        return

    # R-F4022 (C-95) — decide COMPACT vs JOURNAL before doing any I/O.
    #
    # Bookkeeping rides the journal when it declared its record. It only forces
    # a compaction when it did NOT — then the changed bytes are unknown and a
    # full write is the only thing that persists them.
    _bk_entries = [("fact", r) for r in _pending_bookkeeping.values()]
    _bk_undeclared = _bk_due and not _bk_entries

    # R-F4073 (C-125) — drop superseded journal entries BEFORE the size checks
    # below read it. Compaction fires on journal SIZE, and 82.7% of live entries
    # were repeat upserts of the same records, so the redundancy was pulling
    # every 411 MB snapshot rewrite forward by ~5.8x. Off the loop: it reads and
    # rewrites a file. Never fatal — a failure just leaves the journal as it was,
    # which is exactly the pre-R-F4073 behaviour.
    if _journal_size() >= JOURNAL_COMPACT_MIN_BYTES:
        try:
            _reclaimed = await asyncio.to_thread(_compact_journal)
            if _reclaimed:
                logger.debug("[R-F4073] journal compaction reclaimed %d bytes",
                             _reclaimed)
        except Exception as _jc_exc:
            logger.warning("[R-F4073] journal compaction failed (non-fatal): %s",
                           _jc_exc)

    _journal_due = _journal_size() >= JOURNAL_MAX_BYTES
    # R-F4045 (C-105) — the age trigger only fires when there is enough journal
    # to be worth an O(graph) rewrite. `_needs_compaction` starts True, so the
    # first compaction of a process is unaffected by this gate.
    _age_elapsed = (
        _last_compaction_at is None
        or (time.monotonic() - _last_compaction_at) >= COMPACT_MAX_AGE_S
    )
    _age_due = _age_elapsed and _journal_worth_compacting()
    must_compact = (
        final or _needs_compaction or _bk_undeclared or _journal_due or _age_due
    )

    if not must_compact:
        # ── fast path: persist only what changed ────────────────────────────
        journal_entries, _pending_journal[:] = list(_pending_journal), []
        entries = _bk_entries + journal_entries
        if not entries:
            _dirty = False
            return
        try:
            await asyncio.to_thread(_append_journal, entries)
            _dirty = False
            _dirty_since_snapshot = True
            # Counters are now on disk, so the C-61 deferral is satisfied.
            _pending_bookkeeping.clear()
            _dirty_bookkeeping_since = None
        except Exception as e:
            # Put them back — an unwritten record must stay pending, never be
            # dropped (§7). The next flush retries, and compaction would
            # capture them anyway. Bookkeeping was never cleared, so it needs
            # no restoring.
            _pending_journal[:0] = journal_entries
            logger.error("knowledge: journal append failed: %s", e)
            _wire_persistence(
                source="knowledge:journal_append",
                detail=(f"journal append FAILED ({e}) — {len(journal_entries)} "
                        f"changed record(s) are held in memory ONLY and are lost "
                        f"if the process dies before the next successful write"),
            )
        return

    # ── compaction: the original full-snapshot write, unchanged ────────────
    snapshot = _cache  # write-by-reference is safe — we don't mutate
    _journal_bytes_before = _journal_size()
    _pending_at_start = len(_pending_journal)
    try:
        # R-F787 — throttle the json.dump thread against intel_ledger
        # and neural_memory encoders. The recurring debounced flush
        # is the hot path; one-shot boot migrations below don't need
        # throttling (they run before traffic).
        from ._snapshot_throttle import run_in_thread_throttled
        _side = _should_write_sidecar(final=final)   # R-F3985 (C-72)
        await run_in_thread_throttled(_write_to_disk_atomic, snapshot, _side)
        _dirty = False
        # R-F3972 (C-61) — this write persisted the counters too, so the
        # bookkeeping marker must clear or it would force a redundant ~300 MB
        # rewrite later for changes already on disk.
        _dirty_bookkeeping_since = None
        _pending_bookkeeping.clear()   # R-F4022 — the full write carried them
        _dirty_since_snapshot = True
        # R-F4022 — the snapshot now contains everything the journal held, so
        # drop exactly those bytes. Anything appended DURING the write (the
        # shallow-copy race) is beyond `_journal_bytes_before` and survives.
        _needs_compaction = False
        _last_compaction_at = time.monotonic()
        del _pending_journal[:_pending_at_start]
        try:
            await asyncio.to_thread(
                _truncate_journal_after_compaction, _journal_bytes_before,
            )
        except Exception as _te:
            # The snapshot IS written — this must not be reported as a flush
            # failure or re-arm compaction. An over-long journal only costs
            # replay time, and replay is idempotent.
            logger.warning("knowledge: journal truncate failed (non-fatal): %s", _te)
            _wire_persistence(
                source="knowledge:journal_truncate",
                detail=(f"journal truncate FAILED ({_te}) after a successful "
                        f"snapshot — replay stays correct (idempotent upserts) "
                        f"but boot replay cost will grow until it succeeds"),
            )
        _wire_persistence(
            source="knowledge:compaction",
            ok=True,
            summary=(f"knowledge graph compacted to disk "
                     f"({len(_cache.get('facts') or [])} facts)"),
        )
    except Exception as e:
        # Leave `_needs_compaction` set so the next flush retries the full
        # write rather than silently falling back to journalling onto a
        # snapshot that was never updated.
        _needs_compaction = True
        logger.error("knowledge: disk flush failed: %s", e)
        _wire_persistence(
            source="knowledge:compaction",
            detail=(f"knowledge snapshot write FAILED ({e}) — the on-disk graph "
                    f"is stale and every change since the last compaction "
                    f"depends on the journal surviving; §7 forget risk"),
        )


async def _flush_loop() -> None:
    """Background coroutine: every FLUSH_DEBOUNCE_S, flush to disk if
    dirty; every SNAPSHOT_INTERVAL_S, also push a Redis snapshot."""
    global _dirty_since_snapshot
    last_snapshot = time.monotonic()
    while not _flusher_stop:
        try:
            await asyncio.sleep(FLUSH_DEBOUNCE_S)
            await _flush_to_disk()
            now = time.monotonic()
            if (
                _dirty_since_snapshot
                and (now - last_snapshot) >= SNAPSHOT_INTERVAL_S
                and _cache
            ):
                try:
                    # R-F4028 (C-98) — only if it is genuinely a BACKUP. On
                    # sqlite this wrote 83.7 MB of the graph every 600 s onto
                    # the same volume as the graph. Unknown still runs: an
                    # unmeasurable target is a reason to keep a copy.
                    _offhost = _snapshot_target_is_offhost()
                    if not _should_snapshot(_offhost):
                        # Reset the timer so this is a cheap no-op per interval
                        # rather than a re-check every 2 s.
                        last_snapshot = now
                        _wire_persistence(
                            source="knowledge:snapshot_skipped_same_volume",
                            ok=True,
                            once=True,   # steady state, not an event
                            summary=(
                                "R-F334 sharded snapshot SKIPPED — the state "
                                "store shares a volume with the knowledge "
                                "graph, so it is not an off-host backup"
                            ),
                        )
                        continue
                    # R-F334: write as sharded snapshot (N keys ~500KB each)
                    # instead of one 4MB+ blob. Backward-compat read path
                    # in _load() falls through to legacy KEY if no shards.
                    result = await _save_sharded_snapshot(_cache)
                    _dirty_since_snapshot = False
                    last_snapshot = now
                    logger.info(
                        "knowledge: R-F334 sharded state snapshot written "
                        "(%d facts in %d shards, %d total bytes gzip)",
                        result["items"]["facts"],
                        result["shard_count"],
                        result["total_bytes"],
                    )
                except Exception as e:
                    logger.warning("knowledge: state snapshot failed: %s", e)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning("knowledge: flush loop error: %s", e)


def _ensure_flusher() -> None:
    """Start the debounced flusher if a running loop exists. No-op in
    sync test contexts (no loop) — those should call flush() explicitly
    if they need persistence."""

# R-F3321 - the flusher guard must be PER-LOOP, not per-process.
#
# `_flusher_started` was a module global that nothing ever reset, so:
#   loop A: _ensure_flusher() creates _flush_task on A, sets _flusher_started=True
#   loop A closes, taking its task with it
#   loop B: _ensure_flusher() returns EARLY (the flag is still True) -> no flusher,
#           while _flush_task still points at a task bound to the DEAD loop A
#
# shutdown() then does `await _flush_task` on that stale handle. Awaiting a task
# from another (closed) loop NEVER completes, so the awaiting task never finishes
# cancelling and asyncio.run()'s Runner.close() blocks forever.
#
# That is the third suite wedge, isolated to the deterministic pair
# test_rf2976_dd_jurisdiction_uk_gb + test_rf3064_3065_coder_gate_and_profiler,
# where either file passes ALONE. Found by inspecting a manual loop instead of
# asyncio.run(), which showed exactly one survivor: a pending _flush_loop task.
# Three earlier hypotheses were disproven by measurement first: thread
# accumulation (only MainThread alive), a live network call (the R-F3319 guard
# caught nothing), and orphaned tasks in the poisoner's own loop (draining them
# changed nothing, so that attempt was reverted rather than kept).
#
# state_store already solved this class for its lock (_reset_lock, "Each test's
# asyncio.run() resets _conn ... binds it to the new loop"). These two modules
# never got the same treatment.
#
# In production there is ONE long-lived loop, so this costs nothing there; it is
# correctness for every multi-loop context, which is what the test suite is.
    global _flush_task, _flusher_started, _flusher_loop
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    if (_flusher_started and _flusher_loop is loop
            and _flush_task is not None and not _flush_task.done()):
        return
    if _flusher_loop is not None and _flusher_loop is not loop:
        # Stale handle from a previous loop. DROP it, never await it.
        _flush_task = None
    _flush_task = loop.create_task(_flush_loop())
    _flusher_started = True
    _flusher_loop = loop


async def _load() -> dict:
    """Hydrate the cache once. Order: disk → legacy Redis blob (one-shot
    migration) → empty default. Subsequent calls hit the in-memory
    cache without I/O."""
    global _cache
    if _cache is not None:
        return _cache

    # 1. Prefer disk — this is the canonical store post-F94.
    # R-F2144: chunked sidecar read (falls back to the monolithic load) so the
    # ~223k-fact boot load streams in loop-yielding chunks and never starves the
    # single event loop (the 2026-06-29 warmup starvation).
    data = await _read_from_disk_chunked()
    if data:
        # R-F4022 (C-95) — the snapshot is only current as of the last
        # compaction; everything since then is in the journal. Replaying is
        # what makes the cheap flush path safe.
        data = _replay_journal(data)
        _cache = data
        logger.info(
            "knowledge: loaded %d facts from disk (%s)",
            len(_cache.get("facts", [])), _DISK_PATH,
        )
        _ensure_flusher()
        return _cache

    # 2. Disk empty — R-F334: try the SHARDED Redis snapshot first
    #    (new format from 2026-05-11). Falls back to legacy single-blob
    #    if no meta key is present.
    sharded = None
    try:
        sharded = await _load_sharded_snapshot()
    except Exception as _se:
        logger.debug("R-F334 sharded load failed: %s", _se)
    if sharded:
        # R-F4022 — a journal can outlive a lost snapshot file; replaying it
        # here is a genuine recovery, and it is idempotent when it is not.
        _cache = _replay_journal(sharded)
        logger.warning(
            "knowledge: hydrated from R-F334 sharded Redis snapshot "
            "(%d facts) — migrating to disk %s",
            len(_cache.get("facts", [])), _DISK_PATH,
        )
        try:
            await asyncio.to_thread(_write_to_disk_atomic, _cache)
            logger.info("knowledge: sharded Redis → disk migration complete")
        except Exception as e:
            logger.error("knowledge: sharded migration to disk failed: %s", e)
        _ensure_flusher()
        return _cache

    # 2b. No shards — fall back to legacy single-blob (will be migrated
    # forward to shards on next write).
    raw = await rs.get(KEY)
    legacy = _decode_snapshot(raw)
    if legacy:
        _cache = _replay_journal(legacy)   # R-F4022 — see above
        logger.warning(
            "knowledge: hydrated from legacy Redis blob (%d facts) — "
            "migrating to disk %s + will write shards on next flush",
            len(_cache.get("facts", [])), _DISK_PATH,
        )
        try:
            await asyncio.to_thread(_write_to_disk_atomic, _cache)
            logger.info("knowledge: legacy Redis → disk migration complete")
        except Exception as e:
            logger.error("knowledge: legacy migration to disk failed: %s", e)
        _ensure_flusher()
        return _cache

    # 3. Cold start with no prior state.
    # R-F4022 — if a journal survived a lost snapshot, this is the only place
    # those facts can come back. §7: never forget.
    _cache = _replay_journal(
        {"facts": [], "queries": [], "learnings": [], "version": 1}
    )
    _ensure_flusher()
    return _cache


async def _save(*, material: bool = True, record: dict | None = None,
                kind: str = "fact", structural: bool = False) -> None:
    """Mark the cache dirty. Actual disk I/O is debounced through
    _flush_loop so brain_hook bursts (~14 absorbs/turn) coalesce into a
    single write.

    R-F3972 (C-61) — `material=False` marks a BOOKKEEPING mutation: one that
    changed no knowledge, only a usage counter. The duplicate-content branch of
    `store_fact` is the case that matters, and it is the most common outcome of
    a crawl-and-absorb loop re-encountering the same pages.

    It used to force a full flush, and a flush is expensive out of all
    proportion to a `+= 1`: `_write_to_disk_atomic` serialises the WHOLE graph
    (~150-171 MB at ~223k facts), fsyncs it, renames, fsyncs the directory, then
    unconditionally writes the derived JSONL sidecar with its OWN fsync — the
    same data twice. At FLUSH_DEBOUNCE_S=2.0 that is ~1.7-2 GB/min onto the same
    volume as aria_state.db, its WAL, chromadb and the neural shards. Live
    2026-08-13 the loop read `starved`, p95 2058ms.

    Losing a bump on a crash is acceptable; losing a FACT is not. `accessCount`
    feeds ranking (`:1880`, capped at `min(count, 5)`) and a dedup preference —
    a derived usage statistic. §7's infinite-memory rule governs facts, not
    counters. Every material mutation still flushes exactly as before, and
    `material=True` is the default so no existing caller changes behaviour.

    R-F4022 (C-95) — `record` is the thing that changed, and declaring it lets
    the flush journal ~1 KB instead of rewriting ~389 MB.

    **`record=None` on a material save forces a full rewrite.** That is the
    safety default, and it is the whole reason this is safe to add: a mutation
    site written later by someone who never read this docstring degrades to
    exactly today's behaviour instead of silently losing data. The journal can
    only ever be as correct as the set of changes it was told about, so "I was
    told nothing" must mean "write everything", never "write nothing".

    `structural=True` marks a removal or a wholesale list replacement. The
    journal is an UPSERT log and cannot express a deletion — replaying it would
    resurrect what was just removed — so those compact instead.

    A BOOKKEEPING save should declare its record too. C-61 made these wait for
    a flush to carry them; with a journal there is a flush cheap enough to
    carry them, so declaring the record keeps `_bk_due` from forcing a full
    compaction every BOOKKEEPING_MAX_AGE_S. They are held in a dict keyed by
    record id, so a page re-encountered a thousand times in one window costs
    ONE journal line, not a thousand.
    """
    global _dirty, _dirty_bookkeeping_since, _needs_compaction
    if not _cache:
        return
    if structural:
        _needs_compaction = True
        _dirty = True
    elif material:
        _dirty = True
        if record is None:
            _needs_compaction = True
        else:
            _pending_journal.append((kind, record))
    else:
        if record is not None:
            # Bookkeeping with a declared record — dedup by id, so a page
            # re-encountered a thousand times costs ONE journal line.
            _pending_bookkeeping[str(record.get("id") or id(record))] = record
        if _dirty_bookkeeping_since is None:
            # Deferred, never dropped: it rides the next flush, and if none
            # arrives it is written once BOOKKEEPING_MAX_AGE_S has passed.
            _dirty_bookkeeping_since = time.monotonic()
    _ensure_flusher()


@fail_wire(module="knowledge", gap_type="engine_failure")
async def flush() -> None:
    """Force an immediate disk flush. Call from shutdown hooks or tests
    that need to assert on-disk state without waiting for the debounced
    loop.

    R-F3972 (C-61) — this is the FORCED path, so it writes even when only
    bookkeeping is pending: a shutdown must not drop counter bumps that the
    debounced loop was still deferring."""
    global _dirty
    if _dirty_bookkeeping_since is not None:
        _dirty = True
    await _flush_to_disk(final=True)   # R-F3985 (C-72)


@fail_wire(module="knowledge", gap_type="engine_failure")
async def shutdown() -> None:
    """Stop the background flusher and write any pending changes."""
    global _flusher_stop, _flush_task, _flusher_loop, _flusher_started
    _flusher_stop = True
    if _flush_task:
        # R-F3321: only ever await a task belonging to the RUNNING loop. Awaiting
        # one from a closed loop never returns and hangs Runner.close() forever.
        try:
            _running = asyncio.get_running_loop()
        except RuntimeError:
            _running = None
        _flush_task.cancel()
        if _flusher_loop is _running and _running is not None:
            try:
                await _flush_task
            except (asyncio.CancelledError, Exception):
                pass
        _flush_task = None
        _flusher_loop = None
        _flusher_started = False
    # R-F3985 (C-72) — a clean shutdown MUST leave a current sidecar, or every
    # restart falls back to the ~10-minute monolithic boot load.
    await _flush_to_disk(final=True)


# ── Public API ───────────────────────────────────────────────────────────────

@fail_wire(module="knowledge", gap_type="engine_failure")
async def get_all_facts() -> list[dict]:
    """Return all facts in the knowledge base (for security audit scanning)."""
    cache = await _load()
    # R-F996 — wire to brain
    from .engine_wiring import wire_success, wire_failure
    wire_success(
        module="knowledge",
        summary="Get All Facts",
        source_id="knowledge:R-F996",
    )

    return cache.get("facts", [])


# ── R-F2280: reclaim orphaned atomic-write temp files left by KILLED processes ──
# _write_facts_sidecar and _write_to_disk_atomic each unlink their mkstemp .tmp on
# any Python exception — but they CANNOT clean up when the process is KILLED
# mid-write (SIGKILL, fly SIGTERM, OOM, or the R-F2277 state_store watchdog's
# os._exit): the atomic rename never runs and the .tmp is orphaned. During the
# 2026-06-29..07-01 wedge-restart incidents this leaked ~1.5 GB of
# .aria_kn_facts.*.jsonl.tmp (some 100-147 MB) into /data. These are INCOMPLETE
# temp files, never live knowledge, so removing them does not touch ARIA's
# infinite memory (§7). Swept once at boot (init), before this process writes
# anything, with an age floor so a CONCURRENT worker's in-flight write (seconds
# old) is never removed.
_TMP_SWEEP_PREFIXES = (".aria_kn_facts.", ".aria_kn_meta.", ".aria_knowledge.")


def _sweep_orphaned_sidecar_tmp(min_age_s: float | None = None) -> dict:
    """Remove stale orphaned atomic-write .tmp files from the knowledge data dir.

    Best-effort, never raises. Only removes files that both (a) match an
    atomic-write temp prefix AND end in ``.tmp``, and (b) are older than
    ``min_age_s`` (default 1800s; a real atomic write completes in seconds, so
    this floor guarantees we never delete a concurrent worker's live temp).
    The canonical ``aria_knowledge.json`` and the derived ``*.facts.jsonl`` /
    ``*.meta.json`` sidecars have no leading dot and no ``.tmp`` suffix, so they
    are never matched. Returns {removed, bytes_reclaimed, skipped_recent}.
    """
    if min_age_s is None:
        try:
            min_age_s = float(os.getenv("ARIA_KN_TMP_SWEEP_MIN_AGE_S", "1800"))
        except (TypeError, ValueError):
            min_age_s = 1800.0
    target_dir = os.path.dirname(str(_DISK_PATH)) or "."
    removed = reclaimed = skipped_recent = 0
    now = time.time()
    try:
        with os.scandir(target_dir) as it:
            for entry in it:
                name = entry.name
                if not name.endswith(".tmp"):
                    continue
                if not any(name.startswith(p) for p in _TMP_SWEEP_PREFIXES):
                    continue
                try:
                    st = entry.stat()
                    if (now - st.st_mtime) < min_age_s:
                        skipped_recent += 1  # possibly a concurrent worker's live write
                        continue
                    size = st.st_size
                    os.unlink(entry.path)
                    removed += 1
                    reclaimed += size
                except OSError:
                    continue
    except OSError as e:
        logger.debug("knowledge: R-F2280 tmp sweep skipped (scandir failed): %s", e)
        return {"removed": 0, "bytes_reclaimed": 0, "skipped_recent": 0}
    if removed:
        logger.info(
            "knowledge: R-F2280 reclaimed %d orphaned atomic-write tmp file(s), "
            "%.1f MB (skipped %d too-recent)",
            removed, reclaimed / 1048576.0, skipped_recent,
        )
    return {"removed": removed, "bytes_reclaimed": reclaimed, "skipped_recent": skipped_recent}


@fail_wire(module="knowledge", gap_type="engine_failure")
async def init() -> None:
    # R-F2280: reclaim orphaned atomic-write .tmp files leaked by killed processes
    # (e.g. R-F2277 os._exit / fly SIGTERM mid-sidecar-write) BEFORE we load or
    # write anything. Best-effort, age-gated; never touches live knowledge (§7).
    try:
        _sweep_orphaned_sidecar_tmp()
    except Exception:  # pragma: no cover - defensive; boot must never fail here
        pass
    await _load()
    facts = (_cache or {}).get("facts", [])
    logger.info(f"Knowledge base loaded: {len(facts)} facts")
    # Semantic index build is INTENTIONALLY deferred — rebuild_index_from_knowledge
    # encodes every fact through sentence-transformers (~200-700ms per fact, sync
    # C call that doesn't yield to the event loop). For ~500 facts that's 100-350s
    # of blocking, which prevents uvicorn from binding and causes fly health checks
    # to fail. Past incident 2026-04-08.
    #
    # Spawn it as a background task so the server can bind first. Search calls
    # before the index is ready will fall through to the TF-IDF / Jaccard
    # fallback in semantic_search, which is degraded but functional.
    #
    # Can be disabled entirely with ARIA_SEMANTIC_INDEX_BUILD=0 — useful during
    # interactive testing. Even though encode() runs in a thread executor, it
    # holds the GIL in chunks, which starves the chat handler enough that
    # liveness probes time out. Past incident 2026-04-08 (round 2): user
    # couldn't get a reply for 'Aria, are you online?' because the startup
    # index build was hammering CPU continuously for 60+ seconds.
    import os as _os
    if (_os.getenv("ARIA_SEMANTIC_INDEX_BUILD", "1") or "1").lower() in ("0", "false", "no"):
        logger.info("Semantic index build SKIPPED via ARIA_SEMANTIC_INDEX_BUILD=0 — search will use TF-IDF/Jaccard fallback")
        return
    try:
        import asyncio as _aio
        async def _build_index_bg():
            await _aio.sleep(10)  # Give the server time to bind first
            try:
                from .semantic_search import rebuild_index_from_knowledge
                # Run in a thread executor so the encode loop doesn't starve
                # the event loop. encode() is sync C; the executor lets the
                # main loop keep handling requests while it works.
                loop = _aio.get_running_loop()
                count = await loop.run_in_executor(None, rebuild_index_from_knowledge, facts)
                logger.info("Semantic index built in background: %d facts indexed", count)
            except Exception as e:
                logger.warning("Background semantic index build failed: %s", e)
        _aio.create_task(_build_index_bg())
    except Exception as e:
        logger.warning("Could not schedule semantic index build: %s", e)


# ── Contradiction detection ──────────────────────────────────────────────────
# When a new fact arrives, we look for existing facts on the same topic that
# might disagree. Caught contradictions are flagged on BOTH facts so ARIA's
# context layer can surface "I previously thought X, but now I'm seeing Y" —
# the foundation of metacognitive self-correction.

_NEGATION_RE = re.compile(
    r"\b(not|no longer|never|denies|denied|withdrew|cancelled|cancelled|"
    r"reversed|stopped|halted|terminated|suspended|abandoned|dropped|"
    r"refuted|disputed|false|incorrect|wrong)\b",
    re.IGNORECASE,
)
_NUMBER_RE = re.compile(r"\b\d[\d,\.]*\b")
_CONFIDENCE_RANK = {"CONFIRMED": 4, "PROBABLE": 3, "ASSESSED": 2, "UNCERTAIN": 1, "SPECULATIVE": 0}

def _detect_contradictions(topic: str, content: str, existing_facts: list[dict]) -> list[dict]:
    """Find existing facts that may contradict the new statement.

    Heuristic — flags potential conflicts when:
      1. Same topic, but new content has negation that old doesn't (or vice-versa)
      2. Same topic, but the numeric values disagree (e.g. £200m vs £350m)
      3. Same topic, opposing keywords (won/lost, signed/cancelled, alive/dead)
    """
    if not existing_facts:
        return []

    new_lower = content.lower()
    new_negated = bool(_NEGATION_RE.search(new_lower))
    new_numbers = set(_NUMBER_RE.findall(new_lower))
    topic_lower = topic.strip().lower()

    OPPOSING = [
        ({"won", "awarded", "signed", "delivered"}, {"lost", "cancelled", "withdrew", "rejected", "terminated"}),
        ({"alive", "active", "in office", "serving"}, {"dead", "deceased", "removed", "dismissed", "retired"}),
        ({"increased", "rising", "growing"}, {"decreased", "falling", "declining", "cut"}),
        ({"sanctioned", "embargoed", "blocked"}, {"removed", "delisted", "exempt", "cleared"}),
    ]
    contradictions: list[dict] = []
    for f in existing_facts:
        if f.get("topic", "").strip().lower() != topic_lower:
            continue
        old_text = (f.get("content") or "").lower()
        old_negated = bool(_NEGATION_RE.search(old_text))
        conflict_reason = None

        if new_negated != old_negated:
            conflict_reason = "negation mismatch"
        else:
            old_numbers = set(_NUMBER_RE.findall(old_text))
            if new_numbers and old_numbers and not (new_numbers & old_numbers):
                conflict_reason = f"numeric mismatch (was {sorted(old_numbers)[:3]}, now {sorted(new_numbers)[:3]})"
            else:
                for set_a, set_b in OPPOSING:
                    has_a_old = any(w in old_text for w in set_a)
                    has_b_old = any(w in old_text for w in set_b)
                    has_a_new = any(w in new_lower for w in set_a)
                    has_b_new = any(w in new_lower for w in set_b)
                    if (has_a_old and has_b_new) or (has_b_old and has_a_new):
                        conflict_reason = "opposing terms"
                        break

        if conflict_reason:
            contradictions.append({
                "fact_id": f.get("id"),
                "old_content": (f.get("content") or "")[:200],
                "old_confidence": f.get("confidence"),
                "old_source": f.get("source"),
                "old_updated_at": f.get("updatedAt"),
                "reason": conflict_reason,
            })
    return contradictions


# ── R-F1622 — O(1) dedup/contradiction indices ──────────────────────────────
def _index_key_content(content_hash: str, source_domain: str) -> str:
    return f"{content_hash}|{source_domain}"


def _rebuild_indices(db: dict) -> None:
    """(Re)build the dedup indices from the full facts list. O(N), but runs
    once per cache load (or if the list length drifts from an out-of-band
    append/delete) — NOT per store_fact."""
    global _topic_index, _content_index, _index_count, _indexed_cache_id
    facts = db.get("facts", []) if db else []
    ti: dict[str, list] = {}
    ci: dict[str, dict] = {}
    for f in facts:  # facts is newest-first (insert(0) order); preserve it
        t = (f.get("topic") or "").strip().lower()
        if t:
            ti.setdefault(t, []).append(f)
        ch, sd = f.get("content_hash"), f.get("source_domain")
        if ch and sd:
            ci.setdefault(_index_key_content(ch, sd), f)  # first seen = newest wins
    _topic_index, _content_index = ti, ci
    _index_count = len(facts)
    _indexed_cache_id = id(db)


def _ensure_indices(db: dict) -> None:
    """Cheap O(1) hot-path guard: rebuild only if the cache identity changed
    (reload) or the fact count drifted (something appended/removed out of
    band). store_fact's own insert keeps _index_count in lock-step, so the
    steady-state path never rebuilds."""
    if _indexed_cache_id != id(db) or _index_count != len(db.get("facts", [])):
        _rebuild_indices(db)


def _index_add(new_record: dict) -> None:
    """Incrementally index a freshly-inserted fact. Newest → front of its
    topic list so topic-dedup keeps picking the newest (matching the prior
    `for f in db['facts']` first-match-wins order, since new facts insert(0))."""
    global _index_count
    t = (new_record.get("topic") or "").strip().lower()
    if t:
        _topic_index.setdefault(t, []).insert(0, new_record)
    ch, sd = new_record.get("content_hash"), new_record.get("source_domain")
    if ch and sd:
        _content_index[_index_key_content(ch, sd)] = new_record  # newest wins
    _index_count += 1


# ── R-F1530: auto-verification queue ────────────────────────────────────────
# Every fact stored via store_fact is automatically queued for verification,
# regardless of whether the caller supplied source_url/fact_type/entity_name.
# Previously only facts with all three fields went through verified_intel;
# everything else was stamped LEGACY_UNVERIFIED and never verified.
# This is the structural fix for verified_intel being stale for 59h.


def _auto_verify_fact(
    fact_record: dict,
    topic: str,
    content: str,
    source: str,
) -> None:
    """Fire-and-forget: queue a fact for background verification.

    Runs as a sync function (fire-and-forget via asyncio.create_task)
    so it never blocks the store_fact caller. If verification is not
    configured (no verified_intel module), this is a no-op.

    R-F1656: bounded with a global Semaphore (max 4 concurrent) and
    asyncio.wait_for timeout (30s per verify). Skips verification when
    the search circuit breaker is OPEN to prevent tasks queuing against
    a dead dependency.
    """
    try:
        import asyncio
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return  # no running loop — can't schedule background task

    try:
        from . import verified_intel as _vi
        from . import redis_store as rs

        # R-F1656: global semaphore — max 4 concurrent verify tasks
        if not hasattr(_auto_verify_fact, "_sem"):
            _auto_verify_fact._sem = asyncio.Semaphore(4)
        _sem = _auto_verify_fact._sem

        async def _verify():
            # R-F1656: skip verification when search circuit is OPEN
            # to prevent tasks queuing against a dead dependency.
            try:
                from .circuit_breaker import get_breaker as _cb
                _ddg = _cb("search:duckduckgo")
                if _ddg and _ddg.is_open():
                    return  # search is down — verification would hang
            except Exception:
                pass

            async with _sem:
                try:
                    # R-F1656: hard timeout so a hanging verify can't wedge
                    await asyncio.wait_for(
                        _do_verify(fact_record, topic, content, source, _vi, rs),
                        timeout=30.0,
                    )
                except asyncio.TimeoutError:
                    # Timeout is expected when search is slow — skip silently
                    pass
                except Exception:
                    pass  # Verification is best-effort — never break fact storage

        # R-F1669: bound the PENDING-task QUEUE, not just concurrency. R-F1656's
        # Semaphore(4) caps how many verifies RUN at once, but create_task still
        # fired once per fact — so a fact burst piled up thousands of pending
        # _verify() coroutines (each holding a closure over fact_record/content)
        # → memory growth + event-loop scheduler overhead → the R-F1530 wedge
        # recurred despite R-F1656. Cap the in-flight backlog; drop new verifies
        # when full (verification is best-effort and the fact is already stored,
        # so this loses enrichment, never knowledge — §7 unaffected).
        _max_pending = int(os.getenv("ARIA_VERIFY_MAX_PENDING", "64"))
        if getattr(_auto_verify_fact, "_pending", 0) >= _max_pending:
            _auto_verify_fact._dropped = getattr(_auto_verify_fact, "_dropped", 0) + 1
            return  # backlog full — skip to keep the queue bounded
        _auto_verify_fact._pending = getattr(_auto_verify_fact, "_pending", 0) + 1

        task = loop.create_task(_verify())

        def _verify_done(_t):  # decrement the backlog (loop thread → no race)
            _auto_verify_fact._pending = max(0, getattr(_auto_verify_fact, "_pending", 1) - 1)

        task.add_done_callback(_verify_done)  # also keeps a ref (no GC)
    except Exception:
        pass  # verification not available — no-op


async def _do_verify(
    fact_record: dict,
    topic: str,
    content: str,
    source: str,
    _vi,
    rs,
) -> None:
    """Actual verification logic, extracted so _verify() can wrap it with
    semaphore + timeout without duplicating the try/except nesting."""
    try:
        engine = _vi.ARIAVerificationEngine()
        claim_text = (content or "")[:500]
        if len(claim_text) < 20:
            return

        entity = (topic or "")[:100]
        if not entity:
            entity = (source or "").split(":")[0][:100]

        vfact = await engine.averify_and_store(
            claim_text=claim_text,
            claim_value=content[:200],
            entity_name=entity,
            entity_type="unknown",
            fact_type=_vi.FactType.GENERAL_CLAIM,
            source_url=fact_record.get("source_url", ""),
            source_excerpt=content[:300],
        )

        if vfact:
            fact_record["verification_status"] = vfact.verification_status.value
            fact_record["verification_score"] = vfact.verification_score
            fact_record["verified_fact_id"] = vfact.fact_id
            fact_record["citation"] = vfact.citation
            fact_record["expires_at"] = vfact.expires_at
            fact_record["source_urls"] = [s.url for s in vfact.sources]

            await rs.set_json(
                f"crucix:verified_intel:fact:{vfact.fact_id}",
                vfact.to_dict(),
            )
    except Exception:
        pass


@fail_wire(module="knowledge", gap_type="engine_failure")
async def store_fact(topic: str, content: str, source: str = "user",
                     confidence: str = "CONFIRMED",
                     *,
                     source_url: str = "",
                     fact_type: str = "",
                     entity_name: str = "",
                     entity_type: str = "",
                     skip_rag_ingest: bool = False,
                     skip_semantic_index: bool = False) -> dict:
    """Store a fact, detecting contradictions and merging duplicates.

    R-F1526 content-verification guard: if `content` looks like a bare URL
    (starts with http:// or https://) or is shorter than 50 chars of actual
    extracted text, the fact is REJECTED with action="rejected_no_content".
    This prevents the LLM from storing URL strings as "facts" when page
    extraction failed silently.

    Clause 17 wiring: when `source_url` + `fact_type` + `entity_name` are
    supplied, the fact is routed through `verified_intel.averify_and_store`
    so it carries full provenance (tier, verification score, expiry).
    When they are absent, the legacy record is stamped
    `verification_status="LEGACY_UNVERIFIED"` so downstream renderers emit
    the `[LEGACY — pre-verification pipeline]` citation instead of silently
    presenting the fact as verified.

    Returns a dict with action taken: ``{action: "created"|"updated"|"superseded",
    fact_id, contradictions: [...]}``
    """
    # ── R-F1526 content-verification guard ─────────────────────────────
    # Reject facts where content is just a URL or too short to be meaningful.
    # This catches the pattern where /teach <url> fails to extract page content
    # and the LLM stores the URL string itself as a "fact".
    # R-F1529: brain_hook signals are legitimately short status updates
    # (e.g. "cost_tracker: R-F996" at 19 chars). Only reject content that
    # looks like a failed URL extraction — bare URLs or very short content
    # WITHOUT a brain_hook source.
    _content_stripped = (content or "").strip()
    if not _content_stripped:
        logger.warning("[R-F1526] store_fact rejected: empty content (topic=%s, source=%s)", topic, source)
        return {"action": "rejected_no_content", "reason": "empty_content"}

    # Only apply the content-length guard to non-brain_hook sources.
    # brain_hook signals are intentionally short status updates.
    _is_brain_hook = source.startswith("brain_hook:")
    if not _is_brain_hook:
        if len(_content_stripped) < 50 and not topic.startswith("http"):
            # Very short content is suspicious — likely a failed extraction.
            # Exception: topic is not a URL (legitimate short facts like
            # "CEO: John Doe" are OK if they come through the verified pipeline).
            if not source_url:
                logger.warning(
                    "[R-F1526] store_fact rejected: content too short (%d chars, topic=%s, source=%s)",
                    len(_content_stripped), topic, source,
                )
                return {"action": "rejected_no_content", "reason": f"content_too_short:{len(_content_stripped)}"}

        # Check if content is just a URL (the LLM stored the URL instead of extracted text)
        import re as _re
        if _re.match(r"^https?://", _content_stripped) and len(_content_stripped) < 200:
            logger.warning(
                "[R-F1526] store_fact rejected: content is a bare URL (topic=%s, source=%s, url=%s)",
                topic, source, _content_stripped[:100],
            )
            return {"action": "rejected_no_content", "reason": "bare_url_content"}
    db = await _load()
    now = datetime.now(timezone.utc).isoformat()

    # ── Clause 17 provenance gate ─────────────────────────────────────────
    # Lazy import — verified_intel pulls nothing heavy, but keep the pattern
    # consistent with semantic_search / rag_store elsewhere in this module.
    verified_meta: dict = {}
    if source_url and fact_type and entity_name:
        try:
            from . import verified_intel as _vi
            engine = _vi.ARIAVerificationEngine()
            try:
                ft = _vi.FactType[fact_type]
            except KeyError:
                ft = _vi.FactType.GENERAL_CLAIM
            vfact = await engine.averify_and_store(
                claim_text=content[:500],
                claim_value=content[:200],
                entity_name=entity_name,
                entity_type=entity_type or "unknown",
                fact_type=ft,
                source_url=source_url,
                source_excerpt=content[:300],
            )
            verified_meta = {
                "verification_status": vfact.verification_status.value,
                "verification_score": vfact.verification_score,
                "verified_fact_id": vfact.fact_id,
                "citation": vfact.citation,
                "expires_at": vfact.expires_at,
                "source_urls": [s.url for s in vfact.sources],
            }
        except Exception as e:
            logger.debug("verified_intel wiring failed (non-fatal): %s", e)
            verified_meta = {"verification_status": "LEGACY_UNVERIFIED"}
    else:
        # No URL/fact_type supplied — this is a bare /teach or legacy ingest.
        verified_meta = {"verification_status": "LEGACY_UNVERIFIED"}

    # ── R-F174 / R-F217 content-hash setup (cheap; stays on loop) ─────────
    # R-F174 (2026-05-11) added content-hash dedup so RSS titles with
    # near-duplicate bodies don't grow knowledge linearly with reading
    # volume. R-F217 (2026-05-11) restricted source_domain stamping to
    # URL or `:`-prefixed sources so bare-source `/teach` writes don't
    # collide on (hash, "user") and silently skip contradiction detection.
    # These two snippets are cheap (single hashlib + single urlparse) —
    # left on the loop. The expensive part is the THREE O(len(facts))
    # scans (contradictions + content_hash + topic) that follow.
    import hashlib as _hashlib
    _content_hash = _hashlib.sha1(
        (content[:300] or "").strip().lower().encode("utf-8")
    , usedforsecurity=False).hexdigest()[:16]
    _source_domain = ""
    try:
        _src_lower = (source or "").lower()
        if "://" in _src_lower:
            from urllib.parse import urlparse as _up
            _source_domain = (_up(_src_lower).hostname or "").strip(".")
        elif ":" in _src_lower:
            _source_domain = _src_lower.split(":", 1)[1].strip()[:60]
    except Exception:
        _source_domain = ""

    # ── R-F1622 — O(1) dedup/contradiction via incremental indices ───────
    # Supersedes R-F775's "single-pass scan in a worker thread". That scan
    # was still O(len(facts)): at ~87k facts it cost ~3.5s/call, driving the
    # brain_hook "knowledge: timeout (>3.5s)" floods, the absorb circuit
    # tripping at p95=120-190s (wedge_673 era), and a major event-loop wedge
    # (the 87k-dict Python iteration holds the GIL even inside the thread).
    # The indices (built once per load, kept in sync on insert) make all three
    # lookups O(1) / O(facts-in-this-topic). They run INLINE — and because
    # there is now no `await` between the lookup and the mutation below, a
    # concurrent store_fact cannot interleave, so the R-F775 reference-vs-index
    # concern is moot (we still hold dict references, which stay valid as
    # §7 never deletes).
    _ensure_indices(db)
    _topic_lower = topic.strip().lower()
    _same_topic = _topic_index.get(_topic_lower, [])
    contradictions = _detect_contradictions(topic, content, _same_topic)
    _content_hit = None
    if _content_hash and _source_domain:
        _content_hit = _content_index.get(
            _index_key_content(_content_hash, _source_domain)
        )
    _topic_hit = _same_topic[0] if (_content_hit is None and _same_topic) else None

    if _content_hit is not None:
        f = _content_hit
        f["accessCount"] = f.get("accessCount", 0) + 1
        f["last_seen_at"] = now
        # R-F3972 (C-61) — BOOKKEEPING. Nothing was learned here: the content
        # hash already matched, so only a usage counter and a timestamp moved.
        # A material save would rewrite the entire ~150-171 MB graph AND its
        # sidecar, and a re-encountered page is the most common outcome of the
        # crawl-and-absorb loop.
        # R-F4022 (C-95) — declare the record so this rides a ~1 KB journal
        # line instead of forcing a whole-graph rewrite when it falls due.
        await _save(material=False, record=f, kind="fact")
        return {
            "action": "duplicate_skipped",
            "fact_id": f["id"],
            "reason": "content_hash_match_R-F174",
        }

    if _topic_hit is not None:
        f = _topic_hit
        old_rank = _CONFIDENCE_RANK.get(f.get("confidence", "ASSESSED"), 2)
        new_rank = _CONFIDENCE_RANK.get(confidence, 2)

        if contradictions:
            if new_rank >= old_rank:
                f["superseded_by"] = None
                f["superseded_at"] = now
                f["history"] = (f.get("history") or [])[-9:] + [{
                    "content": f["content"],
                    "confidence": f["confidence"],
                    "source": f["source"],
                    "replaced_at": now,
                }]
                f["content"] = content
                f["source"] = source
                f["confidence"] = confidence
                f["updatedAt"] = now
                f["accessCount"] = f.get("accessCount", 0) + 1
                f["contradictions_detected"] = (f.get("contradictions_detected", 0) or 0) + len(contradictions)
                await _save(record=f, kind="fact")   # R-F4022
                return {"action": "superseded", "fact_id": f["id"], "contradictions": contradictions}
            else:
                f["pending_conflicts"] = (f.get("pending_conflicts") or [])[-4:] + [{
                    "content": content[:200], "confidence": confidence,
                    "source": source, "noted_at": now,
                }]
                await _save(record=f, kind="fact")   # R-F4022
                return {"action": "conflict_logged", "fact_id": f["id"], "contradictions": contradictions}

        f["content"] = content
        f["source"] = source
        f["confidence"] = confidence
        f["updatedAt"] = now
        f["accessCount"] = f.get("accessCount", 0) + 1
        if verified_meta:
            f.update(verified_meta)
        await _save(record=f, kind="fact")   # R-F4022
        return {"action": "updated", "fact_id": f["id"], "contradictions": []}

    # ── Brand-new fact ────────────────────────────────────────────────────
    new_id = str(uuid.uuid4())[:8]
    new_record = {
        "id": new_id,
        "topic": topic,
        "content": content,
        "source": source,
        "confidence": confidence,
        "createdAt": now,
        "updatedAt": now,
        "accessCount": 0,
        "contradictions_detected": len(contradictions),
        # R-F174 (2026-05-11): stamp content_hash + source_domain on every
        # new fact so the next ingest of the same content from the same
        # source short-circuits via the dedup check above.
        "content_hash": _content_hash,
        "source_domain": _source_domain,
    }
    if verified_meta:
        new_record.update(verified_meta)
    db["facts"].insert(0, new_record)
    _index_add(new_record)  # R-F1622 — keep O(1) dedup indices in sync

    # R-F1530: auto-queue every fact for verification, regardless of whether
    # the caller supplied source_url/fact_type/entity_name. Previously only
    # facts with all three fields went through verified_intel; everything else
    # was stamped LEGACY_UNVERIFIED and never verified. Now every fact gets
    # queued for background verification.
    try:
        _auto_verify_fact(new_record, topic, content, source)
    except Exception:
        pass  # never let verification break fact storage
    # R-F239 (2026-05-11) — warn-only at WARN_FACTS, no truncation at MAX_FACTS.
    # Pre-R-F239 the truncation `db["facts"] = db["facts"][:MAX_FACTS]` dropped
    # the OLDEST facts (insert(0,...) puts newest at index 0, slice keeps
    # newest 1M, oldest get forgotten). That violates the infinite-memory
    # rule. Now we never truncate; instead, warn the operator at the soft
    # threshold so they can plan cold-storage offload.
    _fact_count = len(db["facts"])
    if _fact_count > WARN_FACTS:
        global _kb_warn_throttle
        _kb_warn_throttle += 1
        if _kb_warn_throttle % 100 == 1:  # log once per 100 over-threshold writes
            logger.warning(
                "[knowledge] R-F239 — fact count %d > warn threshold %d. "
                "NOT truncating (infinite-memory rule). Plan cold-storage offload.",
                _fact_count, WARN_FACTS,
            )
            # ── R-F3935 — THIS WARNING WAS DARK, AND IT IS THE ONLY SAFETY
            # MECHANISM PROTECTING THE INFINITE-MEMORY POLICY. ────────────────
            #
            # R-F239's own comment (line ~76) promises "the operator gets a
            # brain_hook absorb prompting offload to cold storage". The code did
            # `logger.warning` and nothing else. §21a is explicit that a console
            # log is DARK, not wired — so at 1M facts ARIA would write one line
            # per 100 writes into fly logs nobody reads, RSS would keep growing,
            # and the operator would never be told. §19e calls that the worst
            # outcome: a blocker the operator has to discover himself.
            #
            # §7 makes the OFFLOAD a deliberate operator action ("overflow → cold
            # storage, never delete"), and that stays manual on purpose — an
            # automatic offload invented here would be the deletion-adjacent
            # behaviour §7 exists to prevent (cf. R-F173, reversed by R-F238).
            # What must NOT be manual is the NOTICE. Wiring it makes the one
            # automated guarantee actually arrive.
            #
            # Throttled with the log line (1 per 100 over-threshold writes), so a
            # standing overflow cannot flood the gap ledger — an alert that fires
            # continuously is one that gets muted.
            try:
                from . import capability_gaps as _cg
                import asyncio as _aio
                _t = _aio.create_task(_cg.record_gap(
                    gap_type="performance",
                    severity=3,
                    title=(
                        f"Knowledge base at {_fact_count} facts — cold-storage "
                        f"offload needed (warn threshold {WARN_FACTS})"
                    ),
                    detail=(
                        f"§7 infinite memory: nothing is being truncated or evicted, "
                        f"which is correct. But the working set is now {_fact_count} "
                        f"facts and grows with RSS — see /api/aria/memory/health "
                        f"(process.subsystems.facts). The remedy is an OPERATOR-"
                        f"PLANNED cold-storage offload, not deletion and not a "
                        f"higher threshold. Raise ARIA_KB_WARN_FACTS only after the "
                        f"offload, never instead of it."
                    ),
                    source="knowledge:fact_count_overflow",
                ))
                _t.add_done_callback(
                    lambda t: t.exception() if not t.cancelled() else None)
            except Exception:      # pragma: no cover - never block a fact write
                pass
            try:
                from .engine_wiring import wire_failure as _wf3935
                _wf3935(
                    module="knowledge",
                    detail=(
                        f"fact count {_fact_count} exceeds warn threshold "
                        f"{WARN_FACTS}; cold-storage offload is an operator action "
                        f"and has not happened"
                    ),
                    gap_type="performance",
                    source="knowledge:fact_count_overflow",
                )
            except Exception:      # pragma: no cover
                pass
    await _save(record=new_record, kind="fact")   # R-F4022 (C-95)
    # Index for semantic search — runs sync model.encode() under the hood,
    # which holds the GIL. Must be off the event loop or it will block the
    # /teach reply for hundreds of milliseconds (longer if first call cold-
    # loads the model). Callers that intend to batch-index downstream
    # (e.g. researcher._process_analysis with semantic_search
    # .index_facts_batch) pass skip_semantic_index=True to avoid
    # double-encoding (F83 fix 2026-04-29).
    if not skip_semantic_index:
        try:
            # R-F807 (2026-05-22) — queue for background indexing
            # instead of awaiting the encode synchronously here.
            # store_fact returns in microseconds + queue-put time;
            # the actual model.encode runs in a single background
            # worker that batches up to 32 items per call. Live
            # 2026-05-22 evidence: awaiting encode per-call drove
            # absorb wall-time to 20 min under concurrent load.
            # Fallback path: if the queue is disabled
            # (ARIA_INDEX_QUEUE_DISABLED=1) or full / no event
            # loop, fall back to the synchronous to_thread path
            # so we never silently lose the index update.
            from . import _semantic_index_queue as _siq
            _fact_id = db["facts"][0]["id"]
            _text = f"{topic} {content}"
            _meta = {"confidence": confidence}
            queued = await _siq.enqueue(_fact_id, _text, _meta)
            if not queued:
                # Fallback to the legacy sync path so the index
                # update isn't lost when the queue is unavailable.
                from .semantic_search import index_fact
                import asyncio as _aio
                await _aio.to_thread(index_fact, _fact_id, _text, _meta)
        except Exception:
            pass
    # Index into the persistent RAG store as well so retrieval can find
    # it. Callers that intend to batch-upsert downstream (e.g.
    # researcher._process_analysis with rag_store.add_facts_batch) pass
    # skip_rag_ingest=True to avoid double-encoding (F24 fix 2026-04-27).
    if not skip_rag_ingest:
        try:
            from . import rag_store
            await rag_store.ingest_fact(
                fact_id=new_id,
                topic=topic,
                content=content,
                confidence=confidence,
                source=source,
            )
        except Exception:
            pass

    # R-F96 (2026-05-09): record domain freshness so R-F88 tracker
    # accumulates state. Topic maps directly onto domain when it matches
    # a known domain; otherwise falls back to free-text topic which still
    # writes a record (just with the default 7-day staleness window).
    try:
        from . import learning_progress as _lp
        # Use the topic itself as the domain — knowledge facts are stored
        # by topic and the topic vocabulary already aligns with domains
        # (sanctions_screening, eccn_classification, etc).
        domain = topic.strip().lower().replace(" ", "_")[:80] if topic else None
        if domain:
            await _lp.record_refresh(
                domain,
                source=f"knowledge:{source}",
                facts_added=1,
            )
    except Exception:
        pass

    return {"action": "created", "fact_id": new_id, "contradictions": contradictions}


@fail_wire(module="knowledge", gap_type="engine_failure")
async def get_contradictions(limit: int = 50) -> list[dict]:
    """Return facts that have detected contradictions or version history.

    This is what powers ARIA's self-aware "I used to think X, now Y" reasoning.
    """
    db = await _load()
    result = []
    for f in db.get("facts", []):
        if f.get("contradictions_detected", 0) > 0 or f.get("history") or f.get("pending_conflicts"):
            result.append({
                "id": f.get("id"),
                "topic": f.get("topic"),
                "current_content": f.get("content"),
                "current_confidence": f.get("confidence"),
                "current_source": f.get("source"),
                "updated_at": f.get("updatedAt"),
                "history": f.get("history") or [],
                "pending_conflicts": f.get("pending_conflicts") or [],
                "contradictions_count": f.get("contradictions_detected", 0),
            })
        if len(result) >= limit:
            break
    return result


@fail_wire(module="knowledge", gap_type="engine_failure")
async def record_query(query: str, summary: str, market: str = "", category: str = "") -> None:
    db = await _load()
    _record = {
        "id": str(uuid.uuid4())[:8],
        "query": query,
        "summary": summary,
        "market": market,
        "category": category,
        "createdAt": datetime.now(timezone.utc).isoformat(),
    }
    db["queries"].insert(0, _record)
    # R-F239 (2026-05-11) — no truncation; queries persist forever per the
    # infinite-memory rule. MAX_QUERIES is a warn sentinel only.
    await _save(record=_record, kind="query")   # R-F4022 (C-95)


@fail_wire(module="knowledge", gap_type="engine_failure")
async def store_learning(correction: str, context: str = "") -> None:
    db = await _load()
    _record = {
        "id": str(uuid.uuid4())[:8],
        "correction": correction,
        "context": context,
        "createdAt": datetime.now(timezone.utc).isoformat(),
    }
    db["learnings"].insert(0, _record)
    # R-F239 (2026-05-11) — no truncation; learnings persist forever per
    # the infinite-memory rule.
    await _save(record=_record, kind="learning")   # R-F4022 (C-95)


@fail_wire(module="knowledge", gap_type="engine_failure")
def all_facts() -> list[dict]:
    """R-F164 (2026-05-11): expose the raw fact list to callers that need
    structured access. coverage_heatmap._count_facts_for_cell was using
    `hasattr(_k, "search")` then calling _k.search(query) — but knowledge
    has no `search` function (only `search_knowledge`, which returns a
    formatted string for prompt injection, not a list). The hasattr check
    silently evaluated False, and every coverage cell returned fact_count=0,
    leaving the dashboard heatmap at 867/867 absent indefinitely. This
    accessor returns a snapshot so the heatmap matcher can iterate.

    Returns the full in-memory fact list as a *new* list. Mutations on
    the result don't affect the cache (each fact dict is still shared by
    reference — callers must treat them as read-only)."""
    if not _cache:
        return []
    facts = _cache.get("facts") if isinstance(_cache, dict) else None
    return list(facts) if isinstance(facts, list) else []


@fail_wire(module="knowledge", gap_type="engine_failure")
def facts_by_tag(tag: str, limit: int = 50) -> list[dict]:
    """R-F245 (2026-05-11): tag-aware fact retrieval.

    Pre-R-F245 the only retrieval path was `search_knowledge(query)`
    which did word-tokenised keyword search. Inventory questions like
    "what do you know about angola_procurement" missed most relevant
    facts because:
      - knowledge facts about Angola don't literally contain the
        string "angola_procurement"
      - The query words "angola_procurement" tokenise as a single
        17-char word (no underscore split), missing the actual
        Angola/procurement word matches

    This accessor matches a TAG against the topic, source, source_domain,
    and content fields of every fact, returning the top-`limit` matches
    sorted by recency. Tag-shaped queries (snake_case, kebab-case,
    short labels) hit this; free-text queries continue to use
    search_knowledge.

    Matches:
      - The literal tag string (case-insensitive)
      - Each underscore- or hyphen-split component (e.g.
        "angola_procurement" → ["angola", "procurement"]) — at least
        ONE component must appear in topic/content for the fact to
        count as a tag hit

    Returns a list of fact dicts (not a formatted string) so callers
    can render however they need.
    """
    if not _cache:
        return []
    facts = _cache.get("facts") if isinstance(_cache, dict) else None
    if not isinstance(facts, list):
        return []
    tag_lower = (tag or "").strip().lower()
    if not tag_lower:
        return []
    # Tokenise the tag — accept snake_case, kebab-case, dot-separated,
    # plain whitespace. R-F246 (2026-05-11): added `.` to the splitter
    # so "u.s. sanctions" → ["u","s","sanctions"] (then ≥3-char filter
    # drops "u"/"s") and "sam.gov" → ["sam","gov"]. The literal-tag
    # check above still catches "sam.gov" verbatim when content contains
    # it, so split-and-AND is purely additive recall.
    components = [
        c for c in re.split(r"[_\-\s.]+", tag_lower) if len(c) >= 3
    ]
    if not components:
        components = [tag_lower]

    matches: list[dict] = []
    for f in facts:
        if not isinstance(f, dict):
            continue
        text_blob = " ".join(str(f.get(k) or "") for k in
                             ("topic", "content", "source",
                              "source_domain", "entity_name")).lower()
        if not text_blob:
            continue
        # Literal tag match OR all-components match (AND across components)
        if tag_lower in text_blob:
            matches.append(f)
            continue
        # Sub-component match — require ALL non-trivial components to
        # appear so the result is genuinely tag-relevant (a fact about
        # "Angola tourism" won't match "angola_procurement" because
        # "procurement" is missing).
        if len(components) >= 2 and all(c in text_blob for c in components):
            matches.append(f)

    # Sort by recency (updatedAt or createdAt fall-through)
    matches.sort(
        key=lambda f: f.get("updatedAt") or f.get("createdAt") or "",
        reverse=True,
    )
    return matches[: max(1, min(limit, 200))]


def _rank_knowledge_facts(query: str, limit: int) -> list[dict]:
    """Return ranked fact records for the shared knowledge search paths."""
    if not _cache:
        return []
    words = [w.lower() for w in query.split() if len(w) > 2]
    if not words:
        return []

    # R-F939 — reuse the lowercased fact-text cache; clear it when the facts
    # list object is replaced (reload), so removed/renumbered ids don't linger.
    global _search_lc_facts_id
    facts = _cache["facts"]
    if id(facts) != _search_lc_facts_id:
        _search_lc.clear()
        _search_lc_facts_id = id(facts)

    scored: list[tuple[float, dict]] = []
    for idx, f in enumerate(facts):
        # R-F939 — yield the GIL during a cold/large scan so this worker thread
        # (the 7-layer-context pool) can't starve the event loop while it builds
        # the cache for the first time. Cheap once the cache is warm.
        # R-F2086 — yield every 256 facts (was 2048): a live wedge stack still
        # caught this cold scan stalling the loop 5s+ post-deploy. 8x more
        # frequent GIL release keeps any per-chunk hold to ~tens of ms. The boot
        # prewarm (main.py) builds the cache off the request path so users never
        # hit the cold scan; this is the in-scan safety net.
        if (idx & 0xFF) == 0:
            time.sleep(0)
        content = f.get("content") or ""
        clen = len(content)
        fid = f.get("id")
        cached = _search_lc.get(fid) if fid else None
        if cached is not None and cached[0] == clen:
            text = cached[1]
        else:
            text = f"{f.get('topic') or ''} {content}".lower()
            if fid:
                _search_lc[fid] = (clen, text)
        score = 0
        for w in words:
            if w in text:
                score += 3
        # R-F4133 (C-168) — the popularity boost is a TIE-BREAKER BETWEEN
        # RELEVANT FACTS, and it used to be added BEFORE this threshold. That
        # let it manufacture relevance: `accessCount` counts RE-ABSORPTION (all
        # three of its bumps are in store_fact, fired when the same content or
        # topic is stored again), which is exactly what the crawl and reading
        # loops do all day. A fact matching nothing scored up to 5 and beat a
        # fact matching one query word, which scores 3 — and search_knowledge
        # renders the winners into the chat prompt under "[ARIA KNOWLEDGE BASE
        # — verified facts]". Measured live on 2026-08-17: ~10.8% of 567,720
        # facts carry accessCount >= 1 (max 3,593), so ~61,000 unrelated rows
        # entered the candidate set of EVERY query. Self-worsening under §7:
        # the more ARIA reads, the higher the noise floor, and it never falls.
        if score > 0:
            score += min(f.get("accessCount", 0), 5)
            scored.append((score, f))

    scored.sort(key=lambda x: x[0], reverse=True)
    ranked = [fact for _, fact in scored[: max(1, min(limit, 200))]]

    # ── R-F3615 — QUARANTINE, NOT DELETION ──────────────────────────────────
    # The R-F3033 window (2026-07-25..07-31) let modules absorb a reasoning
    # model's chain of thought as knowledge. R-F3615's write-side guard in
    # brain_hook.absorb stops NEW rows; these are the ones already stored, and
    # they surfaced live on 2026-08-01 rendered as "verified facts".
    #
    # They are FILTERED FROM RECALL, never removed. §7 is binding — ARIA has
    # infinite memory: no TTL, no prune, no eviction. The rows stay on disk and
    # stay auditable; they simply stop being served as established fact. That is
    # reversible, which deleting them would not be.
    #
    # Filtered HERE rather than in search_knowledge() because both the rendered
    # block and the programmatic consumer (search_fact_records) rank through
    # this function — fixing only the renderer would leave the other serving
    # deliberation, which is the producer/consumer split that caused several of
    # this session's other defects.
    try:
        from .deliberation_guard import looks_like_deliberation as _r3615
        kept = [f for f in ranked if not _r3615(f.get("content") or "")]
        if len(kept) != len(ranked):
            logger.warning(
                "[R-F3615] quarantined %d deliberation row(s) from recall "
                "(retained on disk per §7, not served as fact)",
                len(ranked) - len(kept),
            )
        return kept
    except Exception:
        logger.debug("[R-F3615] recall quarantine failed (non-fatal)", exc_info=True)
        return ranked


@fail_wire(module="knowledge", gap_type="engine_failure")
def search_fact_records(query: str, limit: int = 10) -> list[dict]:
    """Return ranked knowledge fact records for programmatic consumers."""
    return _rank_knowledge_facts(query, limit)


@fail_wire(module="knowledge", gap_type="engine_failure")
def search_knowledge(query: str) -> str:
    """Synchronous search for prompt injection. Returns formatted string."""
    top = _rank_knowledge_facts(query, 10)
    if not top:
        return ""

    # Two-tier rendering so rich case-library findings (Serban et al.)
    # aren't silently truncated to their opening header. Any fact whose
    # source begins with `dd_case_library:` is rendered in full up to
    # 4000 chars — the whole narrative including HARD STOPS, compliance
    # exposure, and the recommended Arkmurus action. Other facts stay
    # at a 400-char snippet so the context budget is still bounded.
    # Top N is lowered to 10 to compensate for longer case rows.
    lines = ["\n[ARIA KNOWLEDGE BASE — verified facts]"]
    for f in top:
        src = (f.get("source") or "").lower()
        content = f.get("content") or ""
        if src.startswith("dd_case_library:"):
            snippet = content[:4000]
            lines.append(
                f"- [{f['confidence']}] {f['topic']} "
                f"[CASE LIBRARY — authoritative prior finding, apply directly]:\n{snippet}"
            )
        else:
            lines.append(f"- [{f['confidence']}] {f['topic']}: {content[:400]}")
    return "\n".join(lines)


@fail_wire(module="knowledge", gap_type="engine_failure")
async def auto_extract_facts(
    user_query: str,
    aria_response: str,
    *,
    tool_context: str | None = None,
    verifier_verdict: str | None = None,
) -> None:
    """Auto-mine tagged facts from ARIA responses — BUT only when the
    response is grounded in a tool call verified by source_verifier.

    Past bug: ARIA's own [CONFIRMED] tags were mined and persisted as
    permanent knowledge, creating a hallucination feedback loop where
    a bad LLM guess became 'verified fact' after one session. We now
    refuse to extract unless:

      (a) tool_context is non-empty (the response cited a tool result), AND
      (b) source_verifier returned a grounded verdict

    Even then, [CONFIRMED] is demoted to [PROBABLE] at ingest — only
    /teach and the correction_learner can mint CONFIRMED facts.
    """
    if not _cache:
        return

    # Hard gate: no tool context = no knowledge extraction.
    if not tool_context or not tool_context.strip():
        return

    # Hard gate: source_verifier must have actually verified something.
    # Acceptable verdicts: 'grounded', 'grounded_partial' (both indicate
    # at least one citation was checked against a real source).
    if verifier_verdict not in ("grounded", "grounded_partial"):
        return

    patterns = [
        (r"\[CONFIRMED\]\s*(.+?)(?:\n|$)", "PROBABLE"),  # DEMOTED
        (r"\[PROBABLE\]\s*(.+?)(?:\n|$)", "PROBABLE"),
    ]
    import asyncio
    for pat, conf in patterns:
        for m in re.finditer(pat, aria_response):
            text = m.group(1).strip()[:300]
            if len(text) > 20:
                topic = text[:60].rstrip(".")
                asyncio.create_task(store_fact(topic, text, "aria_auto_verified", conf))


@fail_wire(module="knowledge", gap_type="engine_failure")
async def extract_facts_from_reading(
    article_text: str,
    *,
    source: str,
    title: str = "",
    url: str = "",
) -> int:
    """R-F200 (2026-05-11) — local-only auto-extract path for student
    reading-session outputs.

    Pre-R-F200, knowledge.auto_extract_facts was the only auto-mining
    path AND it required (tool_context + grounded verifier) — but
    callers always passed tool_context=None, so it was dead. Reading
    session was contributing nothing to permanent knowledge auto-mining.

    This separate path mines [CONFIRMED]/[PROBABLE] tags from RSS
    article bodies ONLY (source starts with 'reading:' or
    'research_degraded:'). The trust gate is the SOURCE itself — RSS
    feeds are tier-2 by definition; no LLM mediation is involved so
    the brain-poisoning concern doesn't apply.

    Returns the number of facts extracted.
    """
    if not article_text or not isinstance(article_text, str):
        return 0
    # Trust gate: source must be a reading/research-derived source.
    src_lower = (source or "").lower()
    if not (
        src_lower.startswith("reading:")
        or src_lower.startswith("research_degraded:")
        or src_lower.startswith("research:")
    ):
        return 0
    # Tag-mining patterns — same shape as auto_extract_facts but no
    # verifier gate (reading source IS the trust).
    patterns = [
        (r"\[CONFIRMED\]\s*(.+?)(?:\n|$)", "PROBABLE"),  # demoted
        (r"\[PROBABLE\]\s*(.+?)(?:\n|$)", "PROBABLE"),
        (r"\[ASSESSED\]\s*(.+?)(?:\n|$)", "ASSESSED"),
    ]
    n = 0
    for pat, conf in patterns:
        for m in re.finditer(pat, article_text):
            text = m.group(1).strip()[:300]
            if len(text) > 20:
                # R-F207 (2026-05-11) — use PER-TAG distinct topic, not
                # the article title. Pre-R-F207 every tag in an article
                # used the same `title[:60]` topic, so the topic-dedup
                # branch in store_fact overwrote previously-stored
                # content in place. Function returned n=5 but only the
                # last tag survived. Matches the sibling auto_extract_
                # facts pattern: `text[:60]` per tag.
                topic = text[:60].rstrip(".")
                try:
                    await store_fact(topic, text, source, conf, source_url=url[:500])
                    n += 1
                except Exception:
                    pass
    return n


@fail_wire(module="knowledge", gap_type="engine_failure")
async def consolidate_facts() -> dict:
    """Merge near-duplicate facts and prune stale ones."""
    from datetime import datetime, timezone
    db = await _load()
    facts = db.get("facts", [])
    if not facts:
        return {"merged": 0, "pruned": 0, "total_before": 0, "total_after": 0}

    total_before = len(facts)
    now = datetime.now(timezone.utc)

    # ── 1. Merge near-duplicate facts (same topic, case-insensitive) ─────
    merged = 0
    seen: dict[str, int] = {}  # topic_lower → index of best fact
    to_remove: set[int] = set()

    for i, f in enumerate(facts):
        key = f["topic"].strip().lower()
        if key in seen:
            # Keep the one with highest confidence rank / access_count
            existing_idx = seen[key]
            existing = facts[existing_idx]
            # Compare: prefer higher accessCount, then more recent update
            e_score = existing.get("accessCount", 0)
            f_score = f.get("accessCount", 0)
            if f_score > e_score:
                # Current fact is better — remove the existing one
                to_remove.add(existing_idx)
                seen[key] = i
            else:
                to_remove.add(i)
            merged += 1
        else:
            seen[key] = i

    # ── 2. FLAG stale facts — NON-DESTRUCTIVE (R-F962, CLAUDE.md §7) ─────
    # §7 is binding: infinite memory — NO TTL, NO oldest-first prune, NO
    # eviction; overflow → cold storage, never delete. The pre-R-F962 code
    # here DELETED facts >90 days old with accessCount<2 — a direct §7
    # violation (same class as R-F173, reversed by R-F238). It was reachable
    # only via the manual POST /api/aria/neural/consolidate (no cron), so it
    # was a latent landmine rather than an active drain, but a landmine still.
    # We now MARK staleness in-place instead of deleting: every fact is kept
    # forever; a `stale` flag makes age visible — the legitimate kernel of the
    # 2026-05-28 self-gap-analysis "recency layer" request — without losing
    # knowledge. The flag is self-correcting: facts that no longer meet the
    # criteria (re-accessed, recently re-confirmed) are un-flagged next pass.
    flagged_stale = 0
    ninety_days_ago = now.timestamp() - 90 * 86400
    for i, f in enumerate(facts):
        if i in to_remove:
            continue  # already merged away as a duplicate
        is_stale = False
        created = f.get("createdAt", "")
        if created:
            try:
                created_ts = datetime.fromisoformat(created.replace("Z", "+00:00")).timestamp()
                is_stale = created_ts < ninety_days_ago and f.get("accessCount", 0) < 2
            except (ValueError, TypeError):
                is_stale = False
        if is_stale:
            if not f.get("stale"):
                flagged_stale += 1
            f["stale"] = True
        elif f.get("stale"):
            f["stale"] = False  # criteria no longer met → clear the flag

    # ── 3. Rebuild facts list — ONLY merged duplicates are removed.
    #       Aged facts are FLAGGED (above), never deleted (§7). ───────────
    db["facts"] = [f for i, f in enumerate(facts) if i not in to_remove]
    # R-F4022 (C-95) — STRUCTURAL: merged duplicates were removed and stale
    # flags were edited in place. An upsert journal cannot express either, so
    # this compacts.
    await _save(structural=True)

    total_after = len(db["facts"])
    logger.info(
        "Knowledge consolidation: merged %d (dupes), flagged_stale %d, "
        "%d → %d facts (§7: no age-prune)",
        merged, flagged_stale, total_before, total_after,
    )
    return {
        "merged": merged,
        # R-F962 — age-pruning removed (§7). Key retained at 0 for callers
        # that read it; nothing is deleted by age anymore.
        "pruned": 0,
        "flagged_stale": flagged_stale,
        "total_before": total_before,
        "total_after": total_after,
    }


@fail_wire(module="knowledge", gap_type="engine_failure")
async def get_stats() -> dict:
    db = await _load()
    return {
        "totalFacts": len(db["facts"]),
        "totalQueries": len(db["queries"]),
        "totalLearnings": len(db["learnings"]),
    }


@fail_wire(module="knowledge", gap_type="engine_failure")
async def purge_by_keywords(
    keywords: list[str],
    *,
    dry_run: bool = False,
    fields: tuple[str, ...] = ("topic", "content", "source", "source_url"),
) -> dict:
    """Remove every fact whose listed text fields contain any of the
    given keywords (case-insensitive substring match). Used for surgical
    cleanup when a fabricated answer was absorbed into the knowledge base
    via the pay-once-remember-forever pattern.

    Background: 2026-04-24 OpenClaw incident — Brave Answers fabricated a
    fictional WhatsApp gateway product on a self-infra question; the
    answer was absorbed via brain_hook into the knowledge facts store
    (and mem0/RAG/reasoning_library) tagged [CONFIRMED]. There was no
    keyword-purge tooling for facts — only intel_ledger had it. This
    function fills the gap.

    Match semantics: ANY keyword present in ANY of the listed fields
    triggers removal. Case-insensitive substring. Pass `dry_run=True` to
    preview before committing.

    Returns:
        {scanned, removed, dry_run, keywords_used, removed_samples: [...]}
    """
    if not keywords:
        return {
            "scanned": 0, "removed": 0, "dry_run": dry_run,
            "keywords_used": [], "removed_samples": [],
        }

    needles = [k.lower().strip() for k in keywords if k and k.strip()]
    if not needles:
        return {
            "scanned": 0, "removed": 0, "dry_run": dry_run,
            "keywords_used": [], "removed_samples": [],
        }

    db = await _load()
    facts = db.get("facts", [])
    keep: list[dict] = []
    removed_samples: list[dict] = []
    n_removed = 0

    for fact in facts:
        if not isinstance(fact, dict):
            keep.append(fact)
            continue
        haystack = " ".join(
            str(fact.get(f, "") or "").lower() for f in fields
        )
        hit = next((n for n in needles if n in haystack), None)
        if hit is None:
            keep.append(fact)
            continue
        n_removed += 1
        if len(removed_samples) < 25:
            removed_samples.append({
                "id": fact.get("id", ""),
                "topic": (fact.get("topic", "") or "")[:120],
                "source": (fact.get("source", "") or "")[:120],
                "matched_keyword": hit,
            })

    if not dry_run and n_removed > 0:
        db["facts"] = keep
        # R-F4022 (C-95) — STRUCTURAL: a purge is a deletion, and replaying an
        # upsert journal over it would resurrect exactly what was purged.
        await _save(structural=True)
        logger.warning(
            "knowledge.purge_by_keywords: removed %d facts matching %s",
            n_removed, needles,
        )

    return {
        "scanned": len(facts),
        "removed": n_removed,
        "dry_run": dry_run,
        "keywords_used": needles,
        "removed_samples": removed_samples,
    }

# R-F2538: R-F2119 import-time wire_failure("module shutdown") block removed — it fired a FALSE engine_failure gap on every import (not at shutdown); do not re-add.
