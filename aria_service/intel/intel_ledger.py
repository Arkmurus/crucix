"""
Intelligence Ledger — permanent signal store.
Ported from lib/aria/intel_ledger.mjs.

Retention: previously 30-day rolling, now permanent (100-year retention
sentinel + 1M-signal cap). Operator explicitly asked for forever memory.

Persistence layout (F110, 2026-04-30 — mirrors F94 knowledge):
  primary  : disk JSON at /data/aria_signals.json (atomic write)
  hydrate  : disk → legacy Redis blob → empty
  snapshot : periodic copy to Redis for off-host backup (every
             SNAPSHOT_INTERVAL_S, only if dirty since last snapshot)

Why this shape:
  Pre-F110, every add_signal/ingest_sweep_signals call did a full
  rs.set_json(KEY, _cache) — at 4043 signals × ~600 bytes that's a
  ~2.5 MB Redis SET per write. Sweep cycles can ingest 50+ signals
  in a burst, and Upstash silently truncates values > 1 MB, which is
  what cost us 2587 signals on 2026-04-29 (memo: F87/F88). Mirroring
  F94's pattern: disk is canonical, Redis is a 10-min off-host snapshot.
  Public API (add_signal / ingest_sweep_signals / query_ledger / get_*
  AND the de-facto-public _load() that 5 modules read) is unchanged.
"""
from __future__ import annotations

import asyncio
import base64
import gzip
import json
import logging
import os
import re
import tempfile
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

from . import redis_store as rs
from .wire import fail_wire

logger = logging.getLogger("aria.intel.ledger")

# R-F1318: wire intel_ledger's own health to the brain
try:
    from .engine_wiring import wire_success as _ws1318b, wire_failure
    _ws1318b(
        module="intel_ledger",
        summary="Intel Ledger active — permanent signal store",
        source_id="intel_ledger:R-F1318",
    )
except Exception:
    pass

KEY = "crucix:intel_ledger"
# R-F239 (2026-05-11) — MAX_SIGNALS is a WARN THRESHOLD, not a hard cap.
# Per the infinite-memory rule (memory/aria_infinite_memory.md), signals
# persist forever. Pre-R-F239 the truncation at line 339 dropped oldest
# signals beyond 1M — that's forgetting, forbidden. Now the sentinel is
# raised to 100M (real growth wouldn't trip it for ~100 years at current
# rate) and _prune() no longer truncates; it just warns.
MAX_SIGNALS = 100_000_000
WARN_SIGNALS = int(os.getenv("ARIA_LEDGER_WARN_SIGNALS", "1000000"))
RETENTION_DAYS = 36500  # 100 years — effectively permanent
_signal_warn_throttle = 0

# R-F36 (2026-05-06): mirrors F111's knowledge-snapshot fix. The 10-min
# Redis snapshot of the ledger crossed Upstash's 4 MB warn threshold at
# ~13.7k signals (4.51 MB raw) and would hit the tier cap in days. Signal
# JSON compresses ~5-8× with gzip, so a base64+gzip wrapper buys multi-month
# headroom. Magic prefix lets the loader distinguish gzipped values from
# legacy raw-JSON blobs written before this fix.
_GZ_PREFIX = "GZ1:"


def _encode_snapshot(obj: dict) -> str:
    # R-F727 (2026-05-19): same GIL fast-path as _write_to_disk_atomic
    # — fly snapshot encode runs in to_thread per R-F714 but `default=str`
    # forces CPython's pure-Python encoder which holds the GIL across
    # the full iteration. wedge_673 captured this exact site contributing
    # to the 213.97s stall.
    try:
        raw = json.dumps(obj).encode("utf-8")
    except TypeError:
        raw = json.dumps(obj, default=str).encode("utf-8")
    gz = gzip.compress(raw, compresslevel=6)
    return _GZ_PREFIX + base64.b64encode(gz).decode("ascii")


def _decode_snapshot(value: Any) -> dict | None:
    """Decode a Redis ledger snapshot. Returns None if empty/unparseable.
    Accepts both new gzipped payloads (prefix `GZ1:`) and legacy raw-JSON
    blobs so existing snapshots migrate forward on the next read. Tolerates
    str, bytes, and the dict that legacy `rs.get_json` test fixtures hand
    back so swapping the call site doesn't break them."""
    if not value:
        return None
    if isinstance(value, dict):
        return value if "signals" in value else None
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
            if isinstance(data, dict) and "signals" in data:
                return data
        except Exception as e:
            logger.warning("intel_ledger: gzip snapshot decode failed: %s", e)
        return None
    try:
        data = json.loads(value)
        if isinstance(data, dict) and "signals" in data:
            return data
    except Exception:
        pass
    return None

_cache: dict | None = None

# Debounced-flush state. Writes mark _dirty; a single background task
# flushes to disk after FLUSH_DEBOUNCE_S. Sweep ingest of 50+ signals in a
# burst coalesces into one disk write instead of N Redis SETs.
_dirty: bool = False
_dirty_since_snapshot: bool = False
_flush_task: asyncio.Task | None = None
_flusher_started: bool = False
_flusher_loop: object | None = None  # R-F3321: the loop _flush_task belongs to
_flusher_stop = False
FLUSH_DEBOUNCE_S = 2.0
# ── R-F4108 (C-141): journalled writes ───────────────────────────────────────
# `_write_to_disk_atomic` rewrote the WHOLE ledger every debounced flush —
# 81,971 signals / 35.5 MB, plus fsync + rename + dir-fsync, to persist however
# few signals changed. Live 2026-08-17 that was 52.0% and 59.3% of two
# consecutive profiler snapshots. §7 forbids eviction, so the cost rises
# without bound: the better ARIA's memory gets, the more starved she becomes.
#
# An APPEND journal is correct here because signals are never edited in place —
# verified by AST: the only two assignments into `signals` are whole-list
# replacements (`_prune`, the keyword purge), which are structural and compact.
_journal_pending: list[dict] = []
#: One-element box so `_save` can flag a compaction without a `global`.
_force_full_rewrite: list[bool] = [False]
#: Compact once replaying the journal would cost more than a snapshot.
JOURNAL_MAX_BYTES = 32 * 1024 * 1024


def _journal_path() -> str:
    """Derived from `_DISK_PATH` at call time, not cached at import — tests
    repoint the ledger path, and a cached journal path would then write beside
    the wrong file."""
    return _DISK_PATH + ".journal.jsonl"


def _append_journal(records: list[dict]) -> None:
    """Append changed signals, one JSON object per line, in a single write."""
    if not records:
        return
    blob = "".join(json.dumps(r, default=str) + "\n" for r in records)
    with open(_journal_path(), "a", encoding="utf-8") as f:
        f.write(blob)
        f.flush()
        os.fsync(f.fileno())


def _read_journal() -> list[dict]:
    """Replay the journal, newest-first to match the ledger's head-insert
    ordering. A torn trailing line is skipped, not fatal."""
    out: list[dict] = []
    try:
        with open(_journal_path(), "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except ValueError:
                    continue
    except OSError:
        return []
    out.reverse()          # journal is append-order; the ledger is newest-first
    return out


def _drop_journal() -> None:
    try:
        os.unlink(_journal_path())
    except OSError:
        pass
# R-F4109 (C-142) — this cadence is only spent when the target is a GENUINE
# second failure domain; see `_snapshot_target_is_offhost`. With the sqlite
# backend on the same volume it was 8.18 MB of gzip every 600 s into the file's
# own neighbour, which is not a backup.
SNAPSHOT_INTERVAL_S = 600.0  # 10 min — off-host backup cadence (when off-host)
_snapshot_skip_announced = False   # announce the skip ONCE per process


def _resolve_disk_path() -> str:
    """Match knowledge.py / rag_store.py resolution rules so the same volume
    is used. Override with ARIA_LEDGER_PATH for tests / dev shells. Falls
    back to the OS temp dir on hosts without /data (Windows dev, CI)."""
    override = os.getenv("ARIA_LEDGER_PATH", "").strip()
    if override:
        return override
    if Path("/data").exists() and os.access("/data", os.W_OK):
        return "/data/aria_signals.json"
    fallback = os.path.join(tempfile.gettempdir(), "aria_signals.json")
    logger.warning(
        "intel_ledger: /data volume not mounted — falling back to %s. "
        "State will NOT persist across restarts. Mount a fly.io volume "
        "at /data to enable persistence.",
        fallback,
    )
    return fallback


_DISK_PATH = _resolve_disk_path()


def _read_from_disk() -> dict | None:
    try:
        with open(_DISK_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and "signals" in data:
            return data
    except FileNotFoundError:
        return None
    except Exception as e:
        logger.warning("intel_ledger: disk load failed at %s: %s", _DISK_PATH, e)
    return None


def _fsync_dir(dir_path: str) -> None:
    """R-F1420 — fsync a directory so a contained rename is durable.
    Best-effort (directory fsync is unsupported on Windows); the file-level
    fsync is the load-bearing guarantee."""
    try:
        dfd = os.open(dir_path, os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    except (OSError, AttributeError, ValueError):
        pass


def _write_to_disk_atomic(data: dict) -> None:
    """Atomic write via temp file + rename so a crash mid-write can't
    corrupt the canonical signals file.

    R-F727 (2026-05-19): json.dump fast path without `default=`. The
    C-accelerated `_json` encoder releases the GIL between operations;
    passing `default=str` forces the pure-Python encoder, which holds
    the GIL through the whole serialisation (wedge_673 captured 1
    worker thread here while 3 others were in neural_memory and
    1 was in knowledge — 5 GIL holders, 213.97s loop stall). Fast
    path first; fall back to default=str on TypeError so a stray
    non-native value (datetime, set, etc.) still serializes safely."""
    target = _DISK_PATH
    target_dir = os.path.dirname(target) or "."
    fd, tmp_path = tempfile.mkstemp(
        prefix=".aria_signals.", suffix=".json.tmp", dir=target_dir,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            try:
                json.dump(data, f)
            except TypeError:
                f.seek(0)
                f.truncate()
                json.dump(data, f, default=str)
            # R-F1420 — flush + fsync DATA to disk before the rename so a
            # host crash / power loss can't lose still-in-page-cache signals
            # (atomic rename only guards against torn files).
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, target)
        _fsync_dir(target_dir)  # R-F1420: make the rename entry durable
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


# ── R-F4110 (C-143): the ledger's own orphaned temp files ────────────────────
#
# `_write_to_disk_atomic` unlinks its mkstemp file ONLY on the `except` branch.
# A process killed mid-write — every deploy, every restart — orphans it, and
# with FLUSH_DEBOUNCE_S = 2.0 (C-141) the write window is a large fraction of
# uptime, so the two defects compound. Measured on aria-intel 2026-08-17:
#
#     108 files · 401,138,594 bytes · 382.6 MB
#     oldest 2026-05-17, newest 2026-07-31, individual files up to 25 MB
#
# A repo-wide search found NO cleanup anywhere — the only reference to the
# prefix was the mkstemp call that creates them.
_TMP_PREFIX = ".aria_signals."
_TMP_SUFFIX = ".json.tmp"
#: A young temp file may be an IN-FLIGHT write; removing it corrupts a flush
#: happening right now. Writes complete in seconds, so a day is generous.
_TMP_MIN_AGE_S = 86400.0


def _tmp_orphans() -> list[tuple[str, int, float]]:
    """(path, bytes, mtime) for every file matching OUR temp pattern.

    Scoped to the ledger's own prefix AND suffix so it can never reach the
    canonical file or another module's temporaries.
    """
    out: list[tuple[str, int, float]] = []
    try:
        d = os.path.dirname(_DISK_PATH) or "."
        for name in os.listdir(d):
            if not (name.startswith(_TMP_PREFIX) and name.endswith(_TMP_SUFFIX)):
                continue
            p = os.path.join(d, name)
            try:
                st = os.stat(p)
            except OSError:
                continue
            if not os.path.isfile(p):
                continue
            out.append((p, int(st.st_size), float(st.st_mtime)))
    except OSError:
        pass
    return out


@fail_wire(module="intel_ledger", gap_type="engine_failure")
def tmp_orphan_report() -> dict:
    """How much disk is stranded in temp files nobody will ever read?"""
    items = _tmp_orphans()
    now = time.time()
    stale = [i for i in items if (now - i[2]) >= _TMP_MIN_AGE_S]
    oldest = max((now - m for _, _, m in items), default=0.0)
    return {
        "count": len(items),
        "bytes": sum(b for _, b, _ in items),
        "oldest_age_days": round(oldest / 86400.0, 1),
        "would_remove": len(stale),
        "would_free_bytes": sum(b for _, b, _ in stale),
        "sweep_enabled": (os.getenv("ARIA_LEDGER_TMP_SWEEP", "") or "").strip() == "1",
    }


@fail_wire(module="intel_ledger", gap_type="engine_failure")
def sweep_tmp_orphans() -> dict:
    """Reclaim stranded temp files — REPORT-ONLY unless the operator opts in.

    §26 governs this: *"never touch data stores destructively (archive with a
    manifest; `rm` is never the answer)"*. So:

      * **Report-only by default.** Removing 382 MB is the operator's call, not
        a session's. Set `ARIA_LEDGER_TMP_SWEEP=1` to enable.
      * **A manifest is written BEFORE anything is removed**, recording name,
        size and mtime — what went stays knowable even when the bytes do not.
      * **Prefix-scoped and age-gated**, so it can reach neither the canonical
        ledger nor an in-flight write.

    Never raises: reclaim is housekeeping and must not break the caller.
    """
    now = time.time()
    items = _tmp_orphans()
    stale = [i for i in items if (now - i[2]) >= _TMP_MIN_AGE_S]
    enabled = (os.getenv("ARIA_LEDGER_TMP_SWEEP", "") or "").strip() == "1"
    result = {
        "found": len(items),
        "would_remove": len(stale),
        "would_free_bytes": sum(b for _, b, _ in stale),
        "removed": 0,
        "freed_bytes": 0,
        "manifest": None,
        "enabled": enabled,
    }
    if not stale:
        return result
    if not enabled:
        logger.info(
            "[R-F4110] %d orphaned ledger temp file(s), %.1f MB stranded on %s "
            "— sweep is REPORT-ONLY (set ARIA_LEDGER_TMP_SWEEP=1 to reclaim)",
            len(stale), result["would_free_bytes"] / 1048576.0,
            os.path.dirname(_DISK_PATH) or ".",
        )
        return result

    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "reason": "R-F4110 (C-143) orphaned mkstemp files from _write_to_disk_atomic",
        "min_age_s": _TMP_MIN_AGE_S,
        "removed": [
            {"name": os.path.basename(p), "bytes": b,
             "mtime": datetime.fromtimestamp(m, timezone.utc).isoformat()}
            for p, b, m in stale
        ],
    }
    mpath = os.path.join(
        os.path.dirname(_DISK_PATH) or ".",
        f"aria_signals_tmp_reclaim_{int(now)}.manifest.json",
    )
    try:
        # Manifest FIRST — a removal we cannot describe is not an archive.
        with open(mpath, "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=1)
        result["manifest"] = mpath
    except OSError as e:
        logger.warning("[R-F4110] manifest write failed (%s) — refusing to sweep", e)
        return result

    for p, b, _m in stale:
        try:
            os.unlink(p)
            result["removed"] += 1
            result["freed_bytes"] += b
        except OSError:
            continue
    logger.info(
        "[R-F4110] reclaimed %d orphaned ledger temp file(s), %.1f MB (manifest: %s)",
        result["removed"], result["freed_bytes"] / 1048576.0, mpath,
    )
    return result


def _device_of(path) -> int | None:
    """`st_dev` of the nearest existing directory for `path`, else None.

    Separate and patchable so the off-host decision can be tested
    deterministically — device ids are platform-specific and a temp dir is
    always on the caller's own volume. Compared by device IDENTITY, not path
    strings, so a symlink or bind mount cannot masquerade as a second domain.
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
    """Is the R-F334 snapshot target a DIFFERENT failure domain to the ledger?

    R-F4109 (C-142) — the port of C-98 into the module C-98 did not cover.

    `SNAPSHOT_INTERVAL_S` still calls this "the Redis off-host backup cadence",
    and when R-F334 wrote it that was true. R-F745 flipped the default backend
    to sqlite and Upstash was cancelled (§6/§18); nothing revisited this module.
    Measured live 2026-08-17: 8.18 MB of gzipped ledger written every 600 s into
    `/data/aria_state.db` — the SAME volume as the `/data/aria_signals.json` it
    backs up, on a 630 MB state DB that is timing out reads (C-140). A copy
    sharing a failure domain with its original is not a backup.

    Returns True (genuinely elsewhere), False (same volume), or **None = COULD
    NOT MEASURE**.

    The tri-state is load-bearing and its safety default is the OPPOSITE of a
    write's: an unmeasurable target must keep BACKING UP. "I don't know" is a
    reason to keep a copy, never to silently stop making one.
    """
    try:
        from . import redis_store as _rs
        backend = str(getattr(_rs, "_BACKEND", "") or "").strip().lower()
        if backend and backend != "sqlite":
            # A remote store (upstash/redis) IS a real second failure domain.
            # Keep this branch: re-pointing the state store off-host must
            # resume the backup with no code change.
            return True
        from . import state_store as _ss
        db = getattr(_ss, "_DB_PATH", None)
        if not db:
            db = os.getenv("ARIA_STATE_DB_PATH", "/data/aria_state.db")
        dev_state = _device_of(db)
        dev_ledger = _device_of(_DISK_PATH)
        if dev_state is None or dev_ledger is None:
            return None
        return dev_state != dev_ledger
    except Exception:
        return None


def _should_snapshot(offhost: bool | None) -> bool:
    """Run the snapshot unless we KNOW it is not a backup.

    Only a measured False skips. None (unknown) runs — see
    `_snapshot_target_is_offhost`.
    """
    return offhost is not False


async def _flush_to_disk() -> None:
    """Serialize the cache and write to disk in a thread executor (json.dump
    is sync C; doing it on the event loop blocks every other coroutine for
    the duration of the dump)."""
    global _dirty, _dirty_since_snapshot
    if not _cache or not _dirty:
        return
    snapshot = _cache  # write-by-reference is safe — we don't mutate
    # ── R-F4108 (C-141) — append the declared records instead of rewriting
    # 35 MB. Compaction (a full snapshot) happens when the change could NOT be
    # described as appends, or when replaying the journal would cost more than
    # a snapshot. Both paths clear the journal so a stale one can never replay
    # over a compacted file.
    _needs_compaction = _force_full_rewrite[0] or not _journal_pending
    # A JOURNAL WITHOUT ITS BASE IS NOT RECOVERABLE. `_load` replays the
    # journal *over* the snapshot, so if no snapshot exists yet the appended
    # records would be orphaned — which is data loss, and it is exactly what
    # `test_disk_round_trip_survives_cache_reset` caught. The first write to a
    # fresh ledger must therefore create the snapshot.
    if not _needs_compaction and not os.path.exists(_DISK_PATH):
        _needs_compaction = True
    if not _needs_compaction:
        try:
            if os.path.getsize(_journal_path()) >= JOURNAL_MAX_BYTES:
                _needs_compaction = True
        except OSError:
            pass
    if not _needs_compaction:
        from ._snapshot_throttle import run_in_thread_throttled
        _records, _journal_pending[:] = list(_journal_pending), []
        try:
            await run_in_thread_throttled(_append_journal, _records)
            _dirty = False
            _dirty_since_snapshot = True
            return
        except Exception as e:
            # Could not journal → fall through to the honest full rewrite.
            # Put the records back so the snapshot below still contains them
            # (they are already in `_cache`, so this is belt-and-braces).
            _journal_pending[:0] = _records
            logger.warning("intel_ledger: journal append failed (%s) — "
                           "falling back to a full snapshot", e)
    try:
        # R-F787 — throttle against knowledge + neural_memory encoders
        # so concurrent flushes don't pile up GIL holders and stall
        # the loop. One-shot boot migrations below stay un-throttled.
        from ._snapshot_throttle import run_in_thread_throttled
        await run_in_thread_throttled(_write_to_disk_atomic, snapshot)
        _dirty = False
        _dirty_since_snapshot = True
        # R-F4108 (C-141) — the snapshot now CONTAINS everything the journal
        # described, so the journal must go. A stale journal replayed over a
        # compacted file resurrects rows a purge just removed.
        _journal_pending.clear()
        _force_full_rewrite[0] = False
        _drop_journal()
        # F87 observability (preserved from pre-F110 _save): log signal
        # count at flush time on 250-signal increments so the operator can
        # spot trajectory drops between two boots from the disk-flush log.
        try:
            n = len(_cache.get("signals", []) or [])
            if n and n % 250 == 0:
                logger.info("intel_ledger flush checkpoint: %d signals", n)
        except Exception:
            pass
    except Exception as e:
        logger.error("intel_ledger: disk flush failed: %s", e)


async def _flush_loop() -> None:
    """Background coroutine: every FLUSH_DEBOUNCE_S, flush dirty cache to
    disk; every SNAPSHOT_INTERVAL_S, also push a Redis snapshot."""
    global _dirty_since_snapshot
    last_snapshot = time.monotonic()
    # R-F4110 (C-143) §21a — ONCE per process, say how much disk is stranded in
    # our own orphaned temp files. 382.6 MB had accumulated over ~2.5 months
    # with no cleanup routine anywhere in the tree and nothing reporting it.
    # Report-only unless ARIA_LEDGER_TMP_SWEEP=1 (§26: the reclaim is the
    # operator's call). Off the request path, and it must never stop the loop.
    try:
        _orphans = await asyncio.to_thread(sweep_tmp_orphans)
        if _orphans.get("would_remove") and not _orphans.get("removed"):
            from .engine_wiring import wire_failure as _wf4099
            _wf4099(
                module="intel_ledger",
                detail=(f"{_orphans['would_remove']} orphaned ledger temp file(s) "
                        f"stranding {_orphans['would_free_bytes'] / 1048576.0:.1f} MB "
                        f"on the data volume. Created by _write_to_disk_atomic when a "
                        f"process is killed mid-write; nothing reclaims them. Set "
                        f"ARIA_LEDGER_TMP_SWEEP=1 to reclaim (manifest is written first)."),
                gap_type="infra_degraded",
                source="intel_ledger:sweep_tmp_orphans:R-F4110",
            )
    except Exception:      # pragma: no cover — housekeeping never breaks the loop
        pass
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
                # R-F4109 (C-142) — only skip on a MEASURED same-volume target.
                _offhost = _snapshot_target_is_offhost()
                if not _should_snapshot(_offhost):
                    global _snapshot_skip_announced
                    if not _snapshot_skip_announced:
                        _snapshot_skip_announced = True
                        # Announced ONCE per process: at a 600 s cadence a
                        # per-cycle notice would emit ~144/day — the
                        # sanctions_coverage_degraded flood shape.
                        logger.info(
                            "[R-F4109] intel_ledger snapshot SKIPPED — the "
                            "state store shares a volume with %s, so the "
                            "'off-host backup' is a same-domain copy. Point "
                            "the state store off-host to resume it.",
                            _DISK_PATH,
                        )
                    last_snapshot = now      # keep the cadence, skip the work
                    _dirty_since_snapshot = False
                    continue
                try:
                    # R-F714 (2026-05-19): _encode_snapshot does
                    # json.dumps + gzip.compress on the full ledger
                    # (~36k signals → 2MB gzip); running this on the
                    # loop wedged the server for 5-20s. Move to a
                    # worker thread.
                    payload = await asyncio.to_thread(_encode_snapshot, _cache)
                    await rs.set(KEY, payload)
                    _dirty_since_snapshot = False
                    last_snapshot = now
                    logger.info(
                        "intel_ledger: state snapshot written (%d signals, %d bytes gzip)",
                        len(_cache.get("signals", [])), len(payload),
                    )
                except Exception as e:
                    logger.warning("intel_ledger: state snapshot failed: %s", e)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning("intel_ledger: flush loop error: %s", e)


def _ensure_flusher() -> None:
    """Start the debounced flusher if a running loop exists. No-op in sync
    test contexts (no loop) — those should call flush() explicitly if they
    need persistence."""

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


# ── Entity extraction lists ──────────────────────────────────────────────────

COUNTRIES = [
    "Angola", "Mozambique", "Guinea-Bissau", "Cape Verde", "São Tomé", "Nigeria",
    "Kenya", "Ghana", "Senegal", "Ivory Coast", "Cameroon", "Ethiopia", "Rwanda",
    "Uganda", "Tanzania", "Morocco", "Algeria", "Egypt", "Tunisia", "Libya",
    "South Africa", "Namibia", "Botswana", "Zimbabwe", "Zambia", "DRC", "Congo",
    "Mali", "Burkina Faso", "Niger", "Chad", "Sudan", "South Sudan", "Somalia",
    "Djibouti", "Eritrea", "Madagascar", "Indonesia", "Philippines", "Vietnam",
    "Thailand", "Myanmar", "Malaysia", "Singapore", "India", "Pakistan",
    "Bangladesh", "Sri Lanka", "UAE", "Saudi Arabia", "Qatar", "Kuwait", "Oman",
    "Bahrain", "Iraq", "Jordan", "Lebanon", "Turkey", "Israel", "Iran",
    "Poland", "Romania", "Ukraine", "Brazil", "Colombia", "Mexico", "Peru", "Chile",
]

PRODUCTS = {
    "ammunition": ["ammunition", "ammo", "round", "mortar", "shell"],
    "vehicles": ["vehicle", "armoured", "apc", "ifv", "mrap", "tank"],
    "aircraft": ["aircraft", "fighter", "helicopter", "drone", "uav"],
    "naval": ["vessel", "frigate", "corvette", "submarine", "destroyer"],
    "missiles": ["missile", "rocket", "sam", "patriot", "javelin"],
    "radar": ["radar", "air defense", "shorad", "ewi"],
    "small_arms": ["rifle", "pistol", "machine gun", "carbine"],
    "surveillance": ["surveillance", "isr", "reconnaissance", "sigint"],
    "training": ["training", "exercise", "drill", "simulation"],
}

OEMS = [
    "Lockheed", "Boeing", "Raytheon", "BAE Systems", "Leonardo", "Rheinmetall",
    "Thales", "Turkish Aerospace", "Baykar", "Elbit", "Rafael", "IAI",
    "Paramount", "Denel", "Norinco", "AVIC", "Poly Technologies", "Embraer",
    "Otokar", "FNSS", "Aselsan", "Hanwha", "Hyundai Rotem", "KAI",
    "Damen", "Navantia", "Fincantieri", "MBDA", "Saab", "Kongsberg",
    "General Dynamics", "Northrop", "L3Harris",
]


# R-F2715 — a defence CONTEXT anchor. Product tags (which include common English
# words like "round"/"training"/"vessel"/"sam") are only applied when the article
# is genuinely defence-related, so a World Cup or film article can never fabricate
# a "missiles"/"ammunition" tag. Recall is preserved: real defence articles almost
# always carry one unambiguous military term or a matched OEM.
_DEFENCE_ANCHOR_RE = re.compile(
    r"\b(?:defen[cs]e|militar|\barmy\b|\bnavy\b|naval|air\s?force|artillery|infantry|"
    r"troops|combat|warfare|weapon|munition|missile|warship|frigate|corvette|"
    r"submarine|destroyer|howitzer|mrap|\bapc\b|\bifv\b|gunship|procurement|tender|"
    r"\bmod\b|ministry\s+of\s+defen[cs]e|arms\s+(?:deal|sale|export)|export\s+licen[cs]e|"
    r"\bnato\b|sanction|ordnance|calibre|caliber)\b",
    re.IGNORECASE,
)


def _kw_present(kw: str, tl: str) -> bool:
    """Word-boundary keyword match. `"sam"` no longer matches "same"/"sample",
    `"round"` no longer matches "around"/"ground", `"oman"` no longer matches
    "romania" — the substring-match class that fabricated defence tags."""
    return re.search(r"\b" + re.escape(kw.lower()) + r"\b", tl) is not None


def _extract_entities(text: str) -> dict:
    tl = (text or "").lower()
    countries = [c for c in COUNTRIES if _kw_present(c, tl)]
    oems = [o for o in OEMS if _kw_present(o, tl)]
    # Products require a defence context (a matched OEM or a military anchor term)
    # AND a word-boundary keyword hit — never a bare substring in unrelated prose.
    defence_context = bool(oems) or _DEFENCE_ANCHOR_RE.search(text or "") is not None
    products = (
        [cat for cat, kws in PRODUCTS.items() if any(_kw_present(k, tl) for k in kws)]
        if defence_context else []
    )
    return {"countries": countries, "products": products, "oems": oems}


async def _load() -> dict:
    """Hydrate the cache once. Order: disk → legacy Redis blob (one-shot
    migration) → empty default. Subsequent calls hit the in-memory cache
    without I/O. NOTE: 5 sibling modules (chain_correlator, competitor_tracker,
    narrative_monitor, signal_correlator, plus tests) consume this directly
    via `await intel_ledger._load()` — the leading underscore is advisory.
    Return shape `{"signals": [...], "version": int}` MUST be preserved."""
    global _cache
    if _cache is not None:
        return _cache

    # 1. Prefer disk — canonical store post-F110.
    data = _read_from_disk()
    if data:
        _cache = data
        # R-F4108 (C-141) — replay the journal over the snapshot. Entries are
        # prepended (newest first) because signals are head-inserted, so a tail
        # watermark would be wrong. Compaction drops the journal, so anything
        # still here post-dates the snapshot.
        _replayed = _read_journal()
        if _replayed:
            _cache["signals"] = _replayed + (_cache.get("signals") or [])
        logger.info(
            "intel_ledger: loaded %d signals from disk (%s)%s",
            len(_cache.get("signals", [])), _DISK_PATH,
            f" (+{len(_replayed)} replayed from journal)" if _replayed else "",
        )
        _prune()
        _ensure_flusher()
        return _cache

    # 2. Disk empty — try the Redis snapshot (gzip post-R-F36, or legacy
    #    raw-JSON pre-R-F36) and migrate it forward to disk.
    raw = await rs.get(KEY)
    legacy = _decode_snapshot(raw)
    if legacy and isinstance(legacy, dict) and "signals" in legacy:
        _cache = legacy
        logger.warning(
            "intel_ledger: hydrated from legacy Redis blob (%d signals) — "
            "migrating to disk %s",
            len(_cache.get("signals", [])), _DISK_PATH,
        )
        try:
            await asyncio.to_thread(_write_to_disk_atomic, _cache)
            logger.info("intel_ledger: legacy Redis → disk migration complete")
        except Exception as e:
            logger.error("intel_ledger: legacy migration to disk failed: %s", e)
        _prune()
        _ensure_flusher()
        return _cache

    # 3. Cold start with no prior state.
    # R-F4108 (C-141) — belt and braces: `_flush_to_disk` refuses to journal
    # without a snapshot, so reaching here with a journal present should be
    # impossible. If it happens anyway (a snapshot deleted underneath us), the
    # journalled signals are still real and §7 says we do not lose facts.
    _orphaned = _read_journal()
    _cache = {"signals": _orphaned, "version": 1}
    if _orphaned:
        logger.warning(
            "intel_ledger: no snapshot on disk but the journal held %d signal(s) "
            "— recovered them rather than starting empty (R-F4108)",
            len(_orphaned),
        )
    _prune()
    _ensure_flusher()
    return _cache


def _prune() -> None:
    # R-F239 (2026-05-11) — warn-only path. Pre-R-F239 this function
    # truncated _cache["signals"] at MAX_SIGNALS (1M cap), dropping the
    # oldest entries. That violates the infinite-memory rule. The
    # RETENTION_DAYS-based age cutoff is preserved BUT set to 100 years
    # (effectively permanent — line 50), so no signal is ever actually
    # aged out under realistic timescales. Only the size-based truncation
    # is gone. If the warn threshold trips, operator gets a log line
    # prompting cold-storage offload.
    if not _cache:
        return
    cutoff = (datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)).isoformat()
    _before = len(_cache["signals"])
    _cache["signals"] = [s for s in _cache["signals"] if s.get("ts", "") >= cutoff]
    sig_count = len(_cache["signals"])
    if sig_count != _before:
        # R-F4108 (C-141) — a REMOVAL cannot be expressed as a journal append,
        # and replaying appends over it would resurrect what was dropped. Force
        # the next flush to compact. `add_signal` calls `_prune()` immediately
        # before `_save(record=...)`, so this is what keeps that path honest.
        _journal_pending.clear()
        _force_full_rewrite[0] = True
    if sig_count > WARN_SIGNALS:
        global _signal_warn_throttle
        _signal_warn_throttle += 1
        if _signal_warn_throttle % 100 == 1:
            logger.warning(
                "[intel_ledger] R-F239 — signal count %d > warn threshold %d. "
                "NOT truncating (infinite-memory rule). Plan cold-storage offload.",
                sig_count, WARN_SIGNALS,
            )


async def _save(record: "dict | list[dict] | None" = None) -> None:
    """Mark the cache dirty. Actual disk I/O is debounced through
    _flush_loop so sweep bursts (50+ signals/cycle) coalesce into a single
    write. Pre-F110 this did rs.set_json on every call — see module docstring.

    R-F4108 (C-141) — `record` DECLARES the one signal that changed, so the
    flush can APPEND it to the journal instead of rewriting the whole ledger.

    **Calling `_save()` with no `record` forces a FULL REWRITE.** That is the
    safety default and it is load-bearing twice over:

      1. A mutation site added later, which does not know about the journal,
         degrades to the old (correct, expensive) behaviour rather than
         silently losing data. "I was told nothing" must mean "write
         everything".
      2. The two STRUCTURAL sites — `_prune()` and `purge_by_keywords()` —
         already call bare `_save()`, so they compact for free. Replaying an
         append journal over a deletion would RESURRECT what was purged.

    Do not "optimise" the undeclared path into a journal append.
    """
    global _dirty
    if not _cache:
        return
    _dirty = True
    if record is None:
        # Undeclared change → the journal cannot describe it → compact.
        _journal_pending.clear()
        _force_full_rewrite[0] = True
    elif isinstance(record, list):
        # A sweep declares its whole batch at once.
        _journal_pending.extend(record)
    else:
        _journal_pending.append(record)
    _ensure_flusher()


@fail_wire(module="intel_ledger", gap_type="engine_failure")
async def flush() -> None:
    """Force an immediate disk flush. Call from shutdown hooks or tests
    that need to assert on-disk state without waiting for the debounce."""
    await _flush_to_disk()


@fail_wire(module="intel_ledger", gap_type="engine_failure")
async def shutdown() -> None:
    """Stop the background flusher and write any pending changes. Wired
    into main.py lifespan teardown next to knowledge.shutdown()."""
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
    await _flush_to_disk()


# ── Public API ───────────────────────────────────────────────────────────────

@fail_wire(module="intel_ledger", gap_type="engine_failure")
async def init() -> None:
    await _load()
    logger.info(f"Intel ledger loaded: {len((_cache or {}).get('signals', []))} signals")


@fail_wire(module="intel_ledger", gap_type="engine_failure")
async def add_signal(payload: dict) -> str:
    """Add a single signal to the ledger.

    Accepts a dict with at least 'summary' or 'title'. Used by the
    autonomous delivery pipeline to push brain_lead signals from
    completed tasks into the permanent ledger.
    """
    db = await _load()
    text = payload.get("summary") or payload.get("title") or ""
    if not text:
        return "skipped:empty"
    source = payload.get("source", "autonomous")
    if _is_propaganda_source(source):
        return "skipped:propaganda"
    ent = _extract_entities(text)
    now = datetime.now(timezone.utc).isoformat()
    # Clause 17 — attach source tier to every signal carrying a URL so the
    # ledger itself becomes provenance-aware. Tier classification is pure
    # (no network, no redis), safe to run inline.
    url = payload.get("url", "")
    source_tier = ""
    source_score = 0.0
    if url:
        try:
            from . import verified_intel as _vi
            _tier = _vi.SourceTierClassifier().classify(url)
            source_tier = _tier.value
            source_score = _vi.TIER_SCORES[_tier]
        except Exception:
            pass
    _new_signal = {
        "text": text[:500],
        "source": source,
        "type": payload.get("type", "brain_lead"),
        "url": url,
        "countries": ent["countries"],
        "products": ent["products"],
        "oems": ent["oems"],
        "severity": payload.get("severity", "medium"),
        "ts": payload.get("timestamp") or now,
        "tags": payload.get("tags", []),
        "source_tier": source_tier,
        "source_tier_score": source_score,
    }
    db["signals"].insert(0, _new_signal)
    # R-F4108 (C-141) — `_prune()` runs FIRST and forces a compaction if it
    # actually removed anything, so declaring the record here is safe: the
    # journal is only used when this insert is the whole of the change.
    _prune()
    await _save(record=_new_signal)

    # Signal brain about the new intel.
    # R-F456 (2026-05-13) — emit module="intel_ledger" so the topic
    # registration that R-F154 added to _MODULE_TOPICS actually fires.
    # Pre-R-F456 the absorb wrote module="conflict_tracker" or
    # "deep_researcher" so the intel_ledger row in _MODULE_TOPICS was
    # dead — System Health perpetually reported intel_ledger silent
    # despite live ingest. R-F154 added the topic entry; this fix
    # closes the loop. The original "conflict_tracker" / "deep_researcher"
    # routing is preserved as `extra_topics` so per-signal context isn't
    # lost.
    try:
        from . import brain_hook
        _signal_topic = (
            "conflict_tracker" if payload.get("type") == "osint"
            else "deep_researcher"
        )
        await brain_hook.absorb(
            module="intel_ledger",
            summary=f"Ledger signal: {text[:200]}",
            success=True,
            confidence="ASSESSED",
            extra_topics=[_signal_topic],
        )
    except Exception as _bh_e:
        logger.debug("R-F456 intel_ledger brain_hook absorb failed: %s", _bh_e)

    # R-F96 (2026-05-09): record domain freshness so R-F88 tracker
    # accumulates real state. Domain inferred from signal type / source
    # / payload tags. Best-effort; never blocks ingest.
    try:
        from . import learning_progress as _lp
        domain = _domain_for_signal(payload, source, ent)
        if domain:
            await _lp.record_refresh(
                domain,
                source=f"intel_ledger:{source}",
                signals_added=1,
            )
    except Exception:
        pass

    return "ok"


def _domain_for_signal(payload: dict, source: str, entities: dict) -> str | None:
    """R-F96: classify a ledger signal into a learning_progress domain.

    Maps the signal's source / type / tags / countries onto one of the
    known domain identifiers from learning_progress._MAX_STALENESS_OVERRIDES
    + the per-country defence_market:* prefix.
    """
    sig_type = (payload.get("type") or "").lower()
    src = (source or "").lower()
    tags = [t.lower() for t in (payload.get("tags") or [])]

    if "fcpa" in src or "enforcement" in tags:
        return "fcpa_enforcement"
    if "sanction" in src or "ofac" in tags or "ofsi" in tags or "sdn" in src:
        return "sanctions_screening"
    if "fatf" in src or "fatf" in tags:
        return "fatf_ml_typologies"
    if "tbml" in tags or sig_type == "tbml":
        return "fatf_tbml"
    if "crypto" in src or "wallet" in tags:
        return "virtual_assets"
    if sig_type == "cve" or "cyber" in src:
        return "cyber_threats"
    if sig_type == "tender" or "procurement" in src or "tender" in tags:
        # Country-specific procurement
        countries = entities.get("countries") or []
        if countries:
            return f"defence_market:{countries[0].lower()}"
        return "procurement_pipeline"
    # Country-anchored signals fall into per-market briefing
    countries = entities.get("countries") or []
    if countries:
        return f"defence_market:{countries[0].lower()}"
    return None


@fail_wire(module="intel_ledger", gap_type="engine_failure")
async def purge_signals_by_keyword(keywords: list[str], dry_run: bool = False) -> dict:
    """Remove signals from the ledger whose text contains ANY of the given
    keywords (case-insensitive). Designed for surgical cleanup of polluted
    signals — e.g. fabricated current-event claims that bled into multiple
    chat replies.

    Returns a summary dict with the number matched, removed, and a sample
    of the matched texts so callers can verify before committing.

    When dry_run=True, returns the same shape but does not actually delete.
    """
    db = await _load()
    if not keywords:
        return {"matched": 0, "removed": 0, "sample": [], "dry_run": dry_run}
    needles = [k.lower() for k in keywords if k]
    matched_signals = []
    surviving = []
    for s in db.get("signals", []):
        text = (s.get("text", "") or "").lower()
        source = (s.get("source", "") or "").lower()
        if any(n in text or n in source for n in needles):
            matched_signals.append(s)
        else:
            surviving.append(s)

    sample = [
        {"text": s.get("text", "")[:200], "source": s.get("source", ""), "ts": s.get("ts", "")}
        for s in matched_signals[:10]
    ]

    if not dry_run and matched_signals:
        db["signals"] = surviving
        await _save()
        logger.warning(
            "Ledger purge: removed %d signal(s) matching keywords=%s",
            len(matched_signals), keywords,
        )

    return {
        "matched": len(matched_signals),
        "removed": 0 if dry_run else len(matched_signals),
        "remaining": len(surviving) if not dry_run else len(db.get("signals", [])),
        "sample": sample,
        "dry_run": dry_run,
        "keywords": keywords,
    }


# Propaganda-tier sources — these channels are monitored for OSINT
# situational awareness via the sweep cycle but their content is NOT
# trustworthy enough to enter the chat-injection layer. Keeping them out
# of the intel ledger entirely is the cleanest defence: every downstream
# code path (query_ledger, _build_intel_context, the LLM prompt) becomes
# safe by construction. The list mirrors apis/sources/telegram.mjs
# DEFAULT_CHANNELS — when new biased channels are added there, this set
# must be updated in lockstep.
#
# Past incident 2026-04-09: a single intelslava "Lebanon airstrikes 112
# killed" post propagated into the Vision International ammunition RFQ
# analysis, the Modirum Gespi investigation, AND the Ghana defence
# minister query, with [CONFIRMED] tags. Even after constitution clause
# 13 + the relevance filter + a manual purge, the sweep cycle kept
# re-ingesting fresh propaganda content every cycle and the bleed
# returned. The only structural fix is to block these sources at the
# ledger boundary.
_PROPAGANDA_SOURCES = {
    # Russian state / Russian-aligned
    "intelslava", "mod_russia", "rvvoenkor", "readovkanews", "readovka",
    # Conflict Intelligence Team is sometimes Russian-aligned content
    "cig_telegram",
    # Ukrainian state / Ukrainian-aligned (also single-perspective)
    "deepstateua", "operativnozsu", "generalstaffzsu", "legitimniy",
    "ukraine frontline",
    # Generic single-channel buckets that proved high-noise in 2026-04-09
    "telegram", "tg",
}


def _is_propaganda_source(source: str) -> bool:
    """Return True if this source identifier matches the propaganda set.
    Case-insensitive substring match — catches both 'intelslava' as a
    full source string and 'telegram:intelslava' as a prefixed one."""
    if not source:
        return False
    s = source.lower().strip()
    if s in _PROPAGANDA_SOURCES:
        return True
    return any(p in s for p in _PROPAGANDA_SOURCES if len(p) > 3)


@fail_wire(module="intel_ledger", gap_type="engine_failure")
async def ingest_sweep_signals(current_data: dict) -> int:
    """Parse sweep data, extract entities, dedup, store. Returns count added.

    Propaganda-tier sources (intelslava, mod_russia, CIG_telegram, etc.)
    are SKIPPED at ingest time — they never enter the ledger and therefore
    can never be auto-injected into chat replies. This is the structural
    fix for the 2026-04-09 Lebanon contamination incident; clause 13 and
    the relevance filter handle the same content if it slips through via
    other paths, but the ledger boundary is the cleanest place to block.
    """
    db = await _load()
    existing = {s.get("text", "")[:150].lower() for s in db["signals"]}
    added = 0
    skipped_propaganda = 0
    now = datetime.now(timezone.utc).isoformat()
    _new_records: list[dict] = []      # R-F4108 (C-141)

    def _add(text: str, source: str, sig_type: str, url: str = "", severity: str = "medium"):
        nonlocal added, skipped_propaganda
        if not text:
            return
        if _is_propaganda_source(source):
            skipped_propaganda += 1
            return
        if text[:150].lower() in existing:
            return
        ent = _extract_entities(text)
        _rec = {
            "text": text[:500], "source": source, "type": sig_type, "url": url,
            "countries": ent["countries"], "products": ent["products"], "oems": ent["oems"],
            "severity": severity, "ts": now,
        }
        db["signals"].insert(0, _rec)
        _new_records.append(_rec)     # R-F4108 (C-141) — declare the batch
        existing.add(text[:150].lower())
        added += 1

    # OSINT urgent
    for s in (current_data.get("tg", {}).get("urgent") or []):
        _add(s.get("text", ""), s.get("channel", "OSINT"), "osint")

    # Correlations
    for c in (current_data.get("correlations") or []):
        for sig in (c.get("topSignals") or [])[:2]:
            _add(sig.get("text", ""), c.get("region", ""), "correlation", severity=c.get("severity", "medium"))

    # Defence news (may be list of dicts or list of strings)
    for d in (current_data.get("defenseNews") or []):
        if isinstance(d, str):
            _add(d, "defence_news", "defense_news")
        elif isinstance(d, dict):
            _add(d.get("title", ""), d.get("source", "defence_news"), "defense_news", d.get("link", ""))

    # Tenders
    items = (current_data.get("procurementTenders") or {}).get("items") or []
    for t in items:
        if isinstance(t, str):
            _add(t, "tender", "tender")
        elif isinstance(t, dict):
            _add(t.get("title") or t.get("text", ""), t.get("source", "tender"), "tender", t.get("link", ""))

    # BD brain leads
    brain = (current_data.get("bdIntelligence") or {}).get("brain") or {}
    for l in (brain.get("salesLeads") or []):
        _add(f"{l.get('market','')}: {l.get('lead','')}", "brain", "brain_lead")

    # R-F4108 (C-141) — declare the batch so a 50-signal sweep appends ~50
    # small records instead of rewriting the whole 35 MB ledger. `_prune()`
    # runs first and forces a compaction if it removed anything.
    _prune()
    await _save(record=_new_records or None)
    if skipped_propaganda > 0:
        logger.info(
            "Ledger ingested %d new signals (%d propaganda-tier signals skipped at boundary)",
            added, skipped_propaganda,
        )
    else:
        logger.info("Ledger ingested %d new signals", added)
    return added


def _recency_multiplier(ts_iso: str, now: datetime | None = None) -> float:
    """Score multiplier based on signal age — fresh signals dominate stale ones.

    today / <2d  → 2.5x
    2-7 days     → 1.5x
    7-14 days    → 1.0x   (baseline)
    14-21 days   → 0.7x
    >21 days     → 0.4x

    Without this weighting, a 28-day-old correlation outranks today's tender simply
    because it has more keyword matches — disastrous for procurement timing.
    """
    if not ts_iso:
        return 0.4
    try:
        dt = datetime.fromisoformat(ts_iso.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return 0.4
    now = now or datetime.now(timezone.utc)
    age_days = (now - dt).total_seconds() / 86400
    if age_days < 2: return 2.5
    if age_days < 7: return 1.5
    if age_days < 14: return 1.0
    if age_days < 21: return 0.7
    return 0.4


@fail_wire(module="intel_ledger", gap_type="engine_failure")
def query_ledger(query: str) -> str:
    """Time-weighted, entity-aware search for prompt injection.

    Returns a formatted string for LLM context. Recent signals score 2.5x
    higher than 3-week-old ones — restoring the "what's hot now" intuition
    that a senior analyst would apply.
    """
    if not _cache or not _cache["signals"]:
        return ""
    words = [w.lower() for w in query.split() if len(w) > 2]
    if not words:
        return ""

    now = datetime.now(timezone.utc)
    query_lower = query.lower()

    scored: list[tuple[float, dict]] = []
    for s in _cache["signals"]:
        score = 0.0
        for c in s.get("countries", []):
            if c.lower() in query_lower:
                score += 5
        for o in s.get("oems", []):
            if o.lower() in query_lower:
                score += 4
        for p in s.get("products", []):
            if p.lower() in query_lower:
                score += 4
        text = s.get("text", "").lower()
        for w in words:
            if w in text:
                score += 2

        # Severity boost — high-severity signals matter more even if older
        sev = (s.get("severity") or "medium").lower()
        if sev == "high":   score += 2
        elif sev == "low":  score -= 1

        if score > 0:
            # Apply temporal weighting AFTER content scoring
            score *= _recency_multiplier(s.get("ts", ""), now)
            scored.append((score, s))

    scored.sort(key=lambda x: x[0], reverse=True)
    top = scored[:12]
    if not top:
        return ""

    lines = [f"\n[INTELLIGENCE LEDGER — recent signals ({len(_cache['signals'])} total, permanent, recency-weighted)]"]
    for score, s in top:
        age = ""
        if s.get("ts"):
            try:
                dt = datetime.fromisoformat(s["ts"].replace("Z", "+00:00"))
                days = (now - dt).days
                hrs = (now - dt).total_seconds() / 3600
                if hrs < 24: age = f" ({int(hrs)}h ago)"
                else: age = f" ({days}d ago)"
            except Exception:
                pass
        lines.append(f"- [{s.get('type','?')}] {s.get('text','')[:180]}{age}")
    return "\n".join(lines)


@fail_wire(module="intel_ledger", gap_type="engine_failure")
async def get_country_situation(country: str) -> dict:
    db = await _load()
    signals = [s for s in db["signals"] if country.lower() in [c.lower() for c in s.get("countries", [])]]
    return {
        "country": country,
        "signalCount": len(signals),
        "recentSignals": signals[:20],
    }


@fail_wire(module="intel_ledger", gap_type="engine_failure")
async def get_stats() -> dict:
    db = await _load()
    by_type: dict[str, int] = {}
    by_country: dict[str, int] = {}
    for s in db["signals"]:
        t = s.get("type", "unknown")
        by_type[t] = by_type.get(t, 0) + 1
        for c in s.get("countries", []):
            by_country[c] = by_country.get(c, 0) + 1
    return {
        "totalSignals": len(db["signals"]),
        "byType": by_type,
        "byCountry": dict(sorted(by_country.items(), key=lambda x: x[1], reverse=True)[:15]),
    }


@fail_wire(module="intel_ledger", gap_type="engine_failure")
async def get_recent(limit: int = 1000) -> list[dict]:
    """Return the most-recent N signals as a list — newest first.

    R-F134 (2026-05-10): added because counter_intelligence.scan_entity
    expected this exact helper name and returned INDETERMINATE on every
    explorer.html sweep when the helper was absent. Live evidence on
    /explorer.html for "Assan Group Turkey":
        "INDETERMINATE — could not access intel_ledger signal stream.
         Module needs intel_ledger to expose a get_recent() helper..."
    Now returns the in-memory signals list ordered by ts (descending),
    no I/O. Aliases all_signals() / recent_signals() for cross-module
    expectations."""
    db = await _load()
    sigs = list(db.get("signals") or [])
    sigs.sort(
        key=lambda s: s.get("ts") or s.get("timestamp") or "",
        reverse=True,
    )
    return sigs[: max(1, int(limit or 1000))]


@fail_wire(module="intel_ledger", gap_type="engine_failure")
async def all_signals() -> list[dict]:
    """Alias for get_recent(limit=10000) — kept because callers in
    coverage_heatmap and counter_intelligence probe both names."""
    return await get_recent(limit=10000)


@fail_wire(module="intel_ledger", gap_type="engine_failure")
async def recent_signals(limit: int = 1000) -> list[dict]:
    """Alias for get_recent — same defensive reason as all_signals."""
    return await get_recent(limit=limit)

# R-F2538: R-F2119 import-time wire_failure("module shutdown") block removed — it fired a FALSE engine_failure gap on every import (not at shutdown); do not re-add.
